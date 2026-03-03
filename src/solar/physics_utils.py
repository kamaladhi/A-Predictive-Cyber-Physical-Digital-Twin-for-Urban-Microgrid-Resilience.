"""
Solar Physics Utilities
=======================

Provides solar geometry (zenith, azimuth) and clear-sky irradiance models.
Uses pvlib for research-grade accuracy with a pure-math fallback so the
module works even when pvlib is not installed.

Location: 22.20°N, 78.47°E (Madhya Pradesh, India)

v2 changes: None — physics_utils.py was already correct.
"""

import os
import math
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Microgrid Location
LATITUDE  = 22.20
LONGITUDE = 78.47
TZ        = 'Asia/Kolkata'   # UTC+5:30
ALTITUDE  = 450              # m, average for central Madhya Pradesh

try:
    import pvlib
    from pvlib.location import Location
    _PVLIB = True
    logger.debug("pvlib %s found — using Ineichen clear-sky model", pvlib.__version__)
except ImportError:
    _PVLIB = False
    logger.warning("pvlib not found — using built-in Blanco-Muriel/Kasten fallback. "
                   "Install pvlib for higher accuracy: pip install pvlib")


# =============================================================================
# Public API
# =============================================================================

def get_solar_position(timestamps: pd.DatetimeIndex) -> pd.DataFrame:
    """
    Solar zenith, azimuth and elevation for the given timestamps.
    Returns pd.DataFrame with columns ['zenith', 'azimuth', 'elevation'] (degrees).
    """
    if _PVLIB:
        loc = Location(LATITUDE, LONGITUDE, tz=TZ, altitude=ALTITUDE)
        sp  = loc.get_solarposition(timestamps)
        return sp[['zenith', 'azimuth', 'elevation']]
    return _solar_position_fallback(timestamps)


def get_clearsky_ghi(timestamps: pd.DatetimeIndex) -> pd.Series:
    """
    Clear-sky GHI (W/m²).
    pvlib Ineichen-Perez if available; otherwise Kasten/ASHRAE simplified.
    """
    if _PVLIB:
        loc      = Location(LATITUDE, LONGITUDE, tz=TZ, altitude=ALTITUDE)
        clearsky = loc.get_clearsky(timestamps, model='ineichen')
        return clearsky['ghi']
    return _clearsky_ghi_fallback(timestamps)


def calculate_clearness_index(ghi: pd.Series,
                              clearsky_ghi: pd.Series) -> pd.Series:
    """
    Clearness index  kt = GHI / GHI_clear.
    Clamped to [0, 1.2].  Night (clearsky == 0) returns 0.
    """
    kt = ghi / clearsky_ghi.replace(0, float('inf'))
    return kt.fillna(0).clip(0, 1.2)


# =============================================================================
# Pure-Python Fallback  (Blanco-Muriel 2001 + Kasten)
# =============================================================================

def _doy(timestamps: pd.DatetimeIndex) -> np.ndarray:
    return timestamps.dayofyear.astype(float)


def _equation_of_time(doy: np.ndarray) -> np.ndarray:
    """Spencer (1971) equation of time in minutes."""
    gamma = 2 * np.pi / 365 * (doy - 1)
    eot = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma)
        - 0.04089  * np.sin(2 * gamma)
    )
    return eot


def _declination(doy: np.ndarray) -> np.ndarray:
    """Spencer (1971) solar declination in degrees."""
    gamma = 2 * np.pi / 365 * (doy - 1)
    delta = np.rad2deg(
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma)
        + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma)
        + 0.00148  * np.sin(3 * gamma)
    )
    return delta


def _solar_position_fallback(timestamps: pd.DatetimeIndex) -> pd.DataFrame:
    """Analytical solar position (Blanco-Muriel 2001), accurate to ±0.5°."""
    utc_offset = 5.5
    doy        = _doy(timestamps)
    hour_utc   = timestamps.hour + timestamps.minute / 60.0 + timestamps.second / 3600.0
    hour_local = (hour_utc + utc_offset) % 24.0

    eot        = _equation_of_time(doy)
    lstm       = 15.0 * round(utc_offset)
    tc         = 4 * (LONGITUDE - lstm) + eot
    solar_time = hour_local + tc / 60.0
    ha_deg     = 15.0 * (solar_time - 12.0)

    delta_rad  = np.deg2rad(_declination(doy))
    lat_rad    = math.radians(LATITUDE)
    ha_rad     = np.deg2rad(ha_deg)

    cos_z = (np.sin(lat_rad) * np.sin(delta_rad)
             + np.cos(lat_rad) * np.cos(delta_rad) * np.cos(ha_rad))
    cos_z   = np.clip(cos_z, -1.0, 1.0)
    zenith  = np.rad2deg(np.arccos(cos_z))
    elev    = 90.0 - zenith

    sin_elev = np.sin(np.deg2rad(np.clip(elev, 0.01, 90)))
    sin_az   = np.cos(delta_rad) * np.sin(ha_rad) / (sin_elev + 1e-9)
    sin_az   = np.clip(sin_az, -1.0, 1.0)
    az       = np.rad2deg(np.arcsin(sin_az))
    az       = np.where(ha_deg > 0, 180.0 - az, 180.0 + az) % 360.0

    return pd.DataFrame(
        {'zenith': zenith, 'azimuth': az, 'elevation': elev},
        index=timestamps
    )


def _clearsky_ghi_fallback(timestamps: pd.DatetimeIndex) -> pd.Series:
    """Kasten / ASHRAE clear-sky GHI (W/m²)."""
    solpos = _solar_position_fallback(timestamps)
    elev   = solpos['elevation'].values

    doy    = _doy(timestamps)
    Eo     = 1 + 0.033 * np.cos(np.deg2rad(360.0 * doy / 365.0))
    I0     = 1361.0 * Eo

    el_r   = np.deg2rad(np.clip(elev, 0.1, 90.0))
    am     = 1.0 / (np.sin(el_r) + 0.50572 * np.power(elev + 6.07995, -1.6364))
    am     = np.where(elev <= 0, 37.0, am)

    ghi    = I0 * np.sin(el_r) * 0.7 ** (am ** 0.678)
    ghi    = np.where(elev <= 0, 0.0, ghi)
    ghi    = np.clip(ghi, 0.0, 1400.0)

    return pd.Series(ghi, index=timestamps, name='ghi_cs')