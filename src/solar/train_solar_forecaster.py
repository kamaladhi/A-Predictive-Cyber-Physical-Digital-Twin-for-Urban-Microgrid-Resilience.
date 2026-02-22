"""
Train the LSTM Solar Forecaster
================================

End-to-end training script:
  1. Loads NSRDB data via existing preprocessing pipeline
  2. Trains LSTM on 2018-2019, evaluates on 2020
  3. Saves checkpoint to SolarData/models/solar_lstm.pt
  4. Prints evaluation metrics

Usage:
    cd D:\\Digital-twin-microgrid
    python SolarData/train_solar_forecaster.py
"""

import sys
import os
import logging

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from src.solar.solar_preprocessing import load_multi_year, clean_irradiance_data
from src.solar.solar_forecasting import train_forecaster, print_metrics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger('train_solar')


def main():
    # ── 1. Load Data ─────────────────────────────────────────────────────
    data_dir = os.path.join(PROJECT_ROOT, 'Dataset')
    logger.info("Loading NSRDB data from %s", data_dir)

    raw_df = load_multi_year(data_dir)
    clean_df = clean_irradiance_data(raw_df)
    logger.info("Loaded %d rows, years %s",
                len(clean_df), sorted(clean_df.index.year.unique().tolist()))

    # ── 2. Train ─────────────────────────────────────────────────────────
    logger.info("Starting LSTM training (enhanced model v2)...")
    model, scalers, metrics = train_forecaster(
        clean_df,
        train_years=[2018, 2019],
        test_years=[2020],
        epochs=80,
        batch_size=64,
        lr=1e-3,
        patience=15,
    )

    # ── 3. Results ───────────────────────────────────────────────────────
    print_metrics(metrics)

    # Print model save location
    model_path = os.path.join(PROJECT_ROOT, 'SolarData', 'models',
                              'solar_lstm.pt')
    print(f"\nModel saved to: {model_path}")
    print("Run 'python SolarData/evaluate_solar_forecaster.py' "
          "for detailed evaluation.")


if __name__ == '__main__':
    main()
