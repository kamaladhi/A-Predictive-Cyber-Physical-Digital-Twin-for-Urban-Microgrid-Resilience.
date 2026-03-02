import os
import glob
import logging
import math
from typing import Optional, Dict, Any, List, Tuple

import numpy as np
import pandas as pd

try:
    import pvlib
    _PVLIB_AVAILABLE = True
except ImportError:  # pragma: no cover
    pvlib = None  # type: ignore[assignment]
    _PVLIB_AVAILABLE = False

try:
    from .physics_utils import (get_solar_position, get_clearsky_ghi,
                                 calculate_clearness_index)
except ImportError:
    from physics_utils import (get_solar_position, get_clearsky_ghi,
                                calculate_clearness_index)

logger = logging.getLogger(__name__)

# Physical validation thresholds
MAX_GHI_W_M2  = 1400
MAX_DNI_W_M2  = 1100
MAX_DHI_W_M2  = 800
MAX_TEMP_C    = 55
MIN_TEMP_C    = -10
MAX_GAP_HOURS = 3

# Site coordinates used for pvlib clear-sky calculation
_LATITUDE  = 22.20
_LONGITUDE = 78.47

# ── Feature column definitions (canonical order) ────────────────────────────
PHYSICAL_FEATURES  = ['GHI', 'DNI', 'DHI', 'Temperature']
GEOMETRIC_FEATURES = ['zenith', 'elevation', 'cos_zenith', 'sin_zenith', 'airmass',
                      'ghi_cs', 'kt', 'kt_smooth']
CLEARSKY_FEATURES  = ['ghi_clear', 'ghi_norm',
                      'dni_clear', 'dhi_clear', 'dni_ratio', 'dhi_ratio']
LAG_FEATURES       = ['ghi_lag1', 'ghi_lag2', 'ghi_lag3',
                      'ghi_lag6', 'ghi_lag12', 'ghi_lag24',
                      'kt_lag1', 'kt_lag3', 'kt_lag6', 'kt_lag24',
                      'dni_ratio_lag1', 'dhi_ratio_lag1']
ROLLING_FEATURES   = ['ghi_roll3_mean', 'ghi_roll6_mean', 'ghi_roll12_mean',
                      'ghi_roll3_std',  'ghi_roll6_std', 'cloud_var',
                      'cloud_roll3_mean', 'cloud_roll3_std']
TEMPORAL_FEATURES  = ['hour_sin', 'hour_cos', 'doy_sin', 'doy_cos',
                      'month_sin', 'month_cos', 'is_monsoon']
WEATHER_FEATURES   = ['wind_speed', 'pressure', 'humidity',
                      'cloud_type', 'temp_delta', 'humidity_x_cloud']
PERSISTENCE_FEATURES = ['cloud_lag1', 'humidity_lag1', 'ghi_ramp',
                        'ghi_diff1', 'ghi_diff3', 'kt_diff1',
                        'cloud_diff', 'humidity_diff', 'wind_diff']

ALL_FEATURES = (PHYSICAL_FEATURES + GEOMETRIC_FEATURES + CLEARSKY_FEATURES
                + LAG_FEATURES + ROLLING_FEATURES + TEMPORAL_FEATURES
                + WEATHER_FEATURES + PERSISTENCE_FEATURES)
FEATURE_COLUMNS = ALL_FEATURES
NUM_FEATURES = len(FEATURE_COLUMNS)   # 62


# =============================================================================
# File Loading
# =============================================================================

def load_nsrdb_file(filepath: str) -> pd.DataFrame:
    """
    Load a single NSRDB CSV file.

    NSRDB CSVs layout:
      Row 0 : source metadata
      Row 1 : location metadata
      Row 2 : column names  (Year, Month, Day, Hour, Minute, GHI, DNI, ...)
      Row 3+: data
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"NSRDB file not found: {filepath}")

    logger.info("Loading NSRDB file: %s", os.path.basename(filepath))
    df = pd.read_csv(filepath, skiprows=2, low_memory=False)

    required_cols = ['Year', 'Month', 'Day', 'Hour', 'Minute',
                     'GHI', 'DNI', 'DHI', 'Temperature']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"NSRDB file missing required columns: {missing}")

    df['datetime'] = pd.to_datetime(
        df[['Year', 'Month', 'Day', 'Hour', 'Minute']].astype(int))
    df.set_index('datetime', inplace=True)
    df.index.name = 'timestamp'

    rename_map = {'Wind Speed': 'Wind_Speed',
                  'Relative Humidity': 'Relative_Humidity',
                  'Cloud Type': 'Cloud_Type'}
    df.rename(columns=rename_map, inplace=True)

    keep_cols = [c for c in ['GHI', 'DNI', 'DHI', 'Temperature',
                              'Wind_Speed', 'Pressure',
                              'Relative_Humidity', 'Cloud_Type']
                 if c in df.columns]
    df = df[keep_cols]

    for col in ['GHI', 'DNI', 'DHI', 'Temperature']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    logger.info("  Loaded %d rows, %s -> %s",
                len(df), df.index.min(), df.index.max())
    return df


def load_multi_year(data_dir: str, pattern: str = "*.csv") -> pd.DataFrame:
    """Load and concatenate all NSRDB CSV files in a directory."""
    files = sorted(glob.glob(os.path.join(data_dir, pattern)))
    if not files:
        raise FileNotFoundError(
            f"No NSRDB files found in: {data_dir} (pattern={pattern})")

    logger.info("Found %d NSRDB files in %s", len(files), data_dir)
    frames = []
    for fp in files:
        try:
            frames.append(load_nsrdb_file(fp))
        except Exception as exc:
            logger.warning("Skipping %s: %s", fp, exc)

    if not frames:
        raise ValueError("No valid NSRDB files could be loaded.")

    combined = pd.concat(frames)
    n_before = len(combined)
    combined = combined[~combined.index.duplicated(keep='first')]
    if (n_dup := n_before - len(combined)) > 0:
        logger.info("  Removed %d duplicate timestamps", n_dup)

    combined.sort_index(inplace=True)
    logger.info("Combined: %d rows, %s → %s",
                len(combined), combined.index.min(), combined.index.max())
    return combined


# =============================================================================
# Cleaning
# =============================================================================

def clean_irradiance_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate and clean raw NSRDB data.

    Steps:
      1. Clamp negative / over-physical irradiance.
      2. Force nighttime to zero (GHI < 1 → all irradiance = 0).
      3. Linearly interpolate short gaps (≤3 h).
      4. Forward-fill residual long gaps (with warning).
      5. Clamp temperature.
    """
    df = df.copy()

    for col, max_val in [('GHI', MAX_GHI_W_M2),
                         ('DNI', MAX_DNI_W_M2),
                         ('DHI', MAX_DHI_W_M2)]:
        if col in df.columns:
            n_neg  = int((df[col] < 0).sum())
            n_high = int((df[col] > max_val).sum())
            if n_neg:
                logger.info("  %s: clamped %d negative values", col, n_neg)
            if n_high:
                logger.info("  %s: clamped %d values > %d", col, n_high, max_val)
            df[col] = df[col].clip(lower=0, upper=max_val)

    irr_cols = [c for c in ['GHI', 'DNI', 'DHI'] if c in df.columns]
    if 'GHI' in df.columns and irr_cols:
        night = df['GHI'] < 1.0
        df.loc[night, irr_cols] = 0.0

    for col in ['GHI', 'DNI', 'DHI', 'Temperature']:
        if col not in df.columns:
            continue
        n_miss = int(df[col].isna().sum())
        if n_miss == 0:
            continue
        is_na      = df[col].isna()
        gap_id     = (is_na != is_na.shift()).cumsum()
        gap_len    = is_na.groupby(gap_id).transform('sum')
        short_mask = is_na & (gap_len <= MAX_GAP_HOURS)
        if short_mask.any():
            df[col] = df[col].interpolate(method='linear', limit=MAX_GAP_HOURS)
            logger.info("  %s: interpolated %d values in short gaps",
                        col, int(short_mask.sum()))
        remaining = int(df[col].isna().sum())
        if remaining:
            logger.warning("  %s: %d values in long gaps — forward-filling",
                           col, remaining)
            df[col] = df[col].ffill().bfill()

    if 'Temperature' in df.columns:
        df['Temperature'] = df['Temperature'].clip(MIN_TEMP_C, MAX_TEMP_C)

    logger.info("Cleaning complete: %d rows", len(df))
    return df


# =============================================================================
# Resampling
# =============================================================================

def resample_to_interval(df: pd.DataFrame, freq: str = '5min') -> pd.DataFrame:
    """Resample hourly NSRDB data to a finer interval via linear interpolation."""
    new_index    = pd.date_range(df.index.min(), df.index.max(), freq=freq)
    df_resampled = df.reindex(new_index)

    irr_cols   = [c for c in ['GHI', 'DNI', 'DHI'] if c in df.columns]
    other_cols = [c for c in ['Temperature', 'Wind_Speed',
                               'Pressure', 'Relative_Humidity']
                  if c in df.columns]

    for col in irr_cols + other_cols:
        df_resampled[col] = df_resampled[col].interpolate('linear')

    for col in irr_cols:
        df_resampled.loc[df_resampled[col] < 1.0, col] = 0.0

    if 'Cloud_Type' in df_resampled.columns:
        df_resampled['Cloud_Type'] = df_resampled['Cloud_Type'].ffill()

    df_resampled.index.name = 'timestamp'
    logger.info("Resampled to %s: %d rows", freq, len(df_resampled))
    return df_resampled


# =============================================================================
# Feature Engineering  (v3 — 42 features)
# =============================================================================

def add_research_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Full feature engineering pipeline for the solar forecasting model.

    Input  : df with DatetimeIndex and columns [GHI, DNI, DHI, Temperature]
    Output : df with FEATURE_COLUMNS (42 columns) added.

    IMPORTANT: Call this on the FULL multi-year dataframe before splitting.
    Rolling and lag computations require the complete time series. The
    RobustFeatureScaler must then be fit ONLY on the train split.

    Features added
    --------------
    Geometric (6): zenith, elevation, cos_zenith, ghi_cs, kt, kt_smooth
    ClearSky  (2): ghi_clear, ghi_norm
    Lag       (9): GHI lags at 1/2/3/6/12/24h; kt lags at 1/6/24h
    Rolling   (6): rolling mean 3/6/12h; rolling std 3/6h; cloud_var
    Temporal  (7): hour/doy/month sin+cos; is_monsoon binary
    Weather   (6): wind_speed, pressure, humidity, cloud_type, temp_delta,
                   humidity_x_cloud
    """
    df = df.copy()

    # 1. Solar geometry
    solpos = get_solar_position(df.index)
    df['zenith']    = solpos['zenith'].values.clip(0, 90)
    df['elevation'] = solpos['elevation'].values
    df['azimuth']   = solpos['azimuth'].values   # kept for reference, not in model
    df['cos_zenith'] = np.cos(np.deg2rad(df['zenith']))
    df['sin_zenith'] = np.sin(np.deg2rad(df['zenith']))
    
    # Airmass approx (simplified Kasten-Young)
    z_rad = np.deg2rad(df['zenith'])
    df['airmass'] = 1.0 / (np.cos(z_rad) + 0.50572 * (96.07995 - df['zenith'])**-1.6364)
    df['airmass'] = df['airmass'].clip(1.0, 40.0)

    # 2. Clear-sky and clearness index
    ghi_cs = get_clearsky_ghi(df.index)
    df['ghi_cs']    = ghi_cs.values.clip(0, 1400)
    df['kt']        = calculate_clearness_index(df['GHI'], df['ghi_cs']).clip(0, 1.2)
    df['kt_smooth'] = df['kt'].rolling(3, min_periods=1, center=True).mean()

    # 2b. pvlib clear-sky GHI/DNI/DHI and normalised ratios
    if _PVLIB_AVAILABLE:
        location = pvlib.location.Location(
            latitude=_LATITUDE, longitude=_LONGITUDE, tz='UTC')
        idx_utc = df.index.tz_localize('UTC') if df.index.tz is None else df.index.tz_convert('UTC')
        cs = location.get_clearsky(idx_utc)           # Ineichen model
        df['ghi_clear'] = cs['ghi'].values.clip(0, 1400)
        df['dni_clear'] = cs['dni'].values.clip(0, 1100)
        df['dhi_clear'] = cs['dhi'].values.clip(0, 800)
    else:
        logger.warning("pvlib not available; using physics_utils clear-sky")
        df['ghi_clear'] = df['ghi_cs']
        df['dni_clear'] = df['ghi_cs'] * 0.7   # rough decomposition
        df['dhi_clear'] = df['ghi_cs'] * 0.3
    df['ghi_norm']  = df['GHI'] / (df['ghi_clear'] + 1e-6)
    df['dni_ratio'] = df['DNI'] / (df['dni_clear'] + 1e-6)
    df['dhi_ratio'] = df['DHI'] / (df['dhi_clear'] + 1e-6)

    # 3. Lag features (strictly causal — shift(+n) looks n steps back)
    for lag, col in [(1,  'ghi_lag1'),  (2,  'ghi_lag2'),  (3,  'ghi_lag3'),
                     (6,  'ghi_lag6'),  (12, 'ghi_lag12'), (24, 'ghi_lag24')]:
        df[col] = df['GHI'].shift(lag).fillna(0.0)

    for lag, col in [(1, 'kt_lag1'), (3, 'kt_lag3'), (6, 'kt_lag6'), (24, 'kt_lag24')]:
        df[col] = df['kt'].shift(lag).fillna(0.0)
        
    for lag, col in [(1, 'dni_ratio_lag1')]:
        df[col] = df['dni_ratio'].shift(lag).fillna(0.0)
        
    for lag, col in [(1, 'dhi_ratio_lag1')]:
        df[col] = df['dhi_ratio'].shift(lag).fillna(0.0)

    # 4. Rolling statistics (causal — right-aligned window)
    df['ghi_roll3_mean']  = df['GHI'].rolling(3,  min_periods=1).mean()
    df['ghi_roll6_mean']  = df['GHI'].rolling(6,  min_periods=1).mean()
    df['ghi_roll12_mean'] = df['GHI'].rolling(12, min_periods=1).mean()
    df['ghi_roll3_std']   = df['GHI'].rolling(3,  min_periods=2).std().fillna(0.0)
    df['ghi_roll6_std']   = df['GHI'].rolling(6,  min_periods=2).std().fillna(0.0)
    df['cloud_var']       = (
        (df['GHI'] - df['ghi_roll3_mean']).abs()
        / (df['ghi_cs'] + 1.0)
    ).clip(0.0, 1.0)
    
    # Cloud type is available after step 6 (weather features), but we'll compute it 
    # here since we just pulled the raw column safely
    raw_cloud = df['Cloud_Type'].fillna(0.0).astype(float) if 'Cloud_Type' in df.columns else pd.Series(0.0, index=df.index)
    df['cloud_roll3_mean'] = raw_cloud.rolling(3, min_periods=1).mean()
    df['cloud_roll3_std']  = raw_cloud.rolling(3, min_periods=2).std().fillna(0.0)

    # 5. Cyclic temporal encoding
    hours = df.index.hour + df.index.minute / 60.0
    df['hour_sin']  = np.sin(2 * np.pi * hours / 24.0)
    df['hour_cos']  = np.cos(2 * np.pi * hours / 24.0)

    doy = df.index.dayofyear.astype(float)
    df['doy_sin']   = np.sin(2 * np.pi * doy / 365.25)
    df['doy_cos']   = np.cos(2 * np.pi * doy / 365.25)

    month = df.index.month.astype(float)
    df['month_sin'] = np.sin(2 * np.pi * month / 12.0)
    df['month_cos'] = np.cos(2 * np.pi * month / 12.0)

    # Monsoon seasonal indicator (JJA — Indian Summer Monsoon)
    df['is_monsoon'] = df.index.month.isin([6, 7, 8]).astype(float)

    # 6. Weather features (from NSRDB columns if available)
    df['wind_speed'] = df['Wind_Speed'].fillna(0.0) if 'Wind_Speed' in df.columns else 0.0
    df['pressure']   = df['Pressure'].fillna(950.0) if 'Pressure' in df.columns else 950.0
    df['humidity']   = df['Relative_Humidity'].fillna(50.0) if 'Relative_Humidity' in df.columns else 50.0
    df['cloud_type'] = df['Cloud_Type'].fillna(0.0).astype(float) if 'Cloud_Type' in df.columns else 0.0
    df['temp_delta'] = df['Temperature'].diff(1).fillna(0.0)
    df['humidity_x_cloud'] = (df['humidity'] / 100.0) * (df['cloud_type'].clip(0, 10) / 10.0)

    # 7. Persistence / dynamics features (cloud & weather persistence)
    df['cloud_lag1']    = df['cloud_type'].shift(1).fillna(0.0)
    df['humidity_lag1'] = df['humidity'].shift(1).fillna(50.0)
    df['ghi_ramp']      = df['GHI'].diff(1).abs().fillna(0.0)
    
    # Diff features
    df['ghi_diff1']     = df['GHI'].diff(1).fillna(0.0)
    df['ghi_diff3']     = df['GHI'].diff(3).fillna(0.0)
    df['kt_diff1']      = df['kt'].diff(1).fillna(0.0)
    df['cloud_diff']    = df['cloud_type'].diff(1).fillna(0.0)
    df['humidity_diff'] = df['humidity'].diff(1).fillna(0.0)
    df['wind_diff']     = df['wind_speed'].diff(1).fillna(0.0)

    # Drop rows with NaN introduced by lag shifts (head of series)
    pre_drop = len(df)
    df.dropna(subset=['ghi_lag1', 'ghi_lag3', 'ghi_lag6'], inplace=True)
    if (n_dropped := pre_drop - len(df)) > 0:
        logger.info("  Dropped %d NaN rows from lag features", n_dropped)

    missing = [c for c in FEATURE_COLUMNS if c not in df.columns]
    if missing:
        raise RuntimeError(f"Feature engineering failed — missing: {missing}")

    print("Features added:", df.shape)
    logger.info("Features added: %d rows × %d features", len(df), NUM_FEATURES)
    return df


# =============================================================================
# Daytime Mask
# =============================================================================

def get_daytime_mask(df: pd.DataFrame,
                     elevation_col: str = 'elevation',
                     min_elevation_deg: float = 3.0) -> pd.Series:
    """
    Boolean mask for daytime rows (solar elevation > min_elevation_deg).

    Why 3° and not 0° or GHI > 10?
      - 0° includes civil twilight where GHI ≈ 5–20 W/m². Any non-zero
        prediction gives MAPE > 100%, inflating reported MAPE by 30–50 pp.
      - GHI > 10 W/m² threshold still retains some twilight rows.
      - 3° elevation corresponds to GHI ≈ 50 W/m² at this latitude, which
        aligns with WMO solar forecasting benchmark standards.

    Call add_research_features() before calling this function.
    """
    if elevation_col not in df.columns:
        raise ValueError(f"Column '{elevation_col}' not found. "
                         "Call add_research_features() first.")
    return df[elevation_col] > min_elevation_deg


# =============================================================================
# Train / Val / Test Split
# =============================================================================

def temporal_split(
        df: pd.DataFrame,
        train_years: List[int] = (2018, 2019),
        val_years:   List[int] = (2019,),
        test_years:  List[int] = (2020,),
        val_months:  List[int] = None
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Temporal split supporting overlapping years via month filtering.

    If val_months is provided and val_years overlaps with train_years,
    val_months dictates the validation portion, and the rest of that year
    goes to training.
    """
    if val_months is not None:
        train_mask = df.index.year.isin(train_years)
        # If a year is in both train and val, exclude val_months from train
        overlap_mask = df.index.year.isin(set(train_years).intersection(val_years))
        train_mask = train_mask & ~(overlap_mask & df.index.month.isin(val_months))
        
        val_mask = df.index.year.isin(val_years) & df.index.month.isin(val_months)
    else:
        train_mask = df.index.year.isin(train_years)
        val_mask   = df.index.year.isin(val_years)

    test_mask = df.index.year.isin(test_years)

    train = df[train_mask].copy()
    val   = df[val_mask].copy()
    test  = df[test_mask].copy()
    
    logger.info("Temporal split — train: %d  val: %d  test: %d rows",
                len(train), len(val), len(test))
    return train, val, test


# =============================================================================
# Robust Feature Scaler
# =============================================================================

class RobustFeatureScaler:
    """
    Per-feature robust scaler: z = (x - median) / IQR.

    Handles zero-IQR features (constant or near-constant columns) by setting
    scale to 1.0 (no rescaling for those features).

    Must be fit ONLY on the training split to prevent data leakage.
    """

    def __init__(self):
        self.median_:        Optional[np.ndarray] = None
        self.iqr_:           Optional[np.ndarray] = None
        self.feature_names_: Optional[List[str]]  = None

    def fit(self, X: np.ndarray,
            feature_names: Optional[List[str]] = None) -> 'RobustFeatureScaler':
        self.median_        = np.median(X, axis=0)
        q75                 = np.percentile(X, 75, axis=0)
        q25                 = np.percentile(X, 25, axis=0)
        iqr                 = q75 - q25
        iqr[iqr < 1e-8]    = 1.0
        self.iqr_           = iqr
        self.feature_names_ = feature_names
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.median_) / self.iqr_

    def inverse_transform(self, X: np.ndarray) -> np.ndarray:
        return X * self.iqr_ + self.median_

    def to_dict(self) -> Dict[str, Any]:
        return {
            'scaler_type': 'robust_v2',
            'median':      self.median_.tolist(),
            'iqr':         self.iqr_.tolist(),
            'features':    self.feature_names_ or [],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'RobustFeatureScaler':
        s = cls()
        s.median_        = np.array(d['median'])
        s.iqr_           = np.array(d['iqr'])
        s.feature_names_ = d.get('features')
        return s


# =============================================================================
# Data Quality Report
# =============================================================================

def get_data_quality_report(df: pd.DataFrame) -> Dict[str, Any]:
    """Generate a data quality summary dict."""
    report: Dict[str, Any] = {
        'total_rows':  len(df),
        'date_range':  {'start': str(df.index.min()), 'end': str(df.index.max())},
        'years_covered': sorted(df.index.year.unique().tolist()),
        'time_resolution_hours': (
            df.index.to_series().diff().median().total_seconds() / 3600
            if len(df) > 1 else None),
        'columns': {},
    }
    for col in ['GHI', 'DNI', 'DHI', 'Temperature']:
        if col not in df.columns:
            continue
        s = df[col]
        report['columns'][col] = {
            'missing_count':   int(s.isna().sum()),
            'missing_percent': round(s.isna().mean() * 100, 2),
            'min':   round(float(s.min()),  2),
            'max':   round(float(s.max()),  2),
            'mean':  round(float(s.mean()), 2),
            'std':   round(float(s.std()),  2),
        }
    if 'GHI' in df.columns:
        dt_h  = (df.index.to_series().diff().median().total_seconds() / 3600
                 if len(df) > 1 else 1.0)
        daily = df['GHI'].resample('D').sum() * dt_h / 1000
        report['daily_ghi_kwh_m2'] = {
            'mean': round(float(daily.mean()), 2),
            'min':  round(float(daily.min()),  2),
            'max':  round(float(daily.max()),  2),
            'std':  round(float(daily.std()),  2),
        }
    return report


def print_quality_report(report: Dict[str, Any]) -> None:
    """Pretty-print the data quality report."""
    print("\n" + "=" * 60)
    print("  NSRDB Solar Data — Quality Report")
    print("=" * 60)
    print(f"  Rows:        {report['total_rows']}")
    print(f"  Date range:  {report['date_range']['start']} — "
          f"{report['date_range']['end']}")
    print(f"  Years:       {report['years_covered']}")
    print(f"  Resolution:  {report.get('time_resolution_hours', '?')} h")
    print("-" * 60)
    for col, s in report.get('columns', {}).items():
        print(f"  {col}:")
        print(f"    Range:   [{s['min']}, {s['max']}]")
        print(f"    Mean:    {s['mean']}  (σ={s['std']})")
        print(f"    Missing: {s['missing_count']} ({s['missing_percent']}%)")
    if 'daily_ghi_kwh_m2' in report:
        d = report['daily_ghi_kwh_m2']
        print("-" * 60)
        print(f"  Daily GHI (kWh/m²/day): "
              f"mean={d['mean']}  min={d['min']}  max={d['max']}  σ={d['std']}")
    print("=" * 60 + "\n")
