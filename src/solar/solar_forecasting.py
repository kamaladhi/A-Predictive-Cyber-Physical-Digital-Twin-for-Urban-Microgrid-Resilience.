"""
Solar Irradiance Forecasting Module
=====================================

LSTM-based multi-horizon solar irradiance (GHI) forecasting for predictive
digital twin microgrid operation.

Architecture
------------
Input [24 x 10] --> LSTM(64, 2 layers) --> FC(64,32) --> FC(32,3)
                                                          |
                                                    [GHI_1h, GHI_6h, GHI_24h]

Features (10 total):
  - Physical:  GHI, DNI, DHI, Temperature       (4)
  - Temporal:  hour_sin, hour_cos,               (6)
               doy_sin,  doy_cos,
               month_sin, month_cos

Dependencies
------------
- PyTorch >= 2.0
- NumPy, Pandas (already in project)

Usage
-----
    # Training
    from src.solar.solar_forecasting import train_forecaster
    model, scalers, metrics = train_forecaster(clean_df)

    # Inference (EMS integration)
    from src.solar.solar_forecasting import SolarForecaster
    forecaster = SolarForecaster('SolarData/models/solar_lstm.pt', provider)
    forecast = forecaster.predict(current_timestamp)
    # => {'ghi_1h': 820.3, 'ghi_6h': 45.2, 'ghi_24h': 710.5, ...}

Limitations
-----------
- Trained on NSRDB data for a single location (22.20N, 78.47E).
- NSRDB data is satellite-derived with inherent smoothing vs ground truth.
- Hourly resolution limits sub-hour variability capture.
- No exogenous weather forecast inputs (cloud cover predictions, NWP).
- LSTM may underperform transformers for very long horizons (24h).

References
----------
[1] Sengupta et al., "The National Solar Radiation Data Base (NSRDB)",
    Renewable and Sustainable Energy Reviews, 2018.
[2] Hochreiter & Schmidhuber, "Long Short-Term Memory", Neural Computation, 1997.
"""

import os
import json
import math
import logging
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

# Lookback window: 48 hourly observations (two full diurnal cycles)
# Extended from 24 to improve 24h-horizon accuracy.
LOOKBACK_HOURS = 48

# Prediction horizons
HORIZONS = [1, 6, 24]  # hours ahead

# Physical feature columns (from NSRDB preprocessing)
PHYSICAL_FEATURES = ['GHI', 'DNI', 'DHI', 'Temperature']

# Engineered feature columns (added during preprocessing)
ENGINEERED_FEATURES = ['ghi_rolling_3h', 'ghi_rolling_6h', 'ghi_variability']

# Total features = 4 physical + 3 engineered + 8 temporal encodings
NUM_FEATURES = 15

# Default model hyperparameters
DEFAULT_HIDDEN_SIZE = 128
DEFAULT_NUM_LAYERS = 2
DEFAULT_DROPOUT = 0.3
DEFAULT_BATCH_SIZE = 64
DEFAULT_EPOCHS = 80
DEFAULT_LR = 1e-3
DEFAULT_PATIENCE = 15  # early stopping patience

# MC Dropout inference passes
MC_DROPOUT_PASSES = 20

# Reproducibility
RANDOM_SEED = 42


# =============================================================================
# FEATURE ENGINEERING
# =============================================================================

def add_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add engineered and cyclical temporal features to a DatetimeIndex-ed DataFrame.

    Engineered features:
      - ghi_rolling_3h: 3-hour rolling mean GHI (trend smoothing)
      - ghi_rolling_6h: 6-hour rolling mean GHI (longer trend)
      - ghi_variability: 3-hour rolling std of GHI (cloud variability proxy)

    Cyclical encoding (sin/cos) avoids artificial discontinuities at
    midnight (hour 23->0) and year-end (day 365->1).

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with DatetimeIndex. Must contain 'GHI' column.

    Returns
    -------
    pd.DataFrame
        Copy with 11 new columns: 3 engineered + 8 temporal encodings.
    """
    df = df.copy()

    # ── Engineered features (rolling statistics) ─────────────────────────
    if 'GHI' in df.columns:
        df['ghi_rolling_3h'] = (
            df['GHI'].rolling(window=3, min_periods=1).mean()
        )
        df['ghi_rolling_6h'] = (
            df['GHI'].rolling(window=6, min_periods=1).mean()
        )
        df['ghi_variability'] = (
            df['GHI'].rolling(window=3, min_periods=1).std().fillna(0.0)
        )
    else:
        df['ghi_rolling_3h'] = 0.0
        df['ghi_rolling_6h'] = 0.0
        df['ghi_variability'] = 0.0

    # ── Cyclical temporal encodings ──────────────────────────────────────
    hours = df.index.hour + df.index.minute / 60.0
    df['hour_sin'] = np.sin(2 * np.pi * hours / 24.0)
    df['hour_cos'] = np.cos(2 * np.pi * hours / 24.0)

    doy = df.index.dayofyear.astype(float)
    df['doy_sin'] = np.sin(2 * np.pi * doy / 365.25)
    df['doy_cos'] = np.cos(2 * np.pi * doy / 365.25)

    month = df.index.month.astype(float)
    df['month_sin'] = np.sin(2 * np.pi * month / 12.0)
    df['month_cos'] = np.cos(2 * np.pi * month / 12.0)

    # Week-of-year encoding (finer seasonal granularity)
    week = df.index.isocalendar().week.astype(float).values
    df['week_sin'] = np.sin(2 * np.pi * week / 52.0)
    df['week_cos'] = np.cos(2 * np.pi * week / 52.0)

    return df


# =============================================================================
# NORMALIZATION
# =============================================================================

class MinMaxScaler:
    """
    Simple min-max scaler that serializes to/from JSON.

    Uses min-max (not z-score) because GHI has a hard physical floor at 0,
    and the distribution is highly non-Gaussian (nighttime zeros).
    """

    def __init__(self):
        self.min_vals: Optional[np.ndarray] = None
        self.max_vals: Optional[np.ndarray] = None
        self.range_vals: Optional[np.ndarray] = None

    def fit(self, data: np.ndarray) -> 'MinMaxScaler':
        """Compute min/max from training data."""
        self.min_vals = data.min(axis=0)
        self.max_vals = data.max(axis=0)
        self.range_vals = self.max_vals - self.min_vals
        # Avoid division by zero for constant columns
        self.range_vals[self.range_vals == 0] = 1.0
        return self

    def transform(self, data: np.ndarray) -> np.ndarray:
        """Scale data to [0, 1]."""
        return (data - self.min_vals) / self.range_vals

    def inverse_transform(self, data: np.ndarray) -> np.ndarray:
        """Invert scaling back to original range."""
        return data * self.range_vals + self.min_vals

    def to_dict(self) -> Dict[str, List[float]]:
        """Serialize for JSON storage."""
        return {
            'min': self.min_vals.tolist(),
            'max': self.max_vals.tolist(),
            'range': self.range_vals.tolist(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, List[float]]) -> 'MinMaxScaler':
        """Deserialize from JSON."""
        scaler = cls()
        scaler.min_vals = np.array(d['min'])
        scaler.max_vals = np.array(d['max'])
        scaler.range_vals = np.array(d['range'])
        return scaler


class RobustScaler:
    """
    Median / IQR scaler — more robust to outlier GHI spikes than MinMax.

    Transforms data as: (x - median) / IQR, where IQR = Q75 - Q25.
    Falls back to range=1 for constant columns.
    """

    def __init__(self):
        self.median_vals: Optional[np.ndarray] = None
        self.iqr_vals: Optional[np.ndarray] = None

    def fit(self, data: np.ndarray) -> 'RobustScaler':
        self.median_vals = np.median(data, axis=0)
        q25 = np.percentile(data, 25, axis=0)
        q75 = np.percentile(data, 75, axis=0)
        self.iqr_vals = q75 - q25
        self.iqr_vals[self.iqr_vals == 0] = 1.0
        return self

    def transform(self, data: np.ndarray) -> np.ndarray:
        return (data - self.median_vals) / self.iqr_vals

    def inverse_transform(self, data: np.ndarray) -> np.ndarray:
        return data * self.iqr_vals + self.median_vals

    def to_dict(self) -> Dict[str, List[float]]:
        return {
            'median': self.median_vals.tolist(),
            'iqr': self.iqr_vals.tolist(),
            'scaler_type': 'robust',
        }

    @classmethod
    def from_dict(cls, d: Dict[str, List[float]]) -> 'RobustScaler':
        scaler = cls()
        scaler.median_vals = np.array(d['median'])
        scaler.iqr_vals = np.array(d['iqr'])
        return scaler


def load_scaler(d: Dict[str, Any]):
    """Load either MinMaxScaler or RobustScaler from a serialized dict."""
    if d.get('scaler_type') == 'robust':
        return RobustScaler.from_dict(d)
    return MinMaxScaler.from_dict(d)


class WeightedMSELoss(nn.Module):
    """
    MSE loss that weights high-irradiance samples more heavily.

    Samples whose *target* GHI (normalized) exceeds `threshold` get
    `high_weight` instead of 1.0.  This reduces MAPE during
    economically-important daylight hours without ignoring nighttime.
    """

    def __init__(self, threshold: float = 0.4, high_weight: float = 2.0):
        super().__init__()
        self.threshold = threshold
        self.high_weight = high_weight

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        mse = (pred - target) ** 2
        # Weight based on first horizon (1h GHI) as proxy for daylight
        weights = torch.where(
            target[:, 0:1] > self.threshold,
            torch.tensor(self.high_weight, device=pred.device),
            torch.tensor(1.0, device=pred.device),
        )
        return (mse * weights).mean()


# =============================================================================
# PYTORCH DATASET
# =============================================================================

class SolarForecastDataset(Dataset):
    """
    PyTorch Dataset for solar irradiance forecasting.

    Creates sliding-window samples from hourly NSRDB data:
      Input:  [t - LOOKBACK ... t]     (48 timesteps x 15 features)
      Target: GHI at [t+1, t+6, t+24]  (3 values)

    Parameters
    ----------
    df : pd.DataFrame
        Preprocessed NSRDB data with DatetimeIndex (hourly).
        Must contain GHI, DNI, DHI, Temperature columns.
    feature_scaler : MinMaxScaler, RobustScaler, or None
        If provided, used to normalize features. If None, a new
        RobustScaler is fit on this data.
    target_scaler : MinMaxScaler, RobustScaler, or None
        If provided, used to normalize targets. If None, a new
        RobustScaler is fit on this data.
    """

    # Feature column order (must match at train and inference time)
    FEATURE_COLS = PHYSICAL_FEATURES + ENGINEERED_FEATURES + [
        'hour_sin', 'hour_cos', 'doy_sin', 'doy_cos',
        'month_sin', 'month_cos', 'week_sin', 'week_cos',
    ]

    def __init__(self,
                 df: pd.DataFrame,
                 feature_scaler=None,
                 target_scaler=None):

        # Add temporal features
        df = add_temporal_features(df)

        # Validate columns
        missing = [c for c in self.FEATURE_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        # Extract raw arrays
        features_raw = df[self.FEATURE_COLS].values.astype(np.float32)
        ghi_raw = df['GHI'].values.astype(np.float32)

        # Fit or apply scalers (default: RobustScaler for better outlier handling)
        if feature_scaler is None:
            self.feature_scaler = RobustScaler().fit(features_raw)
        else:
            self.feature_scaler = feature_scaler

        if target_scaler is None:
            self.target_scaler = RobustScaler().fit(ghi_raw.reshape(-1, 1))
        else:
            self.target_scaler = target_scaler

        features_scaled = self.feature_scaler.transform(features_raw)
        ghi_scaled = self.target_scaler.transform(
            ghi_raw.reshape(-1, 1)).flatten()

        # Build sliding window samples
        self.X = []  # input sequences
        self.y = []  # multi-horizon targets
        self.timestamps = []  # for evaluation alignment

        max_horizon = max(HORIZONS)

        for i in range(LOOKBACK_HOURS, len(df) - max_horizon):
            x_window = features_scaled[i - LOOKBACK_HOURS: i]
            y_horizons = np.array([ghi_scaled[i + h - 1] for h in HORIZONS])

            self.X.append(x_window)
            self.y.append(y_horizons)
            self.timestamps.append(df.index[i])

        self.X = np.array(self.X, dtype=np.float32)
        self.y = np.array(self.y, dtype=np.float32)

        logger.info(f"SolarForecastDataset: {len(self)} samples, "
                    f"X shape {self.X.shape}, y shape {self.y.shape}")

    def __len__(self) -> int:
        return len(self.X)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        return (torch.from_numpy(self.X[idx]),
                torch.from_numpy(self.y[idx]))


# =============================================================================
# LSTM MODEL
# =============================================================================

class TemporalAttention(nn.Module):
    """
    Lightweight temporal attention over LSTM output sequence.

    Learns which timesteps in the lookback window are most relevant
    for each prediction, improving long-horizon (24h) accuracy.
    """

    def __init__(self, hidden_size: int):
        super().__init__()
        self.attn = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 4),
            nn.Tanh(),
            nn.Linear(hidden_size // 4, 1),
        )

    def forward(self, lstm_out: torch.Tensor) -> torch.Tensor:
        """
        Parameters
        ----------
        lstm_out : torch.Tensor
            Shape [batch, seq_len, hidden_size].

        Returns
        -------
        torch.Tensor
            Shape [batch, hidden_size] — attention-weighted context.
        """
        # attn_weights: [batch, seq_len, 1]
        attn_weights = torch.softmax(self.attn(lstm_out), dim=1)
        # Weighted sum: [batch, hidden_size]
        context = (lstm_out * attn_weights).sum(dim=1)
        return context


class SolarLSTM(nn.Module):
    """
    Multi-horizon LSTM with temporal attention for solar forecasting.

    Architecture:
        LSTM(input=15, hidden=128, layers=2, dropout=0.3)
        -> TemporalAttention(128)
        -> FC(128, 64) -> ReLU -> Dropout -> FC(64, 3)

    Parameters
    ----------
    input_size : int
        Number of features per timestep (default: 15).
    hidden_size : int
        LSTM hidden dimension (default: 128).
    num_layers : int
        Number of stacked LSTM layers (default: 2).
    dropout : float
        Dropout between LSTM layers and in FC head (default: 0.3).
    num_horizons : int
        Number of prediction horizons (default: 3).
    """

    def __init__(self,
                 input_size: int = NUM_FEATURES,
                 hidden_size: int = DEFAULT_HIDDEN_SIZE,
                 num_layers: int = DEFAULT_NUM_LAYERS,
                 dropout: float = DEFAULT_DROPOUT,
                 num_horizons: int = len(HORIZONS)):
        super().__init__()

        self.hidden_size = hidden_size
        self.dropout_rate = dropout

        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )

        # Temporal attention over full sequence
        self.attention = TemporalAttention(hidden_size)

        # Feed-forward head with dropout (used for MC Dropout at inference)
        self.fc = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, num_horizons),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass.

        Parameters
        ----------
        x : torch.Tensor
            Shape [batch, seq_len, features].

        Returns
        -------
        torch.Tensor
            Shape [batch, num_horizons] — predicted GHI (normalized).
        """
        # LSTM output: (batch, seq_len, hidden_size)
        lstm_out, _ = self.lstm(x)

        # Attention-weighted context (replaces last-timestep-only)
        context = self.attention(lstm_out)

        # Project to horizon predictions
        return self.fc(context)


# =============================================================================
# TRAINING
# =============================================================================

def train_forecaster(
    df: pd.DataFrame,
    train_years: List[int] = None,
    test_years: List[int] = None,
    hidden_size: int = DEFAULT_HIDDEN_SIZE,
    num_layers: int = DEFAULT_NUM_LAYERS,
    dropout: float = DEFAULT_DROPOUT,
    batch_size: int = DEFAULT_BATCH_SIZE,
    epochs: int = DEFAULT_EPOCHS,
    lr: float = DEFAULT_LR,
    patience: int = DEFAULT_PATIENCE,
    save_dir: str = None,
    device: str = None,
) -> Tuple[SolarLSTM, Dict[str, MinMaxScaler], Dict[str, Any]]:
    """
    Train the LSTM solar forecaster end-to-end.

    Parameters
    ----------
    df : pd.DataFrame
        Preprocessed NSRDB data (hourly, DatetimeIndex).
    train_years : list of int
        Years for training (default: [2018, 2019]).
    test_years : list of int
        Years for testing (default: [2020]).
    hidden_size, num_layers, dropout : model hyperparameters.
    batch_size, epochs, lr : training hyperparameters.
    patience : int
        Early stopping patience (epochs without improvement).
    save_dir : str
        Directory to save model checkpoint + scalers.
        Default: 'SolarData/models'.
    device : str
        'cuda' or 'cpu'. Auto-detected if None.

    Returns
    -------
    (model, scalers, metrics)
        - model: trained SolarLSTM
        - scalers: dict with 'feature' and 'target' MinMaxScaler
        - metrics: evaluation metrics dict
    """
    # Defaults
    if train_years is None:
        train_years = [2018, 2019]
    if test_years is None:
        test_years = [2020]
    if save_dir is None:
        save_dir = os.path.join(os.path.dirname(__file__), 'models')
    if device is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'

    # Reproducibility
    torch.manual_seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    logger.info(f"Training solar forecaster on {device}")
    logger.info(f"  Train years: {train_years}, Test years: {test_years}")
    logger.info(f"  Model: LSTM(hidden={hidden_size}, layers={num_layers}, "
                f"dropout={dropout})")
    logger.info(f"  Training: epochs={epochs}, batch={batch_size}, lr={lr}")

    # ── Split by year ────────────────────────────────────────────────────
    train_df = df[df.index.year.isin(train_years)]
    test_df = df[df.index.year.isin(test_years)]

    logger.info(f"  Train samples: {len(train_df)}, Test samples: {len(test_df)}")

    # ── Create datasets ──────────────────────────────────────────────────
    train_ds = SolarForecastDataset(train_df)
    # Re-use train scalers for test data (critical for correctness)
    test_ds = SolarForecastDataset(
        test_df,
        feature_scaler=train_ds.feature_scaler,
        target_scaler=train_ds.target_scaler,
    )

    train_loader = DataLoader(train_ds, batch_size=batch_size,
                              shuffle=True, drop_last=True)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False)

    # ── Model ────────────────────────────────────────────────────────────
    model = SolarLSTM(
        input_size=NUM_FEATURES,
        hidden_size=hidden_size,
        num_layers=num_layers,
        dropout=dropout,
    ).to(device)

    param_count = sum(p.numel() for p in model.parameters())
    logger.info(f"  Model parameters: {param_count:,}")

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='min', factor=0.5, patience=5, verbose=False)
    criterion = WeightedMSELoss(threshold=0.4, high_weight=2.0)

    # ── Training loop with early stopping ────────────────────────────────
    best_val_loss = float('inf')
    patience_counter = 0
    best_state = None
    train_losses = []
    val_losses = []

    for epoch in range(1, epochs + 1):
        # Train
        model.train()
        epoch_loss = 0.0
        for X_batch, y_batch in train_loader:
            X_batch = X_batch.to(device)
            y_batch = y_batch.to(device)

            optimizer.zero_grad()
            pred = model(X_batch)
            loss = criterion(pred, y_batch)
            loss.backward()
            optimizer.step()

            epoch_loss += loss.item() * X_batch.size(0)

        train_loss = epoch_loss / len(train_ds)
        train_losses.append(train_loss)

        # Validate
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for X_batch, y_batch in test_loader:
                X_batch = X_batch.to(device)
                y_batch = y_batch.to(device)
                pred = model(X_batch)
                val_loss += criterion(pred, y_batch).item() * X_batch.size(0)

        val_loss /= len(test_ds)
        val_losses.append(val_loss)

        # Step learning rate scheduler
        scheduler.step(val_loss)

        # Early stopping check
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            best_state = {k: v.cpu().clone() for k, v in
                          model.state_dict().items()}
        else:
            patience_counter += 1

        if epoch % 5 == 0 or epoch == 1 or patience_counter == 0:
            logger.info(f"  Epoch {epoch:3d}/{epochs}: "
                        f"train_loss={train_loss:.6f}, "
                        f"val_loss={val_loss:.6f}"
                        f"{' *' if patience_counter == 0 else ''}")

        if patience_counter >= patience:
            logger.info(f"  Early stopping at epoch {epoch} "
                        f"(no improvement for {patience} epochs)")
            break

    # Restore best model
    if best_state is not None:
        model.load_state_dict(best_state)
        model.to(device)

    # ── Save checkpoint ──────────────────────────────────────────────────
    os.makedirs(save_dir, exist_ok=True)
    checkpoint_path = os.path.join(save_dir, 'solar_lstm.pt')

    checkpoint = {
        'model_state_dict': {k: v.cpu() for k, v in
                             model.state_dict().items()},
        'feature_scaler': train_ds.feature_scaler.to_dict(),
        'target_scaler': train_ds.target_scaler.to_dict(),
        'horizons': HORIZONS,
        'lookback_hours': LOOKBACK_HOURS,
        'feature_cols': SolarForecastDataset.FEATURE_COLS,
        'hyperparameters': {
            'hidden_size': hidden_size,
            'num_layers': num_layers,
            'dropout': dropout,
            'input_size': NUM_FEATURES,
        },
        'train_years': train_years,
        'test_years': test_years,
        'train_losses': train_losses,
        'val_losses': val_losses,
        'best_val_loss': best_val_loss,
    }
    torch.save(checkpoint, checkpoint_path)
    logger.info(f"  Checkpoint saved: {checkpoint_path}")

    # ── Evaluate ─────────────────────────────────────────────────────────
    scalers = {
        'feature': train_ds.feature_scaler,
        'target': train_ds.target_scaler,
    }
    metrics = evaluate_forecaster(model, test_ds, scalers, device=device)

    return model, scalers, metrics


# =============================================================================
# EVALUATION
# =============================================================================

def evaluate_forecaster(
    model: SolarLSTM,
    test_ds: SolarForecastDataset,
    scalers: Dict[str, MinMaxScaler],
    device: str = 'cpu',
) -> Dict[str, Any]:
    """
    Evaluate forecaster on test data with RMSE, MAE, MAPE per horizon.

    Also computes seasonal breakdown (DJF, MAM, JJA, SON).

    Parameters
    ----------
    model : SolarLSTM
        Trained model.
    test_ds : SolarForecastDataset
        Test dataset.
    scalers : dict
        Contains 'target' MinMaxScaler for inverse transform.
    device : str
        Compute device.

    Returns
    -------
    dict
        Structured metrics with per-horizon and seasonal breakdowns.
    """
    model.eval()
    model.to(device)

    loader = DataLoader(test_ds, batch_size=128, shuffle=False)
    target_scaler = scalers['target']

    all_preds = []
    all_targets = []

    with torch.no_grad():
        for X_batch, y_batch in loader:
            X_batch = X_batch.to(device)
            pred = model(X_batch).cpu().numpy()
            all_preds.append(pred)
            all_targets.append(y_batch.numpy())

    preds = np.concatenate(all_preds, axis=0)   # [N, 3]
    targets = np.concatenate(all_targets, axis=0)  # [N, 3]

    # Inverse-transform to original GHI scale (W/m2)
    preds_ghi = np.column_stack([
        target_scaler.inverse_transform(preds[:, h:h+1])
        for h in range(len(HORIZONS))
    ])
    targets_ghi = np.column_stack([
        target_scaler.inverse_transform(targets[:, h:h+1])
        for h in range(len(HORIZONS))
    ])

    # Clamp predictions to physical range [0, 1400]
    preds_ghi = np.clip(preds_ghi, 0, 1400)

    # ── Per-horizon metrics ──────────────────────────────────────────────
    metrics = {'horizons': {}}

    for h_idx, horizon in enumerate(HORIZONS):
        p = preds_ghi[:, h_idx]
        t = targets_ghi[:, h_idx]

        rmse = float(np.sqrt(np.mean((p - t) ** 2)))
        mae = float(np.mean(np.abs(p - t)))

        # MAPE: only compute for non-zero actuals (avoid div/0 at night)
        nonzero_mask = t > 10  # threshold to avoid near-zero noise
        if nonzero_mask.sum() > 0:
            mape = float(np.mean(np.abs((t[nonzero_mask] - p[nonzero_mask])
                                        / t[nonzero_mask])) * 100)
        else:
            mape = 0.0

        metrics['horizons'][f'{horizon}h'] = {
            'rmse': round(rmse, 2),
            'mae': round(mae, 2),
            'mape': round(mape, 2),
        }

        logger.info(f"  {horizon}h horizon: "
                    f"RMSE={rmse:.1f} W/m2, MAE={mae:.1f} W/m2, "
                    f"MAPE={mape:.1f}%")

    # ── Seasonal breakdown ───────────────────────────────────────────────
    timestamps = np.array(test_ds.timestamps)
    months = np.array([ts.month for ts in timestamps])

    season_map = {
        'DJF': [12, 1, 2],   # Winter
        'MAM': [3, 4, 5],    # Pre-monsoon
        'JJA': [6, 7, 8],    # Monsoon
        'SON': [9, 10, 11],  # Post-monsoon
    }

    metrics['seasonal'] = {}

    for season, season_months in season_map.items():
        mask = np.isin(months, season_months)
        if mask.sum() == 0:
            continue

        season_metrics = {}
        for h_idx, horizon in enumerate(HORIZONS):
            p = preds_ghi[mask, h_idx]
            t = targets_ghi[mask, h_idx]
            rmse = float(np.sqrt(np.mean((p - t) ** 2)))
            mae = float(np.mean(np.abs(p - t)))
            season_metrics[f'{horizon}h'] = {
                'rmse': round(rmse, 2),
                'mae': round(mae, 2),
            }

        metrics['seasonal'][season] = season_metrics
        logger.info(f"  Season {season}: 1h RMSE={season_metrics['1h']['rmse']:.1f}, "
                    f"6h RMSE={season_metrics['6h']['rmse']:.1f}, "
                    f"24h RMSE={season_metrics['24h']['rmse']:.1f}")

    # Summary
    metrics['total_samples'] = len(test_ds)
    metrics['horizons_hours'] = HORIZONS

    return metrics


# =============================================================================
# INFERENCE API — EMS INTEGRATION
# =============================================================================

class SolarForecaster:
    """
    Inference-time solar irradiance forecaster for EMS integration.

    Loads a trained checkpoint and provides a simple predict(timestamp) API.
    No training dependencies at inference time.

    Parameters
    ----------
    checkpoint_path : str
        Path to saved .pt checkpoint file.
    solar_provider : SolarDataProvider
        Provides historical irradiance data for building lookback windows.
    device : str
        Compute device ('cuda' or 'cpu'). Auto-detected if None.

    Example
    -------
    >>> from src.solar.pv_power_model import SolarDataProvider
    >>> from src.solar.solar_forecasting import SolarForecaster
    >>> forecaster = SolarForecaster('SolarData/models/solar_lstm.pt', provider)
    >>> forecast = forecaster.predict(datetime(2020, 6, 15, 12, 0))
    >>> print(forecast['ghi_1h'])  # GHI 1 hour ahead (W/m2)
    820.3
    """

    def __init__(self,
                 checkpoint_path: str,
                 solar_provider,
                 device: str = None):

        if device is None:
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.device = device

        # Load checkpoint
        checkpoint = torch.load(checkpoint_path, map_location=device,
                                weights_only=False)

        # Restore scalers (auto-detects MinMaxScaler vs RobustScaler)
        self.feature_scaler = load_scaler(checkpoint['feature_scaler'])
        self.target_scaler = load_scaler(checkpoint['target_scaler'])

        # Restore model (keep dropout for MC Dropout inference)
        hp = checkpoint['hyperparameters']
        self._dropout = hp.get('dropout', DEFAULT_DROPOUT)
        self.model = SolarLSTM(
            input_size=hp['input_size'],
            hidden_size=hp['hidden_size'],
            num_layers=hp['num_layers'],
            dropout=self._dropout,
        ).to(device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()

        self.horizons = checkpoint['horizons']
        self.lookback = checkpoint['lookback_hours']
        self.feature_cols = checkpoint['feature_cols']
        self.provider = solar_provider

        logger.info(f"SolarForecaster loaded from {checkpoint_path}")
        logger.info(f"  Horizons: {self.horizons}h, "
                    f"Lookback: {self.lookback}h")

    def predict(self, timestamp: datetime) -> Dict[str, float]:
        """
        Predict future GHI at multiple horizons from the given timestamp.

        Uses MC Dropout (N=20 stochastic forward passes) for empirical
        confidence intervals, replacing the earlier static heuristic.

        Parameters
        ----------
        timestamp : datetime
            Current time. The model uses the preceding LOOKBACK_HOURS of
            irradiance data as context.

        Returns
        -------
        dict
            Keys: 'ghi_1h', 'ghi_6h', 'ghi_24h' (W/m2),
                  plus '_upper' and '_lower' confidence bounds (90% CI),
                  'uncertainty' (mean normalised std across horizons),
                  and 'timestamp' (the input time).
        """
        # Build lookback window from solarProvider
        lookback_data = self._build_lookback(timestamp)

        if lookback_data is None:
            logger.warning(f"Insufficient lookback data for {timestamp}")
            return self._empty_forecast(timestamp)

        # Normalize and predict with MC Dropout
        features_scaled = self.feature_scaler.transform(lookback_data)
        x_tensor = torch.from_numpy(
            features_scaled.astype(np.float32)
        ).unsqueeze(0).to(self.device)

        mc_preds = self._mc_dropout_predict(x_tensor, n_passes=MC_DROPOUT_PASSES)

        # mc_preds: [n_passes, num_horizons] in normalised space
        mean_pred = mc_preds.mean(axis=0)
        std_pred = mc_preds.std(axis=0)

        # Inverse transform to GHI (W/m2)
        predictions = {'timestamp': timestamp}
        uncertainties = []

        for h_idx, horizon in enumerate(self.horizons):
            ghi_mean = float(self.target_scaler.inverse_transform(
                mean_pred[h_idx:h_idx+1].reshape(-1, 1)
            ).flatten()[0])
            ghi_std = float(abs(
                self.target_scaler.inverse_transform(
                    (mean_pred[h_idx:h_idx+1] + std_pred[h_idx:h_idx+1]).reshape(-1, 1)
                ).flatten()[0] - ghi_mean
            ))

            # Clamp to physical range
            ghi = max(0.0, min(ghi_mean, 1400.0))

            key = f'ghi_{horizon}h'
            predictions[key] = round(ghi, 1)

            # 90% confidence interval from empirical distribution
            ci_90 = 1.645 * ghi_std
            predictions[f'{key}_upper'] = round(min(ghi + ci_90, 1400.0), 1)
            predictions[f'{key}_lower'] = round(max(ghi - ci_90, 0.0), 1)

            # Per-horizon normalised uncertainty
            uncertainties.append(ghi_std / max(ghi, 50.0))

        # Aggregate uncertainty (mean across horizons, clipped to [0, 1])
        predictions['uncertainty'] = round(
            float(min(1.0, np.mean(uncertainties))), 3)

        return predictions

    def _mc_dropout_predict(self, x_tensor: torch.Tensor,
                            n_passes: int = MC_DROPOUT_PASSES) -> np.ndarray:
        """
        Run N stochastic forward passes with dropout enabled.

        Returns
        -------
        np.ndarray
            Shape [n_passes, num_horizons] — normalised predictions.
        """
        # Enable dropout layers while keeping batch-norm (if any) in eval
        self.model.train()  # enables dropout
        preds = []
        with torch.no_grad():
            for _ in range(n_passes):
                pred = self.model(x_tensor).cpu().numpy()[0]
                preds.append(pred)
        self.model.eval()  # restore eval mode
        return np.array(preds)

    def _build_lookback(self, timestamp: datetime) -> Optional[np.ndarray]:
        """
        Build a [lookback x features] array from the SolarDataProvider.

        Retrieves hourly GHI, DNI, DHI, Temperature for the preceding
        LOOKBACK_HOURS and computes engineered + temporal features.
        """
        ghi_history = []
        rows = []

        for h in range(self.lookback, 0, -1):
            t = timestamp - timedelta(hours=h)

            # Get irradiance from provider (returns GHI, Temperature)
            ghi, temp = self.provider.get_irradiance(t)
            ghi_history.append(ghi)

            # For DNI and DHI, attempt direct lookup from provider data
            dni, dhi = self._lookup_dni_dhi(t)

            # Rolling engineered features (on accumulated history so far)
            hist = ghi_history
            ghi_r3 = float(np.mean(hist[-3:])) if len(hist) >= 1 else 0.0
            ghi_r6 = float(np.mean(hist[-6:])) if len(hist) >= 1 else 0.0
            ghi_var = float(np.std(hist[-3:])) if len(hist) >= 2 else 0.0

            # Temporal features
            hour_frac = t.hour + t.minute / 60.0
            doy = t.timetuple().tm_yday
            iso_week = t.isocalendar()[1]

            row = [
                ghi, dni, dhi, temp,
                ghi_r3, ghi_r6, ghi_var,
                math.sin(2 * math.pi * hour_frac / 24.0),
                math.cos(2 * math.pi * hour_frac / 24.0),
                math.sin(2 * math.pi * doy / 365.25),
                math.cos(2 * math.pi * doy / 365.25),
                math.sin(2 * math.pi * t.month / 12.0),
                math.cos(2 * math.pi * t.month / 12.0),
                math.sin(2 * math.pi * iso_week / 52.0),
                math.cos(2 * math.pi * iso_week / 52.0),
            ]
            rows.append(row)

        if len(rows) < self.lookback:
            return None

        return np.array(rows, dtype=np.float32)

    def _lookup_dni_dhi(self, timestamp: datetime) -> Tuple[float, float]:
        """
        Look up DNI and DHI from the provider's underlying DataFrame.

        Falls back to estimating from GHI if columns are missing.
        """
        try:
            mapped_ts = self.provider._map_to_available_year(timestamp)
            pd_ts = pd.Timestamp(mapped_ts)
            idx = self.provider.data.index.get_indexer(
                [pd_ts], method='nearest', tolerance=pd.Timedelta('1h'))

            if idx[0] != -1:
                row = self.provider.data.iloc[idx[0]]
                dni = float(row.get('DNI', 0.0))
                dhi = float(row.get('DHI', 0.0))
                return (max(0.0, dni), max(0.0, dhi))
        except Exception:
            pass

        return (0.0, 0.0)

    def _empty_forecast(self, timestamp: datetime) -> Dict[str, float]:
        """Return a zero forecast when lookback data is unavailable."""
        result = {'timestamp': timestamp, 'uncertainty': 1.0}
        for horizon in self.horizons:
            key = f'ghi_{horizon}h'
            result[key] = 0.0
            result[f'{key}_upper'] = 0.0
            result[f'{key}_lower'] = 0.0
        return result


# =============================================================================
# UTILITY — PRINT METRICS TABLE
# =============================================================================

def print_metrics(metrics: Dict[str, Any]) -> None:
    """Pretty-print evaluation metrics to console."""
    print("\n" + "=" * 60)
    print("  Solar Forecast Evaluation Metrics")
    print("=" * 60)

    # Per-horizon table
    print(f"\n{'Horizon':>10} {'RMSE (W/m2)':>12} {'MAE (W/m2)':>12} "
          f"{'MAPE (%)':>10}")
    print("-" * 48)
    for h_key, h_metrics in metrics.get('horizons', {}).items():
        print(f"{h_key:>10} {h_metrics['rmse']:>12.1f} "
              f"{h_metrics['mae']:>12.1f} {h_metrics['mape']:>10.1f}")

    # Seasonal table
    seasonal = metrics.get('seasonal', {})
    if seasonal:
        print(f"\n{'Season':>10} {'1h RMSE':>10} {'6h RMSE':>10} "
              f"{'24h RMSE':>10}")
        print("-" * 44)
        for season, s_metrics in seasonal.items():
            print(f"{season:>10} "
                  f"{s_metrics.get('1h', {}).get('rmse', 0):>10.1f} "
                  f"{s_metrics.get('6h', {}).get('rmse', 0):>10.1f} "
                  f"{s_metrics.get('24h', {}).get('rmse', 0):>10.1f}")

    print(f"\nTotal test samples: {metrics.get('total_samples', 'N/A')}")
    print("=" * 60 + "\n")
