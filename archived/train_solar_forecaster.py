"""
Train the LSTM Solar Forecaster
================================

End-to-end training script.
  1. Loads NSRDB data from the configured data directory.
  2. Trains CNN-BiLSTM on 2018 (train) / 2019 (val), evaluates on 2020.
  3. Saves checkpoint to <project_root>/src/solar/models/solar_forecaster_v2.pt
  4. Prints evaluation metrics.

Usage (from project root):
    python src/solar/train_solar_forecaster.py

FIX NOTES
---------
* `import os` is now guaranteed at the top of solar_forecasting.py so
  no NameError will occur when the checkpoint is later loaded.
* Scaler inverse_transform always uses (N,1) shape — no shape mismatch.
"""

import sys
import os
import logging

# ── Resolve project root regardless of working directory ─────────────────────
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, PROJECT_ROOT)

# ── Local imports (use relative or absolute depending on your package layout) ─
try:
    from src.solar.solar_preprocessing import load_multi_year, clean_irradiance_data
    from src.solar.solar_forecasting   import train_forecaster, print_metrics
except ModuleNotFoundError:
    # Fallback: run directly from the solar directory
    from solar_preprocessing import load_multi_year, clean_irradiance_data
    from solar_forecasting   import train_forecaster, print_metrics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
)
logger = logging.getLogger('train_solar')


def main():
    # ── 1. Locate data ───────────────────────────────────────────────────────
    # Try standard locations in order
    candidate_dirs = [
        os.path.join(PROJECT_ROOT, 'data', 'nsrdb'),
        os.path.join(PROJECT_ROOT, 'Dataset'),
        os.path.join(PROJECT_ROOT, 'SolarData'),
        os.path.dirname(os.path.abspath(__file__)),   # same dir as script
    ]
    data_dir = None
    for d in candidate_dirs:
        if os.path.isdir(d) and any(f.endswith('.csv') for f in os.listdir(d)):
            data_dir = d
            break

    if data_dir is None:
        logger.error("No NSRDB CSV files found. Searched:\n  %s",
                     '\n  '.join(candidate_dirs))
        logger.error("Place the three NSRDB CSV files in one of the above directories.")
        sys.exit(1)

    logger.info("Data directory: %s", data_dir)

    # ── 2. Load & clean ──────────────────────────────────────────────────────
    logger.info("Loading NSRDB data…")
    raw_df   = load_multi_year(data_dir)
    clean_df = clean_irradiance_data(raw_df)
    logger.info("Loaded %d rows, years %s",
                len(clean_df), sorted(clean_df.index.year.unique().tolist()))

    # ── 3. Train ─────────────────────────────────────────────────────────────
    logger.info("Starting CNN-BiLSTM training…")
    model, scalers, metrics = train_forecaster(
        clean_df,
        train_years=[2018, 2019],
        val_years=[2019],
        val_months=[10, 11, 12],
        test_years=[2020],
        epochs=150,
        batch_size=64,
        lr=1e-3,
        patience=30,
    )

    # ── 4. Report ────────────────────────────────────────────────────────────
    print_metrics(metrics)

    model_path = os.path.join(PROJECT_ROOT, 'src', 'solar', 'models', 'solar_forecaster_v2.pt')
    print(f"\nModel checkpoint saved to: {model_path}")
    print("You can now run evaluate_solar_forecaster.py or the EMS experiment.")


if __name__ == '__main__':
    main()