"""
NSRDB Solar Irradiance Data — Preprocessing Pipeline
=====================================================

Loads, cleans, and resamples NSRDB (National Solar Radiation Database) CSV
datasets for use in microgrid simulation.

Data source: NSRDB PSM v3 (https://nsrdb.nrel.gov/)
Location: Lat 22.20°N, Lon 78.47°E (Madhya Pradesh, India)
Resolution: Hourly, 2018–2020

Key assumptions & limitations:
  - NSRDB data is satellite-derived; ground-truth GHI may differ by ±5–10%.
  - Missing values (≤3 consecutive hours) are linearly interpolated;
    longer gaps are flagged and forward-filled with last valid observation.
  - GHI values outside [0, 1400] W/m² are clamped (physical upper bound
    is ~1361 W/m² extraterrestrial, surface values rarely exceed 1200).
  - Nighttime irradiance is forced to zero to prevent interpolation artifacts.
  - Resampling from 1-hour to sub-hourly uses linear interpolation, which
    smooths out intra-hour cloud transients — acceptable for 5-min simulation
    but not for second-level power quality studies.
"""

import os
import glob
import logging
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Physical constants & validation thresholds
# ──────────────────────────────────────────────────────────────────────────────
MAX_GHI_W_M2 = 1400   # Physical ceiling for surface GHI
MAX_DNI_W_M2 = 1100   # Practical ceiling for DNI
MAX_DHI_W_M2 = 800    # Practical ceiling for DHI
MAX_TEMP_C = 55        # Sanity check for ambient temperature
MIN_TEMP_C = -10       # Sanity check (tropical India)
MAX_GAP_HOURS = 3      # Maximum gap for linear interpolation


def load_nsrdb_file(filepath: str) -> pd.DataFrame:
    """
    Load a single NSRDB CSV file into a clean DataFrame.

    NSRDB CSVs have a 2-line header:
      Row 0: Metadata (source, location, units, version)
      Row 1: Column names (Year, Month, Day, Hour, Minute, DNI, DHI, GHI, ...)
    Data starts from row 2.

    Parameters
    ----------
    filepath : str
        Absolute or relative path to an NSRDB CSV file.

    Returns
    -------
    pd.DataFrame
        DataFrame indexed by DatetimeIndex (UTC+5:30 IST) with columns:
        GHI, DNI, DHI, Temperature, Wind_Speed, Pressure,
        Relative_Humidity, Cloud_Type.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"NSRDB file not found: {filepath}")

    # Read metadata line for context (optional logging)
    with open(filepath, 'r') as f:
        metadata_line = f.readline().strip()
    logger.info(f"Loading NSRDB file: {os.path.basename(filepath)}")
    logger.debug(f"Metadata: {metadata_line[:120]}...")

    # Read data: skip 2 metadata rows (rows 0-1), use row 2 as header
    # NSRDB CSVs have: row 0 = source metadata, row 1 = location metadata,
    # row 2 = column names (Year, Month, Day, Hour, Minute, ...)
    df = pd.read_csv(filepath, skiprows=2, low_memory=False)

    # Validate expected columns exist
    required_cols = ['Year', 'Month', 'Day', 'Hour', 'Minute',
                     'GHI', 'DNI', 'DHI', 'Temperature']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"NSRDB file missing required columns: {missing}")

    # Construct datetime index from individual columns
    df['datetime'] = pd.to_datetime(
        df[['Year', 'Month', 'Day', 'Hour', 'Minute']].astype(int)
    )
    df.set_index('datetime', inplace=True)
    df.index.name = 'timestamp'

    # Rename columns for internal consistency
    rename_map = {
        'Wind Speed': 'Wind_Speed',
        'Relative Humidity': 'Relative_Humidity',
        'Cloud Type': 'Cloud_Type',
    }
    df.rename(columns=rename_map, inplace=True)

    # Keep only relevant columns (drop Year/Month/Day/Hour/Minute)
    keep_cols = ['GHI', 'DNI', 'DHI', 'Temperature', 'Wind_Speed',
                 'Pressure', 'Relative_Humidity', 'Cloud_Type']
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols]

    # Ensure numeric types
    for col in ['GHI', 'DNI', 'DHI', 'Temperature']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    logger.info(f"  Loaded {len(df)} rows, date range: "
                f"{df.index.min()} to {df.index.max()}")
    return df


def load_multi_year(data_dir: str,
                    pattern: str = "*.csv") -> pd.DataFrame:
    """
    Load and concatenate multiple NSRDB CSV files from a directory.

    Parameters
    ----------
    data_dir : str
        Path to directory containing NSRDB CSV files.
    pattern : str
        Glob pattern for matching files (default: *.csv).

    Returns
    -------
    pd.DataFrame
        Combined DataFrame sorted by timestamp, duplicates removed.
    """
    search_path = os.path.join(data_dir, pattern)
    files = sorted(glob.glob(search_path))

    if not files:
        raise FileNotFoundError(
            f"No NSRDB files found matching: {search_path}")

    logger.info(f"Found {len(files)} NSRDB files in {data_dir}")

    frames = []
    for fp in files:
        try:
            df = load_nsrdb_file(fp)
            frames.append(df)
        except Exception as e:
            logger.warning(f"Skipping file {fp}: {e}")

    if not frames:
        raise ValueError("No valid NSRDB files could be loaded")

    combined = pd.concat(frames, axis=0)

    # Remove duplicate timestamps (e.g., overlapping year boundaries)
    n_before = len(combined)
    combined = combined[~combined.index.duplicated(keep='first')]
    n_removed = n_before - len(combined)
    if n_removed > 0:
        logger.info(f"  Removed {n_removed} duplicate timestamps")

    combined.sort_index(inplace=True)

    logger.info(f"Combined dataset: {len(combined)} rows, "
                f"{combined.index.min()} to {combined.index.max()}")
    return combined


def clean_irradiance_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate and clean irradiance data.

    Cleaning steps:
      1. Clamp negative irradiance values to zero.
      2. Clamp values exceeding physical maximums.
      3. Force nighttime irradiance to zero (GHI < 1 W/m² → set all to 0).
      4. Interpolate short gaps (≤3 hours) linearly.
      5. Forward-fill remaining gaps (with warning).
      6. Validate temperature range.

    Parameters
    ----------
    df : pd.DataFrame
        Raw NSRDB data from load_nsrdb_file / load_multi_year.

    Returns
    -------
    pd.DataFrame
        Cleaned copy of input DataFrame.
    """
    df = df.copy()
    n_rows = len(df)

    # Track original missing counts for reporting
    missing_before = {col: df[col].isna().sum()
                      for col in ['GHI', 'DNI', 'DHI', 'Temperature']
                      if col in df.columns}

    # --- Step 1 & 2: Clamp irradiance values ---
    for col, max_val in [('GHI', MAX_GHI_W_M2),
                         ('DNI', MAX_DNI_W_M2),
                         ('DHI', MAX_DHI_W_M2)]:
        if col in df.columns:
            n_neg = (df[col] < 0).sum()
            n_high = (df[col] > max_val).sum()
            if n_neg > 0:
                logger.info(f"  {col}: clamped {n_neg} negative values to 0")
            if n_high > 0:
                logger.info(f"  {col}: clamped {n_high} values > {max_val}")
            df[col] = df[col].clip(lower=0, upper=max_val)

    # --- Step 3: Force nighttime to zero ---
    # If GHI is near-zero (< 1), set DNI and DHI to zero as well
    if all(c in df.columns for c in ['GHI', 'DNI', 'DHI']):
        night_mask = df['GHI'] < 1.0
        df.loc[night_mask, ['GHI', 'DNI', 'DHI']] = 0.0

    # --- Step 4 & 5: Handle missing values ---
    for col in ['GHI', 'DNI', 'DHI', 'Temperature']:
        if col not in df.columns:
            continue

        n_missing = df[col].isna().sum()
        if n_missing == 0:
            continue

        # Identify gap lengths
        is_na = df[col].isna()
        gap_groups = (is_na != is_na.shift()).cumsum()
        gap_lengths = is_na.groupby(gap_groups).transform('sum')

        # Short gaps: linear interpolation
        short_gap_mask = is_na & (gap_lengths <= MAX_GAP_HOURS)
        if short_gap_mask.any():
            df[col] = df[col].interpolate(method='linear',
                                          limit=MAX_GAP_HOURS)
            logger.info(f"  {col}: interpolated {short_gap_mask.sum()} "
                        f"values in gaps ≤ {MAX_GAP_HOURS}h")

        # Long gaps: forward-fill + backward-fill edges
        remaining = df[col].isna().sum()
        if remaining > 0:
            logger.warning(f"  {col}: {remaining} values in long gaps — "
                           f"forward-filling (review recommended)")
            df[col] = df[col].ffill().bfill()

    # --- Step 6: Temperature validation ---
    if 'Temperature' in df.columns:
        df['Temperature'] = df['Temperature'].clip(
            lower=MIN_TEMP_C, upper=MAX_TEMP_C)

    # Final report
    missing_after = {col: df[col].isna().sum()
                     for col in ['GHI', 'DNI', 'DHI', 'Temperature']
                     if col in df.columns}
    logger.info(f"Cleaning complete: {n_rows} rows, "
                f"missing before={missing_before}, after={missing_after}")
    return df


def resample_to_interval(df: pd.DataFrame,
                         freq: str = '5min') -> pd.DataFrame:
    """
    Resample hourly NSRDB data to a finer interval via linear interpolation.

    Nighttime values (GHI == 0 in the source) are preserved as zero —
    interpolation only occurs between nonzero daytime values.

    Parameters
    ----------
    df : pd.DataFrame
        Cleaned NSRDB data at hourly resolution.
    freq : str
        Target frequency (default '5min' to match simulator timestep).

    Returns
    -------
    pd.DataFrame
        Resampled DataFrame at the specified frequency.

    Note
    ----
    Linear interpolation of hourly data smooths out intra-hour cloud
    transients. For sub-minute power quality studies, minute-level
    satellite or pyranometer data would be needed.
    """
    # Create the target time index
    new_index = pd.date_range(start=df.index.min(),
                              end=df.index.max(),
                              freq=freq)

    # Reindex and interpolate numeric columns
    df_resampled = df.reindex(new_index)

    irradiance_cols = [c for c in ['GHI', 'DNI', 'DHI'] if c in df.columns]
    other_numeric = [c for c in ['Temperature', 'Wind_Speed', 'Pressure',
                                 'Relative_Humidity']
                     if c in df.columns]

    # Interpolate weather variables linearly
    for col in irradiance_cols + other_numeric:
        df_resampled[col] = df_resampled[col].interpolate(method='linear')

    # Force nighttime irradiance to zero after interpolation
    # (prevents artificial sunrise/sunset ramps at night boundaries)
    for col in irradiance_cols:
        df_resampled.loc[df_resampled[col] < 1.0, col] = 0.0

    # Forward-fill categorical columns
    if 'Cloud_Type' in df_resampled.columns:
        df_resampled['Cloud_Type'] = df_resampled['Cloud_Type'].ffill()

    df_resampled.index.name = 'timestamp'

    logger.info(f"Resampled to {freq}: {len(df_resampled)} rows")
    return df_resampled


def get_data_quality_report(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Generate a data quality summary for research reproducibility.

    Parameters
    ----------
    df : pd.DataFrame
        NSRDB data (raw or cleaned).

    Returns
    -------
    dict
        Summary statistics including coverage, missing %, ranges,
        and daily generation profiles.
    """
    report = {
        'total_rows': len(df),
        'date_range': {
            'start': str(df.index.min()),
            'end': str(df.index.max()),
        },
        'years_covered': sorted(df.index.year.unique().tolist()),
        'time_resolution_hours': (
            df.index.to_series().diff().median().total_seconds() / 3600
            if len(df) > 1 else None
        ),
        'columns': {},
    }

    for col in ['GHI', 'DNI', 'DHI', 'Temperature']:
        if col not in df.columns:
            continue
        series = df[col]
        report['columns'][col] = {
            'missing_count': int(series.isna().sum()),
            'missing_percent': round(
                series.isna().sum() / len(series) * 100, 2),
            'min': round(float(series.min()), 2),
            'max': round(float(series.max()), 2),
            'mean': round(float(series.mean()), 2),
            'std': round(float(series.std()), 2),
        }

    # Daily GHI energy (kWh/m²/day) — key metric for PV sizing validation
    if 'GHI' in df.columns:
        # Determine time step in hours for energy calculation
        if len(df) > 1:
            dt_hours = (df.index.to_series().diff().median().total_seconds()
                        / 3600)
        else:
            dt_hours = 1.0
        daily_ghi = df['GHI'].resample('D').sum() * dt_hours / 1000  # kWh/m²
        report['daily_ghi_kwh_m2'] = {
            'mean': round(float(daily_ghi.mean()), 2),
            'min': round(float(daily_ghi.min()), 2),
            'max': round(float(daily_ghi.max()), 2),
            'std': round(float(daily_ghi.std()), 2),
        }

    return report


def print_quality_report(report: Dict[str, Any]) -> None:
    """Pretty-print a data quality report to the console."""
    print("\n" + "=" * 60)
    print("  NSRDB Solar Data — Quality Report")
    print("=" * 60)
    print(f"  Rows:        {report['total_rows']}")
    print(f"  Date range:  {report['date_range']['start']} -- "
          f"{report['date_range']['end']}")
    print(f"  Years:       {report['years_covered']}")
    print(f"  Resolution:  {report.get('time_resolution_hours', '?')} hours")
    print("-" * 60)

    for col, stats in report.get('columns', {}).items():
        print(f"  {col}:")
        print(f"    Range:   [{stats['min']}, {stats['max']}]")
        print(f"    Mean:    {stats['mean']}  (std = {stats['std']})")
        print(f"    Missing: {stats['missing_count']} "
              f"({stats['missing_percent']}%)")

    if 'daily_ghi_kwh_m2' in report:
        dg = report['daily_ghi_kwh_m2']
        print("-" * 60)
        print(f"  Daily GHI Energy (kWh/m²/day):")
        print(f"    Mean: {dg['mean']}  Min: {dg['min']}  "
              f"Max: {dg['max']}  std: {dg['std']}")

    print("=" * 60 + "\n")
