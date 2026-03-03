"""
SolarData — NSRDB Solar Irradiance Preprocessing, PV Power Modeling & Forecasting

This module provides:
  - solar_preprocessing: Load, clean, and resample NSRDB CSV datasets
  - pv_power_model: Convert irradiance to PV power output with realistic losses
  - SolarDataProvider: Time-aligned irradiance lookup for simulator integration
  - solar_forecasting: LSTM-based multi-horizon GHI forecasting
  - SolarForecaster: Inference-time forecast API for EMS integration
"""

from src.solar.solar_preprocessing import (
    load_nsrdb_file,
    load_multi_year,
    clean_irradiance_data,
    resample_to_interval,
    get_data_quality_report,
)
from src.solar.pv_power_model import (
    calculate_cell_temperature,
    calculate_pv_power,
    SolarDataProvider,
)
from src.solar.solar_forecasting import (
    SolarForecaster,
    train_forecaster,
)
