"""
Evaluate the Trained Solar Forecaster
=======================================

Loads a saved checkpoint, evaluates on 2020 test data, and demonstrates
the predict() API that EMS will use.

Usage:
    cd D:\\Digital-twin-microgrid
    python SolarData/evaluate_solar_forecaster.py
"""

import sys
import os
import logging

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from datetime import datetime
from src.solar.solar_preprocessing import load_multi_year, clean_irradiance_data
from src.solar.pv_power_model import SolarDataProvider, calculate_pv_power
from src.solar.solar_forecasting import (
    SolarForecaster, SolarForecastDataset, SolarLSTM,
    evaluate_forecaster, print_metrics, load_scaler,
)
import torch

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger('evaluate_solar')


def main():
    model_path = os.path.join(PROJECT_ROOT, 'SolarData', 'models',
                              'solar_lstm.pt')
    data_dir = os.path.join(PROJECT_ROOT, 'Dataset')

    if not os.path.exists(model_path):
        print(f"ERROR: No trained model found at {model_path}")
        print("Run 'python SolarData/train_solar_forecaster.py' first.")
        return

    # ── 1. Load data ─────────────────────────────────────────────────────
    logger.info("Loading NSRDB data...")
    clean_df = clean_irradiance_data(load_multi_year(data_dir))

    # ── 2. Full evaluation on 2020 ───────────────────────────────────────
    logger.info("Running full evaluation on 2020 test set...")

    checkpoint = torch.load(model_path, map_location='cpu', weights_only=False)
    feature_scaler = load_scaler(checkpoint['feature_scaler'])
    target_scaler = load_scaler(checkpoint['target_scaler'])

    hp = checkpoint['hyperparameters']
    model = SolarLSTM(
        input_size=hp['input_size'],
        hidden_size=hp['hidden_size'],
        num_layers=hp['num_layers'],
        dropout=0.0,
    )
    model.load_state_dict(checkpoint['model_state_dict'])

    test_df = clean_df[clean_df.index.year == 2020]
    test_ds = SolarForecastDataset(
        test_df,
        feature_scaler=feature_scaler,
        target_scaler=target_scaler,
    )

    scalers = {'feature': feature_scaler, 'target': target_scaler}
    metrics = evaluate_forecaster(model, test_ds, scalers)
    print_metrics(metrics)

    # ── 3. Demonstrate predict() API ─────────────────────────────────────
    print("\n" + "=" * 60)
    print("  Forecast API Demo (EMS Integration)")
    print("=" * 60)

    provider = SolarDataProvider(clean_df)
    forecaster = SolarForecaster(model_path, provider)

    # Predict at several representative timestamps
    test_times = [
        datetime(2020, 1, 15, 10, 0),  # Winter morning
        datetime(2020, 4, 15, 12, 0),  # Spring noon
        datetime(2020, 7, 15, 14, 0),  # Summer afternoon
        datetime(2020, 10, 15, 8, 0),  # Autumn morning
    ]

    from src.microgrid.Hospital.hospital_parameters import PVConfig
    pv_cfg = PVConfig()

    for ts in test_times:
        forecast = forecaster.predict(ts)
        print(f"\n  Timestamp: {ts}")
        for h in [1, 6, 24]:
            key = f'ghi_{h}h'
            ghi = forecast[key]
            lower = forecast[f'{key}_lower']
            upper = forecast[f'{key}_upper']
            # Convert GHI forecast to PV power estimate
            pv_kw = calculate_pv_power(ghi, 25.0, pv_cfg) if ghi > 0 else 0.0
            print(f"    {h:2d}h ahead: GHI = {ghi:6.1f} W/m2 "
                  f"[{lower:.0f} - {upper:.0f}]  "
                  f"=> PV ~ {pv_kw:.0f} kW")

    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()
