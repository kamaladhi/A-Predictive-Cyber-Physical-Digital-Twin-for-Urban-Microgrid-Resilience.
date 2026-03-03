"""
PV Power Model — Irradiance-to-Power Conversion
=================================================

Converts GHI (Global Horizontal Irradiance) from NSRDB data into realistic
AC power output for photovoltaic arrays.

Model chain:
  GHI → cell temperature (NOCT model)
      → DC power (nameplate × irradiance ratio × temp derating)
      → AC power (× inverter efficiency × system losses × soiling × degradation)

v2 changes:
  - Added GHITargetScaler class (used by solar_forecasting.py SolarForecastDataset)
  - No changes to SolarDataProvider or calculate_pv_power
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# System loss factors
SOILING_LOSS_FACTOR     = 0.02
ANNUAL_DEGRADATION_RATE = 0.005
SYSTEM_LOSSES_FACTOR    = 0.02
STC_IRRADIANCE_W_M2     = 1000.0


# =============================================================================
# GHI Target Scaler (used by solar_forecasting.py)
# =============================================================================

class GHITargetScaler:
    """
    Min-max scaler for GHI targets, fit on daytime-only train data.

    Placing this here (rather than solar_forecasting.py) avoids circular
    imports since both SolarDataProvider and the forecasting Dataset need it.

    IMPORTANT: Must be fit ONLY on the training split daytime GHI values.
    Fitting on the full dataset (including nighttime zeros) compresses the
    effective range and degrades inverse-transform accuracy.
    """

    def __init__(self, max_ghi: float = 1400.0):
        self.max_ghi_ = max_ghi
        self.min_     = 0.0
        self.range_   = max_ghi

    def fit(self, y: np.ndarray) -> 'GHITargetScaler':
        """Fit on daytime GHI values (> 0) only for accurate range estimation."""
        daytime     = y[y > 0]
        self.min_   = float(daytime.min()) if len(daytime) > 0 else 0.0
        self.range_ = float(daytime.max()) - self.min_
        if self.range_ < 1.0:
            self.range_ = self.max_ghi_
        return self

    def transform(self, y: np.ndarray) -> np.ndarray:
        return np.clip((y - self.min_) / self.range_, 0.0, 1.0)

    def inverse_transform(self, y: np.ndarray) -> np.ndarray:
        return np.clip(y * self.range_ + self.min_, 0.0, self.max_ghi_)

    def to_dict(self):
        return {'type': 'GHITargetScaler', 'min': self.min_, 'range': self.range_}

    @classmethod
    def from_dict(cls, d) -> 'GHITargetScaler':
        s = cls()
        s.min_   = d.get('min', 0.0)
        s.range_ = d.get('range', 1400.0)
        return s


class KtTargetScaler:
    """
    Clearness-index (kt) target scaler for physics-aware solar forecasting.

    kt = GHI / GHI_clearsky is bounded [0, 1.2] by definition.
    Simple scaling: transform = kt / 1.2 → [0, 1], no fitting needed.

    Why predict kt instead of raw GHI?
      GHI = clear_sky_envelope × cloud_attenuation (kt)
      The clear-sky envelope is deterministic (physics). Only the cloud
      attenuation needs ML. Predicting kt isolates the ML task, reducing
      both MAPE and seasonal bias (especially JJA monsoon).
    """

    def __init__(self, max_kt: float = 1.2):
        self.max_kt_ = max_kt

    def fit(self, y: np.ndarray) -> 'KtTargetScaler':
        # kt is physically bounded; no empirical fitting needed
        return self

    def transform(self, y: np.ndarray) -> np.ndarray:
        return np.clip(y / self.max_kt_, 0.0, 1.0)

    def inverse_transform(self, y: np.ndarray) -> np.ndarray:
        return np.clip(y * self.max_kt_, 0.0, self.max_kt_)

    def to_dict(self):
        return {'type': 'KtTargetScaler', 'max_kt': self.max_kt_}

    @classmethod
    def from_dict(cls, d) -> 'KtTargetScaler':
        s = cls()
        s.max_kt_ = d.get('max_kt', 1.2)
        return s


# =============================================================================
# NOCT / Power Model
# =============================================================================

def calculate_cell_temperature(ghi_w_m2: float,
                               ambient_temp_c: float,
                               noct_c: float = 45.0) -> float:
    """NOCT model:  T_cell = T_ambient + (NOCT - 20) × (G / 800)"""
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
    Convert GHI irradiance to AC power output (kW).

    P_ac = P_stc × (G/G_stc) × f_temp × η_inv × (1−f_soil)
           × (1−f_deg)^age × (1−f_sys)
    """
    if ghi_w_m2 <= 0:
        return 0.0

    irradiance_ratio = ghi_w_m2 / STC_IRRADIANCE_W_M2

    cell_temp  = calculate_cell_temperature(
        ghi_w_m2, ambient_temp_c,
        noct_c=getattr(pv_config, 'nominal_operating_temp', 45.0))
    temp_coeff  = getattr(pv_config, 'temperature_coefficient', -0.004)
    temp_factor = 1.0 + temp_coeff * (cell_temp - 25.0)

    inverter_eff       = getattr(pv_config, 'inverter_efficiency', 0.97)
    degradation_factor = (1.0 - ANNUAL_DEGRADATION_RATE) ** system_age_years
    loss_factor        = (1.0 - soiling_factor) * (1.0 - system_losses)

    power_kw = (
        pv_config.installed_capacity_kwp
        * irradiance_ratio
        * temp_factor
        * inverter_eff
        * degradation_factor
        * loss_factor
    )
    return max(0.0, power_kw)


# =============================================================================
# SolarDataProvider
# =============================================================================

class SolarDataProvider:
    """
    Time-aligned solar data provider for microgrid simulators.
    Wraps a preprocessed NSRDB DataFrame and provides irradiance + temperature
    lookups by timestamp, with year-cycling for multi-year simulation.
    """

    def __init__(self, solar_df: pd.DataFrame, default_temp_c: float = 25.0):
        if solar_df.empty:
            raise ValueError("Solar data DataFrame is empty")
        if 'GHI' not in solar_df.columns:
            raise ValueError("Solar data must contain 'GHI' column")

        self.data            = solar_df.sort_index()
        self.default_temp_c  = default_temp_c
        self.has_temperature = 'Temperature' in self.data.columns
        self.available_years = sorted(self.data.index.year.unique().tolist())

        self.resolution_seconds = (
            int(self.data.index.to_series().diff().median().total_seconds())
            if len(self.data) > 1 else 3600
        )
        logger.info("SolarDataProvider: %d rows, years %s, resolution %ds",
                    len(self.data), self.available_years, self.resolution_seconds)

    def _map_to_available_year(self, dt: datetime) -> datetime:
        if dt.year in self.available_years:
            return dt
        closest = min(self.available_years, key=lambda y: abs(y - dt.year))
        try:
            return dt.replace(year=closest)
        except ValueError:
            return dt.replace(year=closest, month=2, day=28)

    def get_irradiance(self, timestamp: datetime) -> Tuple[float, float]:
        """
        Nearest-neighbour lookup of GHI and ambient temperature.
        Returns (GHI W/m², temperature °C). Returns (0.0, default_temp) on miss.
        """
        mapped = self._map_to_available_year(timestamp)
        pd_ts  = pd.Timestamp(mapped)
        try:
            idx = self.data.index.get_indexer(
                [pd_ts], method='nearest', tolerance=pd.Timedelta('1h'))
            if idx[0] == -1:
                return (0.0, self.default_temp_c)
            row  = self.data.iloc[idx[0]]
            ghi  = float(row['GHI'])
            temp = (float(row['Temperature']) if self.has_temperature
                    else self.default_temp_c)
            return (max(0.0, ghi), temp)
        except Exception as exc:
            logger.debug("Irradiance lookup failed for %s: %s", timestamp, exc)
            return (0.0, self.default_temp_c)

    def get_irradiance_series(self, start: datetime, end: datetime,
                              freq: str = '5min') -> pd.DataFrame:
        timestamps = pd.date_range(start=start, end=end, freq=freq)
        records    = [{'timestamp': ts,
                       'GHI': self.get_irradiance(ts)[0],
                       'Temperature': self.get_irradiance(ts)[1]}
                      for ts in timestamps]
        df = pd.DataFrame(records).set_index('timestamp')
        return df

    def summary(self) -> str:
        lines = [
            "SolarDataProvider:",
            f"  Rows:       {len(self.data)}",
            f"  Years:      {self.available_years}",
            f"  Resolution: {self.resolution_seconds}s",
            f"  GHI range:  [{self.data['GHI'].min():.0f}, "
            f"{self.data['GHI'].max():.0f}] W/m²",
        ]
        if self.has_temperature:
            lines.append(
                f"  Temp range: [{self.data['Temperature'].min():.1f}, "
                f"{self.data['Temperature'].max():.1f}] °C")
        return "\n".join(lines)