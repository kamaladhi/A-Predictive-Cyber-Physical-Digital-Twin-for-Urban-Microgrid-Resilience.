"""
PV Power Model — Irradiance-to-Power Conversion
=================================================

Converts GHI (Global Horizontal Irradiance) from NSRDB data into realistic
AC power output for photovoltaic arrays.

Model chain:
  GHI → effective irradiance on tilted plane (simplified: GHI used directly)
     → DC power (nameplate × irradiance ratio × temp derating)
     → AC power (× inverter efficiency × system losses)

Assumptions documented inline. This model is intentionally kept lightweight
for real-time simulation; for detailed plant modeling consider pvlib-python.

Limitations:
  - Uses GHI directly (no POA transposition) — acceptable for near-horizontal
    or south-facing panels in tropical latitudes where tilt ≈ latitude.
  - Single-diode cell physics are not modeled; a linear derating model
    is used instead (adequate for system-level simulation).
  - Shading from surrounding structures is not modeled.
  - Inverter clipping at high irradiance is not modeled (rare for
    properly sized inverters).
"""

import logging
from datetime import datetime, timedelta
from typing import Tuple, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# System loss factors — research-documented defaults
# ──────────────────────────────────────────────────────────────────────────────

# Soiling loss: dust/dirt accumulation on panel surface
# Reference: Kimber et al. (2006), typical 2-5% in semi-arid India
SOILING_LOSS_FACTOR = 0.02

# Annual degradation rate: PV module power output decline per year
# Reference: Jordan & Kurtz (2013), median 0.5%/yr for crystalline-Si
ANNUAL_DEGRADATION_RATE = 0.005

# System wiring / mismatch / connection losses
# Reference: IEC 61724, typical 1-3%
SYSTEM_LOSSES_FACTOR = 0.02

# Standard test conditions irradiance
STC_IRRADIANCE_W_M2 = 1000.0


def calculate_cell_temperature(ghi_w_m2: float,
                               ambient_temp_c: float,
                               noct_c: float = 45.0) -> float:
    """
    Estimate PV cell temperature using the NOCT (Nominal Operating Cell
    Temperature) model.

    Formula: T_cell = T_ambient + (NOCT - 20) × (G / 800)

    This is the standard simplified model used in PVsyst and SAM.
    More accurate than the linear model in the original code (which
    used G/1000 instead of G/800).

    Parameters
    ----------
    ghi_w_m2 : float
        Irradiance on the panel surface (W/m²).
    ambient_temp_c : float
        Ambient air temperature (°C).
    noct_c : float
        Nominal Operating Cell Temperature (°C). Default 45°C.

    Returns
    -------
    float
        Estimated cell temperature (°C).
    """
    if ghi_w_m2 <= 0:
        return ambient_temp_c
    return ambient_temp_c + (noct_c - 20.0) * (ghi_w_m2 / 800.0)


def calculate_pv_power(ghi_w_m2: float,
                       ambient_temp_c: float,
                       pv_config,
                       system_age_years: float = 0.0,
                       soiling_factor: float = SOILING_LOSS_FACTOR,
                       system_losses: float = SYSTEM_LOSSES_FACTOR) -> float:
    """
    Convert GHI irradiance to AC power output for a PV array.

    Model chain:
      P_ac = P_stc × (G / G_stc) × f_temp × η_inv × (1 - f_soil)
             × (1 - f_deg)^age × (1 - f_sys)

    Parameters
    ----------
    ghi_w_m2 : float
        Global Horizontal Irradiance (W/m²).
    ambient_temp_c : float
        Ambient temperature (°C).
    pv_config : PVConfig
        PV system configuration dataclass (from any microgrid's parameters.py).
        Must have: installed_capacity_kwp, temperature_coefficient,
                   nominal_operating_temp, inverter_efficiency.
    system_age_years : float
        Age of the PV system in years (for degradation calculation).
    soiling_factor : float
        Fractional soiling loss (default 0.02 = 2%).
    system_losses : float
        Fractional wiring/mismatch losses (default 0.02 = 2%).

    Returns
    -------
    float
        AC power output in kW (≥ 0).
    """
    if ghi_w_m2 <= 0:
        return 0.0

    # 1. Irradiance ratio
    irradiance_ratio = ghi_w_m2 / STC_IRRADIANCE_W_M2

    # 2. Temperature derating
    cell_temp = calculate_cell_temperature(
        ghi_w_m2, ambient_temp_c,
        noct_c=getattr(pv_config, 'nominal_operating_temp', 45.0)
    )
    temp_coeff = getattr(pv_config, 'temperature_coefficient', -0.004)
    temp_factor = 1.0 + temp_coeff * (cell_temp - 25.0)

    # 3. Inverter efficiency
    inverter_eff = getattr(pv_config, 'inverter_efficiency', 0.97)

    # 4. Degradation factor
    degradation_factor = (1.0 - ANNUAL_DEGRADATION_RATE) ** system_age_years

    # 5. Combined system losses
    loss_factor = (1.0 - soiling_factor) * (1.0 - system_losses)

    # 6. Final AC power
    power_kw = (
        pv_config.installed_capacity_kwp
        * irradiance_ratio
        * temp_factor
        * inverter_eff
        * degradation_factor
        * loss_factor
    )

    return max(0.0, power_kw)


class SolarDataProvider:
    """
    Time-aligned solar data provider for microgrid simulators.

    Wraps a preprocessed NSRDB DataFrame and provides irradiance + temperature
    lookups by timestamp, with optional year-cycling for multi-year simulation.

    Usage:
        >>> from src.solar.solar_preprocessing import load_multi_year, clean_irradiance_data
        >>> from src.solar.pv_power_model import SolarDataProvider
        >>> df = clean_irradiance_data(load_multi_year('Dataset/...'))
        >>> provider = SolarDataProvider(df)
        >>> ghi, temp = provider.get_irradiance(some_datetime)

    Year Cycling:
        If the simulation datetime falls outside the dataset's date range,
        the provider maps the year to the closest available year in the
        dataset. This allows running simulations at arbitrary dates using
        historical data.
    """

    def __init__(self,
                 solar_df: pd.DataFrame,
                 default_temp_c: float = 25.0):
        """
        Parameters
        ----------
        solar_df : pd.DataFrame
            Preprocessed NSRDB data indexed by DatetimeIndex.
            Must contain at least 'GHI' column; 'Temperature' is optional.
        default_temp_c : float
            Default temperature if 'Temperature' column is missing or
            lookup fails.
        """
        if solar_df.empty:
            raise ValueError("Solar data DataFrame is empty")
        if 'GHI' not in solar_df.columns:
            raise ValueError("Solar data must contain 'GHI' column")

        self.data = solar_df.sort_index()
        self.default_temp_c = default_temp_c
        self.has_temperature = 'Temperature' in self.data.columns
        self.available_years = sorted(self.data.index.year.unique().tolist())

        # Pre-compute time resolution for diagnostics
        if len(self.data) > 1:
            self.resolution_seconds = int(
                self.data.index.to_series().diff().median().total_seconds()
            )
        else:
            self.resolution_seconds = 3600

        logger.info(f"SolarDataProvider initialized: {len(self.data)} rows, "
                    f"years {self.available_years}, "
                    f"resolution {self.resolution_seconds}s")

    def _map_to_available_year(self, dt: datetime) -> datetime:
        """
        If the requested year isn't in the dataset, remap to the
        closest available year while preserving month/day/hour/minute.
        """
        if dt.year in self.available_years:
            return dt

        # Find closest available year
        closest_year = min(self.available_years,
                           key=lambda y: abs(y - dt.year))

        try:
            mapped = dt.replace(year=closest_year)
        except ValueError:
            # Handle Feb 29 in non-leap years
            mapped = dt.replace(year=closest_year, month=2, day=28)

        return mapped

    def get_irradiance(self,
                       timestamp: datetime) -> Tuple[float, float]:
        """
        Look up GHI and ambient temperature for a given timestamp.

        Uses nearest-neighbor matching (within 1 hour tolerance).
        If no match, returns (0.0, default_temp_c).

        Parameters
        ----------
        timestamp : datetime
            Simulation timestamp.

        Returns
        -------
        tuple of (float, float)
            (GHI in W/m², ambient temperature in °C)
        """
        mapped_ts = self._map_to_available_year(timestamp)

        # Convert to pandas Timestamp for index matching
        pd_ts = pd.Timestamp(mapped_ts)

        # Find nearest timestamp within tolerance
        try:
            idx = self.data.index.get_indexer([pd_ts], method='nearest',
                                              tolerance=pd.Timedelta('1h'))
            if idx[0] == -1:
                # No match within tolerance — likely a large data gap
                return (0.0, self.default_temp_c)

            row = self.data.iloc[idx[0]]
            ghi = float(row['GHI'])
            temp = (float(row['Temperature'])
                    if self.has_temperature
                    else self.default_temp_c)

            return (max(0.0, ghi), temp)

        except Exception as e:
            logger.debug(f"Irradiance lookup failed for {timestamp}: {e}")
            return (0.0, self.default_temp_c)

    def get_irradiance_series(self,
                              start: datetime,
                              end: datetime,
                              freq: str = '5min') -> pd.DataFrame:
        """
        Get a time series of irradiance data for a date range.

        Useful for batch processing or pre-loading data before simulation.

        Parameters
        ----------
        start, end : datetime
            Start and end of the desired time range.
        freq : str
            Output frequency (default '5min').

        Returns
        -------
        pd.DataFrame
            DataFrame with columns ['GHI', 'Temperature'] at the
            requested frequency.
        """
        timestamps = pd.date_range(start=start, end=end, freq=freq)
        records = []
        for ts in timestamps:
            ghi, temp = self.get_irradiance(ts)
            records.append({'timestamp': ts, 'GHI': ghi,
                            'Temperature': temp})

        result = pd.DataFrame(records)
        result.set_index('timestamp', inplace=True)
        return result

    def summary(self) -> str:
        """Return a human-readable summary of the data provider."""
        lines = [
            f"SolarDataProvider:",
            f"  Rows:       {len(self.data)}",
            f"  Years:      {self.available_years}",
            f"  Resolution: {self.resolution_seconds}s",
            f"  GHI range:  [{self.data['GHI'].min():.0f}, "
            f"{self.data['GHI'].max():.0f}] W/m²",
        ]
        if self.has_temperature:
            lines.append(
                f"  Temp range: [{self.data['Temperature'].min():.1f}, "
                f"{self.data['Temperature'].max():.1f}] °C"
            )
        return "\n".join(lines)
