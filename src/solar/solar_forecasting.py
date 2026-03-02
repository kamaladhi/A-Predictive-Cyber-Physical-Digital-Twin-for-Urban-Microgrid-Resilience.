"""
Solar Irradiance Forecasting Module — v2
=========================================

CNN-BiLSTM with Temporal Attention + Quantile Regression for multi-horizon
GHI forecasting in a Digital Twin EMS.

v2 Changes vs v1
----------------
BUG FIXES
  1. Target off-by-one: ghi_scaled[i + h - 1] → ghi_scaled[i + h]
     The v1 formula for h=1 resolved to the current timestep (look-ahead leak),
     explaining the anomalous 6h RMSE < 1h RMSE pattern.
  2. Scaler fit on full dataset: now fit ONLY on train split.
  3. Epoch-32 early stopping: ReduceLROnPlateau replaced with OneCycleLR.
  4. MAPE nighttime inflation: evaluation threshold changed from GHI > 10
     to solar elevation > 3° (GHI ≈ 50 W/m²), eliminating twilight bias.

ARCHITECTURE
  ResLSTM (256 hidden, 3 layers) → CNN-BiLSTM + Temporal Attention
  - CNN front-end: extracts cloud-transient motifs (kernel=3, 2 pooling layers)
  - Bidirectional LSTM: captures both trend and reversal within the lookback
  - Horizon-specific attention: separate learned query per forecast horizon
  - Quantile heads: [q10, q50, q90] per horizon — replaces MC-Dropout

FEATURES
  20 → 34 features (see solar_preprocessing.ALL_FEATURES for full list)
  Key additions: cos_zenith, kt_smooth, GHI/kt lag features (1/2/3/6/12/24h),
  rolling std, cloud_var (variability proxy), is_monsoon binary

LOSS
  Weighted multi-horizon pinball loss (replaces MSE):
    L = Σ_h Σ_q  w_h × w_q × pinball(pred_{h,q}, target_h)
  Horizon weights: 1h=4.0, 6h=2.0, 24h=1.0 (EMS value-aligned)
  Additional: nighttime mask, ramp-event penalty (1.5×), irradiance boost (2×)

TRAINING
  AdamW (weight_decay=1e-4) + OneCycleLR(max_lr=1e-3, pct_start=0.10)
  3-way split: 2018 train / 2019 val / 2020 test
  Daytime-only window slicing (elevation > 3°)

INFERENCE
  SolarForecaster.predict(timestamp) → dict with ghi/pv q10/q50/q90 per horizon
  plus reserve_kw, dispatch_kw, risk_index for direct EMS integration

Architecture:
  Input [B, 48, 34]
  → Conv1D(64,k=3)→BN→GELU→Pool(2) → Conv1D(128,k=3)→BN→GELU→Pool(2)
  → BiLSTM(256, 2 layers)
  → TemporalAttention(heads=4, horizon-specific queries)
  → FC heads × 3 horizons → [B, 3, 3]  (horizons × quantiles)
"""

import os
import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader

try:
    from .physics_utils import (get_solar_position, get_clearsky_ghi,
                                 calculate_clearness_index)
    from .solar_preprocessing import (
        add_research_features, get_daytime_mask, temporal_split,
        RobustFeatureScaler, ALL_FEATURES, FEATURE_COLUMNS, NUM_FEATURES)
    from .pv_power_model import GHITargetScaler, KtTargetScaler
except ImportError:
    from physics_utils import (get_solar_position, get_clearsky_ghi,
                               calculate_clearness_index)
    from solar_preprocessing import (
        add_research_features, get_daytime_mask, temporal_split,
        RobustFeatureScaler, ALL_FEATURES, FEATURE_COLUMNS, NUM_FEATURES)
    from pv_power_model import GHITargetScaler, KtTargetScaler

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

LOOKBACK_HOURS    = 48
HORIZONS          = [1, 6, 24]
QUANTILES         = [0.10, 0.50, 0.90]
HORIZON_WEIGHTS   = {1: 4.0, 6: 2.0, 24: 1.0}
RANDOM_SEED       = 42

# CNN-BiLSTM hyper-parameters (v3 — smaller model to match data size)
CNN_CHANNELS  = [32, 64]
CNN_KERNEL    = 3
CNN_POOL      = 2
LSTM_HIDDEN   = 128
LSTM_LAYERS   = 1
ATTN_HEADS    = 2
FC_HIDDEN     = 64
DROPOUT       = 0.30


# =============================================================================
# Model Components
# =============================================================================

class ConvFeatureExtractor(nn.Module):
    """
    1-D temporal CNN: extracts local motifs (ramp events, cloud transients).
    Each block: Conv1d → BatchNorm → GELU → Dropout → MaxPool
    Reduces sequence length by pool^n_blocks for LSTM efficiency.
    """
    def __init__(self, in_channels: int,
                 channels: List[int] = CNN_CHANNELS,
                 kernel_size: int = CNN_KERNEL,
                 pool_size: int = CNN_POOL,
                 dropout: float = 0.1):
        super().__init__()
        layers = []
        ch = in_channels
        for ch_out in channels:
            layers += [
                nn.Conv1d(ch, ch_out, kernel_size, padding=kernel_size // 2),
                nn.BatchNorm1d(ch_out),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.MaxPool1d(pool_size),
            ]
            ch = ch_out
        self.net          = nn.Sequential(*layers)
        self.out_channels = ch

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [B, T, F] → permute to [B, F, T] for Conv1d
        return self.net(x.permute(0, 2, 1)).permute(0, 2, 1)  # back to [B, T', C]


class TemporalAttention(nn.Module):
    """
    Multi-head temporal attention with horizon-specific learned queries.

    Each forecast horizon gets its own query vector, allowing the model to
    attend to different historical time-steps for 1h vs 6h vs 24h forecasts.
    (e.g. 24h attends to same-hour yesterday; 1h attends to last 3 hours)
    """
    def __init__(self, hidden_dim: int, num_heads: int = 4,
                 num_horizons: int = 3, dropout: float = 0.1):
        super().__init__()
        assert hidden_dim % num_heads == 0
        self.num_heads    = num_heads
        self.num_horizons = num_horizons
        self.head_dim     = hidden_dim // num_heads

        self.queries    = nn.Parameter(
            torch.randn(num_horizons, num_heads, self.head_dim) * 0.02)
        self.key_proj   = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.value_proj = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.out_proj   = nn.Linear(hidden_dim, hidden_dim)
        self.dropout    = nn.Dropout(dropout)

    def forward(self, lstm_out: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Args:  lstm_out [B, T, H]
        Returns: context [B, num_horizons, H], weights [B, num_horizons, heads, T]
        """
        B, T, H = lstm_out.shape
        K = self.key_proj(lstm_out).view(B, T, self.num_heads, self.head_dim)
        V = self.value_proj(lstm_out).view(B, T, self.num_heads, self.head_dim)
        Q = self.queries.unsqueeze(0).expand(B, -1, -1, -1)  # [B, NH, heads, hd]

        scores  = torch.einsum('bnhd,bthd->bnht', Q, K) / math.sqrt(self.head_dim)
        weights = F.softmax(scores, dim=-1)
        weights = self.dropout(weights)

        context = torch.einsum('bnht,bthd->bnhd', weights, V)
        context = self.out_proj(context.reshape(B, self.num_horizons, H))
        return context, weights


class SolarForecastModel(nn.Module):
    """
    CNN-BiLSTM + Temporal Attention + Quantile Regression heads.

    Forward returns: (preds [B, H, Q], attn_weights)
    where H = num_horizons, Q = num_quantiles.
    """
    def __init__(self,
                 input_size:   int   = NUM_FEATURES,
                 horizons:     List  = HORIZONS,
                 quantiles:    List  = QUANTILES,
                 cnn_channels: List  = CNN_CHANNELS,
                 cnn_kernel:   int   = CNN_KERNEL,
                 cnn_pool:     int   = CNN_POOL,
                 lstm_hidden:  int   = LSTM_HIDDEN,
                 lstm_layers:  int   = LSTM_LAYERS,
                 attn_heads:   int   = ATTN_HEADS,
                 fc_hidden:    int   = FC_HIDDEN,
                 dropout:      float = DROPOUT):
        super().__init__()
        self.horizons  = horizons
        self.quantiles = quantiles
        H = len(horizons)
        Q = len(quantiles)

        self.cnn = ConvFeatureExtractor(
            input_size, cnn_channels, cnn_kernel, cnn_pool, dropout * 0.4)
        cnn_dim = self.cnn.out_channels

        self.pre_norm = nn.LayerNorm(cnn_dim)
        self.lstm     = nn.LSTM(
            cnn_dim, lstm_hidden, lstm_layers,
            dropout=dropout if lstm_layers > 1 else 0.0,
            batch_first=True, bidirectional=True)
        lstm_dim = lstm_hidden * 2

        self.attention = TemporalAttention(lstm_dim, attn_heads, H, dropout * 0.4)

        self.heads = nn.ModuleList([
            nn.Sequential(
                nn.Linear(lstm_dim, fc_hidden),
                nn.LayerNorm(fc_hidden),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(fc_hidden, Q),
            ) for _ in range(H)
        ])

        self._init_weights()
        logger.info("SolarForecastModel: %d params | horizons=%s | quantiles=%s",
                    sum(p.numel() for p in self.parameters()), horizons, quantiles)

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Conv1d):
                nn.init.kaiming_normal_(m.weight, nonlinearity='relu')

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        cnn_out         = self.pre_norm(self.cnn(x))
        lstm_out, _     = self.lstm(cnn_out)
        context, attn_w = self.attention(lstm_out)
        preds = torch.stack(
            [head(context[:, i, :]) for i, head in enumerate(self.heads)], dim=1)
        return preds, attn_w   # [B, H, Q], [B, H, heads, T']

    def predict_median(self, x: torch.Tensor) -> torch.Tensor:
        """Return only the q50 predictions [B, H]."""
        preds, _ = self.forward(x)
        return preds[:, :, self.quantiles.index(0.50)]


# =============================================================================
# Loss Function
# =============================================================================

class WeightedHorizonQuantileLoss(nn.Module):
    """
    EMS-aware composite pinball loss.

    L = Σ_h Σ_q  w_h · w_q · max(τ(y-p), (τ-1)(y-p))

    Additional weighting:
      - Nighttime mask: zero gradient when elevation ≤ 0°
      - Ramp penalty (1.5×): when |ΔG| > 150 W/m² (monsoon transients)
      - Irradiance boost (2×): when target GHI in upper half of range

    Why not MSE?
      MSE targets the conditional mean; asymmetric EMS dispatch costs
      (curtailment ≠ shortage) require the conditional median (τ=0.5) or
      conservative lower quantile (τ=0.1) as the point forecast.
    """
    def __init__(self,
                 horizons:         List[int]   = HORIZONS,
                 quantiles:        List[float] = QUANTILES,
                 horizon_weights:  Dict        = HORIZON_WEIGHTS,
                 quantile_weights: List[float] = None,
                 ramp_penalty:     float = 1.5,
                 daytime_boost:    float = 2.0):
        super().__init__()
        self.horizons         = horizons
        self.quantiles        = quantiles
        self.hw               = horizon_weights
        self.qw               = quantile_weights or [0.8, 1.0, 0.8]
        self.ramp_penalty     = ramp_penalty
        self.daytime_boost    = daytime_boost

    def forward(self, preds: torch.Tensor, targets: torch.Tensor,
                elevation: torch.Tensor,
                ghi_prev: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        preds:     [B, H, Q]
        targets:   [B, H]  (normalised GHI)
        elevation: [B]
        ghi_prev:  [B]  (normalised GHI at t-1, for ramp detection)
        """
        B = preds.shape[0]
        daytime = (elevation > 0).float().view(B, 1)

        if ghi_prev is not None:
            ramp_mag = (targets[:, 0] - ghi_prev).abs()
            ramp_w   = 1.0 + (self.ramp_penalty - 1.0) * (ramp_mag > 0.15).float()
        else:
            ramp_w = torch.ones(B, device=preds.device)

        high_irr = (targets[:, 0] > 0.5).float()
        irr_w    = 1.0 + (self.daytime_boost - 1.0) * high_irr
        sample_w = daytime[:, 0] * ramp_w * irr_w  # [B]

        total, count = torch.tensor(0.0, device=preds.device), 0
        for h_idx, h in enumerate(self.horizons):
            t_h = targets[:, h_idx]
            for q_idx, tau in enumerate(self.quantiles):
                err    = t_h - preds[:, h_idx, q_idx]
                pb     = torch.max(tau * err, (tau - 1.0) * err)
                w_sum  = sample_w.sum() + 1e-6
                total  = total + self.hw.get(h, 1.0) * self.qw[q_idx] * (pb * sample_w).sum() / w_sum
                count += 1
        return total / max(count, 1)


# =============================================================================
# Dataset
# =============================================================================

class SolarForecastDataset(Dataset):
    """
    Sliding-window dataset with daytime-only anchor points.

    Key fix vs v1:
      y[i] = [ghi_scaled[i+1], ghi_scaled[i+6], ghi_scaled[i+24]]
      NOT ghi_scaled[i+h-1] which for h=1 gave the current (non-future) value.

    Daytime-only:
      Windows are created only where solar elevation > 3°, eliminating the
      trivial zero-prediction task that dominated v1 training and inflated MAPE.

    Scaler strategy:
      feature_scaler and target_scaler must be pre-fit on train split only.
      Pass them in for val/test instantiation to prevent leakage.
    """

    def __init__(self, df: pd.DataFrame,
                 horizons:       List[int]  = HORIZONS,
                 lookback:       int        = LOOKBACK_HOURS,
                 feature_scaler: Optional[RobustFeatureScaler] = None,
                 target_scaler:  Optional[KtTargetScaler]      = None,
                 daytime_only:   bool       = True):

        df = add_research_features(df)

        max_horizon = max(horizons)
        feature_arr = df[FEATURE_COLUMNS].values.astype(np.float32)
        kt_arr      = df['kt'].values.astype(np.float32)
        ghi_cs_arr  = df['ghi_cs'].values.astype(np.float32)
        elev_arr    = df['elevation'].values.astype(np.float32)

        # Fit scalers on this split (train); reuse on val/test
        if feature_scaler is None:
            self.feature_scaler = RobustFeatureScaler().fit(
                feature_arr, feature_names=FEATURE_COLUMNS)
        else:
            self.feature_scaler = feature_scaler

        if target_scaler is None:
            self.target_scaler = KtTargetScaler().fit(kt_arr)
        else:
            self.target_scaler = target_scaler

        feat_scaled = self.feature_scaler.transform(feature_arr)
        kt_scaled   = self.target_scaler.transform(kt_arr)

        self.X, self.y = [], []
        self.elevations, self.ghi_prev = [], []
        self.ghi_cs_targets = []   # clear-sky GHI at target times for kt→GHI
        self.timestamps = []

        for i in range(lookback, len(df) - max_horizon):
            if daytime_only and elev_arr[i] <= 3.0:
                continue
            self.X.append(feat_scaled[i - lookback: i])
            self.y.append([kt_scaled[i + h] for h in horizons])
            self.elevations.append(elev_arr[i])
            self.ghi_prev.append(kt_scaled[i - 1])
            self.ghi_cs_targets.append([ghi_cs_arr[i + h] for h in horizons])
            self.timestamps.append(df.index[i])

        self.X              = np.array(self.X,              dtype=np.float32)
        self.y              = np.array(self.y,              dtype=np.float32)
        self.elevations     = np.array(self.elevations,     dtype=np.float32)
        self.ghi_prev       = np.array(self.ghi_prev,       dtype=np.float32)
        self.ghi_cs_targets = np.array(self.ghi_cs_targets, dtype=np.float32)

        logger.info("SolarForecastDataset: %d daytime windows | X%s y%s",
                    len(self), self.X.shape, self.y.shape)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return (torch.from_numpy(self.X[idx]),
                torch.from_numpy(self.y[idx]),
                torch.tensor(self.elevations[idx]),
                torch.tensor(self.ghi_prev[idx]))


# =============================================================================
# Training
# =============================================================================

def train_forecaster(
        df:          pd.DataFrame,
        train_years: List[int]   = None,
        val_years:   List[int]   = None,
        test_years:  List[int]   = None,
        val_months:  List[int]   = None,
        horizons:    List[int]   = HORIZONS,
        quantiles:   List[float] = QUANTILES,
        batch_size:  int         = 64,
        epochs:      int         = 150,
        lr:          float       = 1e-3,
        patience:    int         = 25,
        min_delta:   float       = 1e-5,
        save_dir:    str         = None,
        device:      str         = None,
) -> Tuple[SolarForecastModel, Dict, Dict]:
    """
    End-to-end training of CNN-BiLSTM solar forecaster.

    Returns (model, scalers_dict, metrics_dict).
    Checkpoint saved to save_dir/solar_forecaster_v2.pt

    Why OneCycleLR replaced ReduceLROnPlateau:
      v1 stopped at epoch 32 because ReduceLROnPlateau halved LR 3 times
      by epoch 30, leaving LR ≈ 6e-5 where Adam made negligible updates.
      OneCycleLR's cosine annealing provides smooth LR decay without the
      plateau-triggered halving that caused premature convergence.
    """
    if train_years is None: train_years = [2018]
    if val_years   is None: val_years   = [2019]
    if test_years  is None: test_years  = [2020]
    if save_dir is None:
        save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
    if device is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'

    torch.manual_seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)
    logger.info("Device: %s | Train: %s | Val: %s | Test: %s",
                device, train_years, val_years, test_years)

    train_df, val_df, test_df = temporal_split(
        df, train_years=train_years, val_years=val_years, test_years=test_years, val_months=val_months)

    train_ds = SolarForecastDataset(train_df, horizons, LOOKBACK_HOURS,
                                    daytime_only=True)
    val_ds   = SolarForecastDataset(val_df,   horizons, LOOKBACK_HOURS,
                                    feature_scaler=train_ds.feature_scaler,
                                    target_scaler=train_ds.target_scaler,
                                    daytime_only=True)
    test_ds  = SolarForecastDataset(test_df,  horizons, LOOKBACK_HOURS,
                                    feature_scaler=train_ds.feature_scaler,
                                    target_scaler=train_ds.target_scaler,
                                    daytime_only=True)

    logger.info("Dataset sizes — train: %d  val: %d  test: %d",
                len(train_ds), len(val_ds), len(test_ds))

    train_loader = DataLoader(train_ds, batch_size=batch_size,
                              shuffle=True, drop_last=True, num_workers=0)
    val_loader   = DataLoader(val_ds,   batch_size=batch_size, shuffle=False)
    test_loader  = DataLoader(test_ds,  batch_size=batch_size, shuffle=False)

    model = SolarForecastModel(
        input_size=NUM_FEATURES, horizons=horizons, quantiles=quantiles).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=lr,
        epochs=epochs, steps_per_epoch=len(train_loader),
        pct_start=0.10, anneal_strategy='cos',
        div_factor=25.0, final_div_factor=1e4)
    criterion = WeightedHorizonQuantileLoss(horizons=horizons, quantiles=quantiles)

    best_val, patience_c, best_state = float('inf'), 0, None
    history = {'train_loss': [], 'val_loss': [], 'lr': []}

    for epoch in range(1, epochs + 1):
        model.train()
        ep_loss = 0.0
        for Xb, yb, elev, ghi_p in train_loader:
            Xb, yb, elev, ghi_p = (Xb.to(device), yb.to(device),
                                    elev.to(device), ghi_p.to(device))
            optimizer.zero_grad()
            preds, _ = model(Xb)
            loss = criterion(preds, yb, elev, ghi_p)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            ep_loss += loss.item() * Xb.size(0)

        train_loss = ep_loss / len(train_ds)
        history['train_loss'].append(train_loss)
        history['lr'].append(optimizer.param_groups[0]['lr'])

        model.eval()
        vl = 0.0
        with torch.no_grad():
            for Xb, yb, elev, ghi_p in val_loader:
                preds, _ = model(Xb.to(device))
                vl += criterion(preds, yb.to(device),
                                elev.to(device), ghi_p.to(device)).item() * Xb.size(0)
        val_loss = vl / len(val_ds)
        history['val_loss'].append(val_loss)

        improved = val_loss < best_val - min_delta
        if improved:
            best_val, patience_c = val_loss, 0
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
        else:
            patience_c += 1

        if epoch % 10 == 0 or epoch <= 5 or improved:
            logger.info("Epoch %3d/%d  train=%.5f  val=%.5f  lr=%.2e%s",
                        epoch, epochs, train_loss, val_loss,
                        optimizer.param_groups[0]['lr'], ' ★' if improved else '')

        if patience_c >= patience:
            logger.info("Early stopping at epoch %d", epoch)
            break

    if best_state:
        model.load_state_dict(best_state)
        model.to(device)

    os.makedirs(save_dir, exist_ok=True)
    ckpt_path = os.path.join(save_dir, 'solar_forecaster_v2.pt')
    torch.save({
        'model_state_dict': {k: v.cpu() for k, v in model.state_dict().items()},
        'feature_scaler':   train_ds.feature_scaler.to_dict(),
        'target_scaler':    train_ds.target_scaler.to_dict(),
        'horizons':         horizons,
        'quantiles':        quantiles,
        'lookback_hours':   LOOKBACK_HOURS,
        'feature_cols':     FEATURE_COLUMNS,
        'hyperparameters': {'model_type': 'SolarForecastModel',
                            'input_size': NUM_FEATURES},
        'train_years':     train_years,
        'val_years':       val_years,
        'test_years':      test_years,
        'training_history': history,
        'best_val_loss':   best_val,
    }, ckpt_path)
    logger.info("Checkpoint saved: %s", ckpt_path)

    scalers = {'feature': train_ds.feature_scaler, 'target': train_ds.target_scaler}
    metrics = evaluate_forecaster(model, test_ds, scalers,
                                  horizons=horizons, quantiles=quantiles, device=device)
    return model, scalers, metrics


# =============================================================================
# Evaluation
# =============================================================================

def evaluate_forecaster(model:     SolarForecastModel,
                        test_ds:   SolarForecastDataset,
                        scalers:   Dict,
                        horizons:  List[int]   = HORIZONS,
                        quantiles: List[float] = QUANTILES,
                        device:    str         = 'cpu') -> Dict:
    """
    Rigorous evaluation: RMSE, MAE, MAPE (daytime only), Skill Score,
    PICP (80% interval), seasonal breakdown, cloud-regime segmentation.

    MAPE threshold: GHI_actual > 50 W/m² (elevation > 3°).
    Nighttime rows are excluded because GHI=0 makes MAPE → ∞ for any
    non-zero prediction, inflating the metric by 30–50 percentage points.
    """
    model.eval()
    model.to(device)
    target_scaler = scalers['target']
    loader = DataLoader(test_ds, batch_size=256, shuffle=False)

    all_preds, all_targets = [], []
    with torch.no_grad():
        for Xb, yb, _, _ in loader:
            preds, _ = model(Xb.to(device))
            all_preds.append(preds.cpu().numpy())
            all_targets.append(yb.numpy())

    preds   = np.concatenate(all_preds,   axis=0)   # [N, H, Q]
    targets = np.concatenate(all_targets, axis=0)   # [N, H]
    timestamps = np.array(test_ds.timestamps)
    months     = np.array([t.month for t in timestamps])

    q50_idx = quantiles.index(0.50)

    def inv_kt(a):
        """Inverse-transform scaled kt back to raw kt."""
        return np.clip(target_scaler.inverse_transform(a), 0, 1.2)

    # Convert kt predictions to GHI: GHI = kt * ghi_cs
    ghi_cs_all = test_ds.ghi_cs_targets  # [N, H]

    preds_kt    = np.stack([inv_kt(preds[:, h, q50_idx]) for h in range(len(horizons))], 1)
    targets_kt  = np.stack([inv_kt(targets[:, h])         for h in range(len(horizons))], 1)
    lower_kt    = np.stack([inv_kt(preds[:, h, 0])        for h in range(len(horizons))], 1)
    upper_kt    = np.stack([inv_kt(preds[:, h, -1])       for h in range(len(horizons))], 1)

    preds_ghi   = np.clip(preds_kt   * ghi_cs_all, 0, 1400)
    targets_ghi = np.clip(targets_kt * ghi_cs_all, 0, 1400)
    lower_ghi   = np.clip(lower_kt   * ghi_cs_all, 0, 1400)
    upper_ghi   = np.clip(upper_kt   * ghi_cs_all, 0, 1400)

    metrics: Dict = {'horizons': {}, 'seasonal': {}, 'cloud_regime': {}}

    for h_idx, h in enumerate(horizons):
        p, t  = preds_ghi[:, h_idx],  targets_ghi[:, h_idx]
        lo, hi = lower_ghi[:, h_idx], upper_ghi[:, h_idx]

        # Persistence baseline: GHI at t-24h (same-hour yesterday approximation)
        persist = np.roll(t, 24); persist[:24] = t[:24]

        # MAPE: daytime only (GHI > 50 W/m²)
        day   = t > 50.0
        mape  = (float(np.mean(np.abs((t[day] - p[day]) / t[day])) * 100)
                 if day.sum() > 0 else None)
        rmse  = float(np.sqrt(np.mean((p - t) ** 2)))
        mae   = float(np.mean(np.abs(p - t)))
        ss    = 1.0 - rmse / (float(np.sqrt(np.mean((persist - t) ** 2))) + 1e-6)
        picp  = float(((t >= lo) & (t <= hi)).mean())

        metrics['horizons'][f'{h}h'] = {
            'rmse':             round(rmse, 2),
            'mae':              round(mae, 2),
            'mape_daytime_pct': round(mape, 2) if mape is not None else None,
            'skill_score':      round(ss, 4),
            'picp_80pct':       round(picp, 4),
            'n_daytime':        int(day.sum()),
        }
        logger.info("  %2dh: RMSE=%.1f  MAE=%.1f  MAPE=%.1f%%  SS=%.3f  PICP=%.3f",
                    h, rmse, mae, mape or 0.0, ss, picp)

    # Seasonal breakdown
    for season, m_list in {'DJF': [12,1,2], 'MAM': [3,4,5],
                            'JJA': [6,7,8],  'SON': [9,10,11]}.items():
        mask = np.isin(months, m_list)
        if not mask.any():
            continue
        sm = {}
        for h_idx, h in enumerate(horizons):
            p, t = preds_ghi[mask, h_idx], targets_ghi[mask, h_idx]
            day  = t > 50.0
            sm[f'{h}h'] = {
                'rmse': round(float(np.sqrt(np.mean((p-t)**2))), 2),
                'mape': (round(float(np.mean(np.abs((t[day]-p[day])/t[day]))*100), 2)
                         if day.sum() > 0 else None),
            }
        metrics['seasonal'][season] = sm

    # Cloud-regime segmentation
    for regime, fn in [('clear',    lambda t: t[:, 0] > 400.0),
                        ('overcast', lambda t: (t[:, 0] > 50.0) & (t[:, 0] <= 400.0))]:
        mask = fn(targets_ghi)
        if not mask.any():
            continue
        metrics['cloud_regime'][regime] = {
            f'{h}h': {'rmse': round(float(np.sqrt(np.mean(
                (preds_ghi[mask, h_idx] - targets_ghi[mask, h_idx])**2))), 2),
                      'n': int(mask.sum())}
            for h_idx, h in enumerate(horizons)
        }

    metrics['total_samples']    = len(test_ds)
    metrics['daytime_fraction'] = float((targets_ghi[:, 0] > 50).mean())
    return metrics


def print_metrics(metrics: Dict) -> None:
    print("\n" + "=" * 72)
    print("  Solar Forecast Evaluation — v2")
    print("=" * 72)
    print(f"\n{'Horizon':>8} {'RMSE':>8} {'MAE':>8} "
          f"{'MAPE%':>8} {'SkillSc':>9} {'PICP80':>7}")
    print("-" * 55)
    for hk, hm in metrics.get('horizons', {}).items():
        mape_s = f"{hm['mape_daytime_pct']:.1f}" if hm.get('mape_daytime_pct') else 'N/A'
        print(f"{hk:>8} {hm['rmse']:>8.1f} {hm['mae']:>8.1f} "
              f"{mape_s:>8} {hm['skill_score']:>9.3f} {hm['picp_80pct']:>7.3f}")

    seasonal = metrics.get('seasonal', {})
    if seasonal:
        print(f"\n{'Season':>8} {'1h RMSE':>9} {'6h RMSE':>9} "
              f"{'24h RMSE':>10} {'1h MAPE%':>10}")
        print("-" * 50)
        for s, sm in seasonal.items():
            print(f"{s:>8} {sm.get('1h',{}).get('rmse',0):>9.1f} "
                  f"{sm.get('6h',{}).get('rmse',0):>9.1f} "
                  f"{sm.get('24h',{}).get('rmse',0):>10.1f} "
                  f"{sm.get('1h',{}).get('mape') or 0:>10.1f}")

    print(f"\nTotal test samples: {metrics.get('total_samples', 'N/A')}")
    print(f"Daytime fraction:   {metrics.get('daytime_fraction', 0):.1%}")
    print("=" * 72 + "\n")


# =============================================================================
# Inference — SolarForecaster (EMS Integration)
# =============================================================================

@dataclass
class EMSForecast:
    """Structured probabilistic forecast for EMS consumption."""
    timestamp:   datetime
    horizons:    List[int]
    raw:         Dict[str, float] = field(default_factory=dict)
    reserve_kw:  float = 0.0
    dispatch_kw: float = 0.0   # conservative: q10 PV power
    risk_index:  float = 0.5   # normalised uncertainty [0, 1]
    is_daytime:  bool  = False

    def get(self, key: str, default: float = 0.0) -> float:
        return self.raw.get(key, default)

    def summary(self) -> str:
        lines = [f"EMSForecast @ {self.timestamp}"]
        for h in self.horizons:
            q10 = self.raw.get(f'pv_q10_{h}h', 0)
            q50 = self.raw.get(f'pv_q50_{h}h', 0)
            q90 = self.raw.get(f'pv_q90_{h}h', 0)
            lines.append(f"  {h:>2}h  GHI q50={self.raw.get(f'ghi_q50_{h}h',0):.0f} W/m²"
                         f"  PV [{q10:.1f}, {q50:.1f}, {q90:.1f}] kW")
        lines.append(f"  Reserve={self.reserve_kw:.1f} kW  "
                     f"Dispatch={self.dispatch_kw:.1f} kW  Risk={self.risk_index:.2f}")
        return "\n".join(lines)


def _ghi_to_pv(ghi: float, kwp: float, temp_c: float = 30.0) -> float:
    """Simplified NOCT PV power conversion."""
    if ghi <= 0:
        return 0.0
    cell_t = temp_c + 25.0 * (ghi / 800.0)
    tf     = 1.0 + (-0.004) * (cell_t - 25.0)
    return max(0.0, kwp * (ghi / 1000.0) * tf * 0.97 * 0.96)


class SolarForecaster:
    """
    Loads a trained checkpoint and provides EMS-structured probabilistic forecasts.

    Usage:
        forecaster = SolarForecaster('models/solar_forecaster_v2.pt', provider)
        forecast   = forecaster.predict(datetime(2020, 6, 15, 12, 0))
        print(forecast.summary())

    v2 changes vs v1:
      - Returns EMSForecast dataclass instead of plain dict
      - Quantile outputs (q10/q50/q90) instead of MC-Dropout CI
      - reserve_kw and dispatch_kw pre-computed
      - Lookback uses all 34 features to match trained model
    """

    def __init__(self, checkpoint_path: str, solar_provider,
                 installed_kwp: float = 350.0, device: str = None):

        if not os.path.exists(checkpoint_path):
            raise FileNotFoundError(
                f"Checkpoint not found: {checkpoint_path}\n"
                "Run train_forecaster() first.")

        if device is None:
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.device = device

        ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)

        self.feature_scaler = RobustFeatureScaler.from_dict(ckpt['feature_scaler'])
        ts_dict = ckpt['target_scaler']
        if ts_dict.get('type') == 'KtTargetScaler':
            self.target_scaler = KtTargetScaler.from_dict(ts_dict)
        else:
            self.target_scaler = GHITargetScaler.from_dict(ts_dict)

        self.horizons  = ckpt['horizons']
        self.quantiles = ckpt.get('quantiles', QUANTILES)
        self.lookback  = ckpt['lookback_hours']

        self.model = SolarForecastModel(
            input_size=NUM_FEATURES,
            horizons=self.horizons,
            quantiles=self.quantiles,
        ).to(device)
        self.model.load_state_dict(ckpt['model_state_dict'])
        self.model.eval()

        self.provider      = solar_provider
        self.installed_kwp = installed_kwp

        logger.info("SolarForecaster v2 loaded: horizons=%s quantiles=%s lookback=%dh",
                    self.horizons, self.quantiles, self.lookback)

    # ── Public API ─────────────────────────────────────────────────────────

    def predict(self, timestamp: datetime,
                ambient_temp_c: float = 30.0) -> EMSForecast:
        """
        Issue a probabilistic forecast at the given timestamp.
        Returns EMSForecast with quantile GHI + PV power and EMS decision fields.
        """
        lookback_arr = self._build_lookback(timestamp)
        if lookback_arr is None:
            logger.warning("Insufficient lookback data at %s", timestamp)
            return self._empty_forecast(timestamp)

        feat_scaled = self.feature_scaler.transform(lookback_arr)
        x = torch.from_numpy(feat_scaled.astype(np.float32)
                              ).unsqueeze(0).to(self.device)

        with torch.no_grad():
            preds_q, _ = self.model(x)   # [1, H, Q]
        preds_np = preds_q[0].cpu().numpy()

        raw = {}
        for h_idx, h in enumerate(self.horizons):
            # Get clear-sky GHI at forecast target time
            target_ts = timestamp + timedelta(hours=h)
            ts_idx = pd.DatetimeIndex([target_ts])
            ghi_cs_h = float(np.clip(get_clearsky_ghi(ts_idx).iloc[0], 0, 1400))

            for q_idx, tau in enumerate(self.quantiles):
                kt_pred = float(np.clip(
                    self.target_scaler.inverse_transform(
                        np.array([preds_np[h_idx, q_idx]])), 0, 1.2))
                ghi = float(np.clip(kt_pred * ghi_cs_h, 0, 1400))
                tag = f'ghi_q{int(tau*100):02d}_{h}h'
                raw[tag] = round(ghi, 1)
                raw[f'pv_q{int(tau*100):02d}_{h}h'] = round(
                    _ghi_to_pv(ghi, self.installed_kwp, ambient_temp_c), 2)

        q10_1h = raw.get('pv_q10_1h', 0.0)
        q50_1h = raw.get('pv_q50_1h', 0.0)
        q90_1h = raw.get('pv_q90_1h', 0.0)
        risk   = min(1.0, (q90_1h - q10_1h) / max(q50_1h + 1.0, 50.0))
        reserve = max(10.0, 0.5 * (q90_1h - q10_1h))
        reserve = min(reserve, 0.30 * self.installed_kwp)

        sp = get_solar_position(pd.DatetimeIndex([timestamp]))
        is_day = float(sp['elevation'].iloc[0]) > 3.0

        return EMSForecast(
            timestamp=timestamp, horizons=self.horizons, raw=raw,
            reserve_kw=round(reserve, 2), dispatch_kw=round(q10_1h, 2),
            risk_index=round(risk, 3), is_daytime=is_day)

    # ── Private helpers ────────────────────────────────────────────────────

    def _build_lookback(self, timestamp: datetime) -> Optional[np.ndarray]:
        """Build [LOOKBACK × NUM_FEATURES] array from historical provider data."""
        # Need extra hours to correctly compute lags up to 24h and rolling windows up to 12h
        history_hours = self.lookback + 24
        times = [timestamp - timedelta(hours=h) for h in range(history_hours - 1, -1, -1)]

        records = []
        for t in times:
            ghi, temp = self.provider.get_irradiance(t)
            dni, dhi = self._lookup_dni_dhi(t)
            weather = self._lookup_weather(t)
            rec = {'GHI': ghi, 'DNI': dni, 'DHI': dhi, 'Temperature': temp}
            rec.update(weather)
            records.append(rec)

        df = pd.DataFrame(records, index=pd.DatetimeIndex(times))
        df.index.name = 'timestamp'
        
        df = add_research_features(df)
        
        if len(df) < self.lookback:
            return None
            
        lookback_df = df.iloc[-self.lookback:]
        return lookback_df[FEATURE_COLUMNS].values.astype(np.float32)

    def _lookup_dni_dhi(self, ts: datetime) -> Tuple[float, float]:
        try:
            mapped = self.provider._map_to_available_year(ts)
            idx = self.provider.data.index.get_indexer(
                [pd.Timestamp(mapped)], method='nearest',
                tolerance=pd.Timedelta('1h'))
            if idx[0] != -1:
                row = self.provider.data.iloc[idx[0]]
                return (max(0.0, float(row.get('DNI', 0.0))),
                        max(0.0, float(row.get('DHI', 0.0))))
        except Exception:
            pass
        return (0.0, 0.0)

    def _lookup_weather(self, ts: datetime) -> dict:
        """Fetch weather columns from provider data for inference consistency."""
        defaults = {'Wind_Speed': 0.0, 'Pressure': 950.0,
                    'Relative_Humidity': 50.0, 'Cloud_Type': 0.0}
        try:
            mapped = self.provider._map_to_available_year(ts)
            idx = self.provider.data.index.get_indexer(
                [pd.Timestamp(mapped)], method='nearest',
                tolerance=pd.Timedelta('1h'))
            if idx[0] != -1:
                row = self.provider.data.iloc[idx[0]]
                return {
                    'Wind_Speed': float(row.get('Wind_Speed', 0.0)),
                    'Pressure': float(row.get('Pressure', 950.0)),
                    'Relative_Humidity': float(row.get('Relative_Humidity', 50.0)),
                    'Cloud_Type': float(row.get('Cloud_Type', 0.0)),
                }
        except Exception:
            pass
        return defaults

    def _empty_forecast(self, ts: datetime) -> EMSForecast:
        raw = {}
        for h in self.horizons:
            for tau in self.quantiles:
                raw[f'ghi_q{int(tau*100):02d}_{h}h'] = 0.0
                raw[f'pv_q{int(tau*100):02d}_{h}h']  = 0.0
        return EMSForecast(timestamp=ts, horizons=self.horizons, raw=raw)


# =============================================================================
# EMS Reserve + Scenario Tree
# =============================================================================

def compute_reserve_kw(forecast: EMSForecast,
                        min_reserve_kw: float = 10.0) -> float:
    """
    Spinning reserve from 80% prediction interval width.
    reserve = max(min_reserve, 0.5 × (q90_pv_1h - q10_pv_1h))
    """
    return forecast.reserve_kw


def build_scenario_tree(forecasts: List[EMSForecast],
                         n_scenarios: int = 5) -> Dict[str, Any]:
    """
    5-scenario equiprobable PV power tree for stochastic MPC.
    Scenarios interpolated linearly between [q10, q90] at each timestep.
    """
    n_steps = len(forecasts)
    scenarios = np.zeros((n_scenarios, n_steps), dtype=np.float32)
    percentiles = np.linspace(0, 1, n_scenarios)

    for t_idx, fc in enumerate(forecasts):
        q10 = fc.raw.get('pv_q10_1h', 0.0)
        q90 = fc.raw.get('pv_q90_1h', 0.0)
        for s_idx, p in enumerate(percentiles):
            scenarios[s_idx, t_idx] = q10 + p * (q90 - q10)

    return {
        'scenarios':     scenarios,
        'probabilities': np.ones(n_scenarios, dtype=np.float32) / n_scenarios,
        'timestamps':    [fc.timestamp for fc in forecasts],
        'reserve_kw':    max((fc.reserve_kw for fc in forecasts), default=0.0),
        'n_scenarios':   n_scenarios,
    }