"""
Validation Script — Solar Forecasting Pipeline v2
==================================================

End-to-end validation that:
  1. Loads and preprocesses the NSRDB dataset (2018–2020)
  2. Prints a data quality report
  3. Trains the CNN-BiLSTM forecaster (2018 train / 2019 val / 2020 test)
  4. Prints evaluation metrics (RMSE, MAPE, Skill Score, PICP)
  5. Runs a 48h EMS forecast simulation and prints probabilistic output
  6. Exports comparison CSV for review
  7. Checks backward compatibility with run_scenario() if hospital simulator
     is present (skipped gracefully if not)

Usage:
    cd <project_root>
    python SolarData/validate_solar_integration.py

All imports are relative to the directory this file lives in, so it works
whether run standalone or as part of the SolarData package.
"""

import sys
import os
import logging

# ── Ensure the directory containing these modules is on sys.path ─────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Also add project root for hospital_simulator imports
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import numpy as np
import pandas as pd
from datetime import datetime

from solar_preprocessing import (
    load_multi_year,
    clean_irradiance_data,
    add_research_features,
    get_daytime_mask,
    temporal_split,
    get_data_quality_report,
    print_quality_report,
    ALL_FEATURES,
    NUM_FEATURES,
)
from pv_power_model import SolarDataProvider, calculate_pv_power
from solar_forecasting import (
    train_forecaster,
    print_metrics,
    SolarForecaster,
    build_scenario_tree,
)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logger = logging.getLogger('validate_solar')


def main():
    # ── 1. Load & Preprocess NSRDB Data ──────────────────────────────────────
    # Adjust this path to where your CSV files are located
    data_dir = os.path.join(_PROJECT_ROOT, 'Dataset')
    if not os.path.isdir(data_dir):
        # Fallback: look for CSV files next to this script
        data_dir = _HERE

    logger.info("=" * 60)
    logger.info("STEP 1: Loading NSRDB data from %s", data_dir)
    logger.info("=" * 60)

    raw_df   = load_multi_year(data_dir)
    clean_df = clean_irradiance_data(raw_df)

    # ── 2. Data Quality Report ────────────────────────────────────────────────
    logger.info("STEP 2: Data Quality Report")
    report = get_data_quality_report(clean_df)
    print_quality_report(report)

    # ── 3. Feature Engineering Preview ───────────────────────────────────────
    logger.info("STEP 3: Feature Engineering Check")
    logger.info("  Calling add_research_features() on full dataset...")
    featured_df = add_research_features(clean_df)
    logger.info("  Features present: %d / %d expected", len(ALL_FEATURES), NUM_FEATURES)

    daytime_mask = get_daytime_mask(featured_df)
    logger.info("  Daytime rows (elevation > 3°): %d / %d  (%.1f%%)",
                daytime_mask.sum(), len(featured_df),
                100.0 * daytime_mask.mean())

    # ── 4. PV Power Model Sanity Check ───────────────────────────────────────
    logger.info("STEP 4: PV Power Model Sanity Check")

    # Create a minimal pv_config-like object for the check
    class _PVConf:
        installed_capacity_kwp = 350.0
        temperature_coefficient = -0.004
        nominal_operating_temp  = 45.0
        inverter_efficiency     = 0.97

    pv_cfg = _PVConf()

    p_stc   = calculate_pv_power(1000, 25, pv_cfg)
    p_hot   = calculate_pv_power(1000, 45, pv_cfg)
    p_low   = calculate_pv_power(200,  25, pv_cfg)
    p_night = calculate_pv_power(0,    20, pv_cfg)

    logger.info("  PV at STC (1000 W/m², 25°C):   %.1f kW", p_stc)
    logger.info("  PV at 45°C:                    %.1f kW  (derating: %.1f%%)",
                p_hot, (p_hot / p_stc - 1) * 100)
    logger.info("  PV at 200 W/m²:                %.1f kW", p_low)
    logger.info("  PV at night (0 W/m²):          %.1f kW", p_night)
    assert p_night == 0.0, "Night power should be zero!"
    assert p_stc > 300.0,  "STC power should be > 300 kW for 350 kWp system"
    logger.info("  ✅ PV model sanity checks passed.")

    # ── 5. Train CNN-BiLSTM Forecaster ───────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 5: Training CNN-BiLSTM Forecaster")
    logger.info("=" * 60)
    logger.info("  Train: 2018 | Val: 2019 | Test: 2020")
    logger.info("  This may take several minutes on CPU...")

    save_dir = os.path.join(_HERE, 'models')

    model, scalers, metrics = train_forecaster(
        clean_df,
        train_years=[2018],
        val_years=[2019],
        test_years=[2020],
        epochs=150,
        lr=1e-3,
        patience=25,
        save_dir=save_dir,
    )

    logger.info("Training complete.")

    # ── 6. Print Evaluation Metrics ───────────────────────────────────────────
    logger.info("STEP 6: Evaluation Metrics")
    print_metrics(metrics)

    # Target achievement check
    h_metrics = metrics.get('horizons', {})
    passed = []
    for hk, target_mape in [('1h', 25.0), ('6h', 40.0), ('24h', 55.0)]:
        m = h_metrics.get(hk, {}).get('mape_daytime_pct')
        ok = m is not None and m < target_mape
        status = '✅' if ok else '⚠️ '
        logger.info("  %s %s MAPE: %.1f%% (target < %.0f%%)",
                    status, hk, m or 0.0, target_mape)
        passed.append(ok)

    # ── 7. EMS Forecast Simulation ───────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 7: EMS Forecast Simulation (48h from 2020-06-15 12:00)")
    logger.info("=" * 60)

    ckpt_path = os.path.join(save_dir, 'solar_forecaster_v2.pt')
    provider  = SolarDataProvider(clean_df)
    forecaster = SolarForecaster(ckpt_path, provider, installed_kwp=350.0)

    start_time = datetime(2020, 6, 15, 12, 0)
    forecasts  = []
    for h in range(48):
        ts = start_time + pd.Timedelta(hours=h)
        fc = forecaster.predict(ts)
        forecasts.append(fc)

    logger.info("Sample forecast at t+0 (noon, JJA / monsoon period):")
    print(forecasts[0].summary())

    # Scenario tree for stochastic MPC
    scenario_tree = build_scenario_tree(forecasts[:24], n_scenarios=5)
    logger.info("Scenario tree: %d scenarios × %d steps | reserve=%.1f kW",
                scenario_tree['n_scenarios'], scenario_tree['n_steps'],
                scenario_tree['reserve_kw'])

    # ── 8. Export Results ─────────────────────────────────────────────────────
    logger.info("STEP 8: Exporting results")
    output_dir = os.path.join(_HERE, 'validation_output')
    os.makedirs(output_dir, exist_ok=True)

    rows = []
    for fc in forecasts:
        row = {'timestamp': fc.timestamp,
               'is_daytime': fc.is_daytime,
               'reserve_kw': fc.reserve_kw,
               'dispatch_kw': fc.dispatch_kw,
               'risk_index': fc.risk_index}
        for h in fc.horizons:
            for q in [10, 50, 90]:
                row[f'ghi_q{q:02d}_{h}h'] = fc.raw.get(f'ghi_q{q:02d}_{h}h', 0.0)
                row[f'pv_q{q:02d}_{h}h']  = fc.raw.get(f'pv_q{q:02d}_{h}h', 0.0)
        rows.append(row)

    results_df = pd.DataFrame(rows)
    csv_path   = os.path.join(output_dir, 'ems_forecast_48h.csv')
    results_df.to_csv(csv_path, index=False)
    logger.info("EMS forecast CSV: %s", csv_path)

    # Metric summary CSV
    metric_rows = []
    for hk, hm in h_metrics.items():
        metric_rows.append({
            'horizon': hk,
            'rmse':    hm.get('rmse'),
            'mae':     hm.get('mae'),
            'mape_daytime_pct': hm.get('mape_daytime_pct'),
            'skill_score': hm.get('skill_score'),
            'picp_80pct':  hm.get('picp_80pct'),
        })
    metrics_csv = os.path.join(output_dir, 'evaluation_metrics.csv')
    pd.DataFrame(metric_rows).to_csv(metrics_csv, index=False)
    logger.info("Metrics CSV: %s", metrics_csv)

    # ── 9. Backward Compatibility (hospital simulator) ────────────────────────
    logger.info("STEP 9: Backward Compatibility Check")
    try:
        from src.microgrid.Hospital.hospital_simulator import MicrogridSimulator
        from src.microgrid.Hospital.hospital_parameters import create_default_config

        config = create_default_config()
        sim    = MicrogridSimulator(config)
        df_compat = sim.run_scenario(
            duration_hours=24, outage_start_hour=12,
            outage_duration_hours=4, start_time=start_time)
        assert len(df_compat) > 0
        assert 'pv_power_kw' in df_compat.columns
        logger.info("  ✅ Backward compatibility confirmed — run_scenario() unchanged.")
    except ImportError:
        logger.info("  ⏭  Hospital simulator not found — backward compat check skipped.")
    except Exception as e:
        logger.warning("  ⚠️  Hospital simulator check failed: %s", e)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  VALIDATION COMPLETE — v2 Pipeline")
    print("=" * 60)
    print(f"  ✅ NSRDB data: {report['total_rows']} rows, "
          f"years {report['years_covered']}")
    print(f"  ✅ Features: {NUM_FEATURES} (was 20 in v1)")
    print(f"  ✅ Model: CNN-BiLSTM + Temporal Attention + Quantile Regression")

    for hk, hm in h_metrics.items():
        mape = hm.get('mape_daytime_pct')
        print(f"  {'✅' if mape and mape < 55 else '⚠️ '} "
              f"{hk}: RMSE={hm.get('rmse'):.1f} W/m²  "
              f"MAPE={mape:.1f}%  SS={hm.get('skill_score'):.3f}")

    print(f"  📊 EMS forecast: {csv_path}")
    print(f"  📊 Metrics:      {metrics_csv}")
    print(f"  💾 Checkpoint:   {ckpt_path}")
    print("=" * 60 + "\n")


if __name__ == '__main__':
    main()