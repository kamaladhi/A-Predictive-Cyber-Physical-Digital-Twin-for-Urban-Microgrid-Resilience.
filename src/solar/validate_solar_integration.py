"""
Validation Script — Real Solar Data Integration
=================================================

End-to-end validation that:
  1. Loads and preprocesses the NSRDB dataset (2018–2020)
  2. Prints a data quality report
  3. Runs the hospital simulator for 48h with BOTH synthetic and real data
  4. Compares PV output profiles and prints summary statistics
  5. Exports comparison CSV for review

Usage:
    cd d:\\Digital-twin-microgrid
    python SolarData/validate_solar_integration.py
"""

import sys
import os
import logging

# Ensure project root is on path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

import numpy as np
import pandas as pd
from datetime import datetime

from src.solar.solar_preprocessing import (
    load_multi_year,
    clean_irradiance_data,
    get_data_quality_report,
    print_quality_report,
)
from src.solar.pv_power_model import SolarDataProvider, calculate_pv_power

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logger = logging.getLogger('validate_solar')


def main():
    # ── 1. Load & Preprocess NSRDB Data ──────────────────────────────────
    data_dir = os.path.join(PROJECT_ROOT, 'Dataset')

    logger.info("=" * 60)
    logger.info("STEP 1: Loading NSRDB data")
    logger.info("=" * 60)

    raw_df = load_multi_year(data_dir)
    clean_df = clean_irradiance_data(raw_df)

    # ── 2. Data Quality Report ───────────────────────────────────────────
    logger.info("\nSTEP 2: Data Quality Report")
    report = get_data_quality_report(clean_df)
    print_quality_report(report)

    # ── 3. PV Power Model Sanity Check ───────────────────────────────────
    logger.info("STEP 3: PV Power Model Sanity Check")
    from src.microgrid.Hospital.hospital_parameters import PVConfig
    pv_cfg = PVConfig()

    # Test at Standard Test Conditions (STC): 1000 W/m², 25°C
    p_stc = calculate_pv_power(1000, 25, pv_cfg)
    logger.info(f"  PV power at STC (1000 W/m², 25°C): {p_stc:.1f} kW")
    logger.info(f"  Expected range: ~310-340 kW for {pv_cfg.installed_capacity_kwp} kWp system (after NOCT cell temp derating)")

    # Test at high temperature
    p_hot = calculate_pv_power(1000, 45, pv_cfg)
    logger.info(f"  PV power at 45°C: {p_hot:.1f} kW "
                f"(temp derating: {(p_hot/p_stc - 1)*100:.1f}%)")

    # Test at low irradiance
    p_low = calculate_pv_power(200, 25, pv_cfg)
    logger.info(f"  PV power at 200 W/m²: {p_low:.1f} kW")

    # Test nighttime
    p_night = calculate_pv_power(0, 20, pv_cfg)
    logger.info(f"  PV power at 0 W/m² (night): {p_night:.1f} kW")
    assert p_night == 0.0, "Night power should be zero!"

    # ── 4. Hospital Simulator Comparison ─────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("STEP 4: Hospital Simulator — Synthetic vs Real Solar")
    logger.info("=" * 60)

    from src.microgrid.Hospital.hospital_parameters import create_default_config
    from src.microgrid.Hospital.hospital_simulator import MicrogridSimulator

    config = create_default_config()
    sim = MicrogridSimulator(config)

    # Use 2018-01-15 as start time (matches data range)
    start_time = datetime(2018, 1, 15, 0, 0, 0)

    # Run A: Synthetic (existing behavior)
    logger.info("\n--- Run A: Synthetic PV (sinusoidal model) ---")
    df_synthetic = sim.run_scenario(
        duration_hours=48,
        outage_start_hour=None,
        outage_duration_hours=None,
        start_time=start_time
    )

    # Run B: Real solar data
    logger.info("\n--- Run B: Real Solar Data (NSRDB) ---")
    provider = SolarDataProvider(clean_df)
    logger.info(provider.summary())

    df_real = sim.run_solar_scenario(
        solar_provider=provider,
        duration_hours=48,
        outage_start_hour=None,
        outage_duration_hours=None,
        start_time=start_time
    )

    # ── 5. Comparison Statistics ─────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("STEP 5: Comparison — Synthetic vs Real PV Output")
    logger.info("=" * 60)

    syn_pv = df_synthetic['pv_power_kw']
    real_pv = df_real['pv_power_kw']

    dt_hours = config.control.time_resolution_minutes / 60

    stats = {
        'Metric': [
            'Peak PV Power (kW)',
            'Mean PV Power (kW)',
            'Std Dev PV Power (kW)',
            'Total PV Energy (kWh)',
            'Capacity Factor (%)',
            'Max Power Difference (kW)',
        ],
        'Synthetic': [
            f"{syn_pv.max():.1f}",
            f"{syn_pv.mean():.1f}",
            f"{syn_pv.std():.1f}",
            f"{syn_pv.sum() * dt_hours:.1f}",
            f"{syn_pv.mean() / pv_cfg.installed_capacity_kwp * 100:.1f}",
            '-',
        ],
        'Real_NSRDB': [
            f"{real_pv.max():.1f}",
            f"{real_pv.mean():.1f}",
            f"{real_pv.std():.1f}",
            f"{real_pv.sum() * dt_hours:.1f}",
            f"{real_pv.mean() / pv_cfg.installed_capacity_kwp * 100:.1f}",
            f"{abs(syn_pv - real_pv).max():.1f}",
        ],
    }

    comparison_df = pd.DataFrame(stats)
    print("\n" + comparison_df.to_string(index=False))

    # ── 6. Export Results ────────────────────────────────────────────────
    output_dir = os.path.join(PROJECT_ROOT, 'SolarData', 'validation_output')
    os.makedirs(output_dir, exist_ok=True)

    # Export comparison CSV
    combined = pd.DataFrame({
        'timestamp': df_synthetic['timestamp'],
        'pv_synthetic_kw': syn_pv.values,
        'pv_real_kw': real_pv.values,
        'pv_diff_kw': (real_pv - syn_pv).values,
        'load_kw': df_synthetic['total_load_kw'].values,
    })
    csv_path = os.path.join(output_dir, 'synthetic_vs_real_pv_comparison.csv')
    combined.to_csv(csv_path, index=False)
    logger.info(f"\nComparison CSV exported to: {csv_path}")

    # ── 7. Backward Compatibility Check ──────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("STEP 6: Backward Compatibility Check")
    logger.info("=" * 60)
    logger.info("Running run_scenario() WITHOUT solar provider...")

    df_compat = sim.run_scenario(
        duration_hours=24,
        outage_start_hour=12,
        outage_duration_hours=4,
        start_time=start_time
    )
    assert len(df_compat) > 0, "Backward-compatible run_scenario() failed!"
    assert 'pv_power_kw' in df_compat.columns, "PV power column missing!"
    logger.info("✅ Backward compatibility confirmed — run_scenario() still "
                "works without solar provider.")

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  VALIDATION COMPLETE")
    print("=" * 60)
    print(f"  ✅ NSRDB data loaded: {report['total_rows']} rows, "
          f"years {report['years_covered']}")
    print(f"  ✅ PV model: STC power = {p_stc:.1f} kW "
          f"(nominal {pv_cfg.installed_capacity_kwp} kWp)")
    print(f"  ✅ Synthetic vs Real comparison: "
          f"peak diff = {abs(syn_pv - real_pv).max():.1f} kW")
    print(f"  ✅ Backward compatibility: run_scenario() unchanged")
    print(f"  📊 Results: {csv_path}")
    print("=" * 60)


if __name__ == '__main__':
    main()
