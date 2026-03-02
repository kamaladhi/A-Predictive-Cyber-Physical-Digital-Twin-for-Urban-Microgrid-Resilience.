"""
Evaluate the Trained Solar Forecaster (v2)
===========================================

Loads the CNN-BiLSTM checkpoint, evaluates on the 2020 test set, and
demonstrates the updated EMSForecast API.

Usage (from project root):
    python scripts/evaluate_solar_forecaster.py
"""

import sys
import os
import logging
from datetime import datetime

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, PROJECT_ROOT)

try:
    from src.solar.solar_preprocessing import load_multi_year, clean_irradiance_data
    from src.solar.pv_power_model      import SolarDataProvider
    from src.solar.solar_forecasting   import (
        SolarForecaster, SolarForecastDataset, SolarForecastModel,
        evaluate_forecaster, print_metrics,
    )
except ModuleNotFoundError:
    from solar_preprocessing import load_multi_year, clean_irradiance_data
    from pv_power_model      import SolarDataProvider
    from solar_forecasting   import (
        SolarForecaster, SolarForecastDataset, SolarForecastModel,
        evaluate_forecaster, print_metrics,
    )

import torch

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)-8s %(name)s: %(message)s')
logger = logging.getLogger('evaluate_solar')


def main():
    model_path = os.path.join(PROJECT_ROOT, 'src', 'solar', 'models',
                              'solar_forecaster_v2.pt')

    if not os.path.exists(model_path):
        print(f"\nERROR: No trained model found at:\n  {model_path}")
        print("Run 'python scripts/train_solar_forecaster.py' first.\n")
        return

    # ── 1. Load data ─────────────────────────────────────────────────────────
    data_dir = None
    for d in [os.path.join(PROJECT_ROOT, 'data', 'nsrdb'),
               os.path.join(PROJECT_ROOT, 'Dataset'),
               os.path.join(PROJECT_ROOT, 'SolarData')]:
        if os.path.isdir(d):
            data_dir = d
            break

    if data_dir is None:
        print("ERROR: NSRDB data directory not found.")
        return

    logger.info("Loading NSRDB data from %s", data_dir)
    clean_df = clean_irradiance_data(load_multi_year(data_dir))

    # ── 2. Full evaluation on 2020 ───────────────────────────────────────────
    logger.info("Running full evaluation on 2020 test set…")

    checkpoint = torch.load(model_path, map_location='cpu', weights_only=False)
    
    # Extract config from checkpoint
    horizons  = checkpoint.get('horizons', [1, 6, 24])
    quantiles = checkpoint.get('quantiles', [0.1, 0.5, 0.9])
    
    from src.solar.solar_preprocessing import RobustFeatureScaler
    from src.solar.pv_power_model import GHITargetScaler, KtTargetScaler
    
    feature_scaler = RobustFeatureScaler.from_dict(checkpoint['feature_scaler'])
    # Support both old (GHI) and new (kt) target scalers
    ts_dict = checkpoint['target_scaler']
    if ts_dict.get('type') == 'KtTargetScaler':
        target_scaler = KtTargetScaler.from_dict(ts_dict)
    else:
        target_scaler = GHITargetScaler.from_dict(ts_dict)

    model = SolarForecastModel(
        input_size=checkpoint['hyperparameters']['input_size'],
        horizons=horizons,
        quantiles=quantiles
    )
    model.load_state_dict(checkpoint['model_state_dict'])

    test_df = clean_df[clean_df.index.year == 2020]
    test_ds = SolarForecastDataset(
        test_df,
        horizons=horizons,
        feature_scaler=feature_scaler,
        target_scaler=target_scaler,
        daytime_only=True
    )

    scalers = {'feature': feature_scaler, 'target': target_scaler}
    metrics = evaluate_forecaster(model, test_ds, scalers, 
                                  horizons=horizons, quantiles=quantiles)
    print_metrics(metrics)

    # ── 3. Demonstrate predict() API ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  Forecast API Demo (EMS Integration)")
    print("=" * 60)

    provider   = SolarDataProvider(clean_df)
    forecaster = SolarForecaster(model_path, provider, installed_kwp=350.0)

    # Representative timestamps spanning the test year
    test_times = [
        datetime(2020,  1, 15, 10, 0),  # Winter morning
        datetime(2020,  4, 15, 12, 0),  # Pre-monsoon noon
        datetime(2020,  7, 15, 14, 0),  # Monsoon afternoon
        datetime(2020, 10, 15,  8, 0),  # Post-monsoon morning
    ]

    for ts in test_times:
        forecast = forecaster.predict(ts)
        print("\n" + forecast.summary())

    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()