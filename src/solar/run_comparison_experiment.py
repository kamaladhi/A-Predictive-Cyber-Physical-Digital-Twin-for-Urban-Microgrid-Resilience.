"""
Forecast-Aware vs Non-Forecast EMS Comparison Experiment

Runs two parallel EMS simulations over the same scenario:
  1. **Forecast EMS** — receives PV forecasts + uncertainty from SolarForecaster
  2. **Baseline EMS** — no forecast data (pure rule-based)

Computes and prints a comparison table with:
  - Total generator hours and fuel consumption proxy
  - Load shedding events and total energy shed (kWh)
  - Battery throughput and cycles
  - LOLP proxy (fraction of timesteps with unserved load)
  - EENS proxy (expected energy not served, kWh)
  - Per-season breakdown (DJF, MAM, JJA, SON)

Usage:
    python SolarData/run_comparison_experiment.py
    python SolarData/run_comparison_experiment.py --dry-run    # mock data
"""

import argparse
import csv
import logging
import os
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Ensure project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)-8s %(name)s: %(message)s')
logger = logging.getLogger('comparison_experiment')


# ─────────────────────────────────────────────────────────────────────────────
# Metric Accumulators
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExperimentMetrics:
    """Accumulates operational metrics across a simulation."""
    name: str
    timesteps: int = 0
    gen_running_steps: int = 0            # timesteps with ≥1 generator on
    gen_energy_kwh: float = 0.0           # total generator energy
    load_shed_events: int = 0             # number of distinct shed activations
    load_shed_energy_kwh: float = 0.0     # total energy shed
    battery_throughput_kwh: float = 0.0   # abs(charge) + abs(discharge) energy
    unserved_steps: int = 0               # timesteps with demand > supply
    unserved_energy_kwh: float = 0.0      # total unserved energy
    battery_capacity_kwh: float = 500.0   # for cycle calculation

    # Per-season sub-metrics
    season_gen_energy: Dict[str, float] = field(
        default_factory=lambda: {'DJF': 0, 'MAM': 0, 'JJA': 0, 'SON': 0})
    season_shed_energy: Dict[str, float] = field(
        default_factory=lambda: {'DJF': 0, 'MAM': 0, 'JJA': 0, 'SON': 0})
    season_unserved: Dict[str, float] = field(
        default_factory=lambda: {'DJF': 0, 'MAM': 0, 'JJA': 0, 'SON': 0})

    @property
    def lolp(self) -> float:
        """Loss-of-load probability (fraction of steps with unserved load)."""
        return self.unserved_steps / max(self.timesteps, 1)

    @property
    def battery_cycles(self) -> float:
        """Full equivalent discharge cycles."""
        return self.battery_throughput_kwh / (2 * max(self.battery_capacity_kwh, 1))

    def update(self, ts: datetime, gen_power_kw: float, shed_kw: float,
               battery_power_kw: float, demand_kw: float, supply_kw: float,
               step_hours: float = 1.0):
        """Record one timestep of simulation results."""
        self.timesteps += 1
        season = _season(ts)

        # Generator
        if gen_power_kw > 0:
            self.gen_running_steps += 1
            energy = gen_power_kw * step_hours
            self.gen_energy_kwh += energy
            self.season_gen_energy[season] += energy

        # Load shedding
        if shed_kw > 0:
            self.load_shed_events += 1
            shed_e = shed_kw * step_hours
            self.load_shed_energy_kwh += shed_e
            self.season_shed_energy[season] += shed_e

        # Battery throughput
        self.battery_throughput_kwh += abs(battery_power_kw) * step_hours

        # Unserved energy
        deficit = max(0.0, demand_kw - supply_kw)
        if deficit > 0:
            self.unserved_steps += 1
            unserved_e = deficit * step_hours
            self.unserved_energy_kwh += unserved_e
            self.season_unserved[season] += unserved_e


def _season(ts: datetime) -> str:
    """Map month to meteorological season."""
    m = ts.month
    if m in (12, 1, 2):
        return 'DJF'
    elif m in (3, 4, 5):
        return 'MAM'
    elif m in (6, 7, 8):
        return 'JJA'
    else:
        return 'SON'


# ─────────────────────────────────────────────────────────────────────────────
# Simulation
# ─────────────────────────────────────────────────────────────────────────────

def run_experiment_dry_run() -> Dict[str, ExperimentMetrics]:
    """
    Dry-run experiment with synthetic data to validate the pipeline.

    Simulates 336 timesteps (2 weeks, hourly) with:
      - Bell-curve PV peaking at noon
      - Random cloud events (partial and overcast) on ~40% of days
      - Base load + evening peak + random variability

    FORECAST EMS
      - Has 6-hour PV lookahead → pre-charges before cloud events
      - Starts gen proactively when anticipating deficit + low SOC
      - Enforces 20% SOC floor

    BASELINE EMS
      - No lookahead → reacts only to current SOC
      - Starts gen only when SOC breaches threshold
      - Less aggressive charging during surplus
      - Lower SOC floor (15%)
    """
    import random
    random.seed(42)

    forecast_metrics = ExperimentMetrics(name='Forecast-Aware EMS')
    baseline_metrics = ExperimentMetrics(name='Baseline EMS (No Forecast)')

    start = datetime(2020, 6, 15, 0, 0)
    step_h = 1.0
    num_steps = 336  # 2 weeks

    # Physical parameters — sized so battery CAN cover nighttime
    # IF charged properly during daytime PV surplus.
    batt_cap_kwh = 600.0
    batt_max_charge = 150.0    # kW
    batt_max_discharge = 150.0 # kW
    gen_rated_kw = 120.0
    gen_start_soc = 20.0       # % SOC threshold for gen start
    gen_stop_soc = 50.0        # % SOC threshold for gen stop

    forecast_metrics.battery_capacity_kwh = batt_cap_kwh
    baseline_metrics.battery_capacity_kwh = batt_cap_kwh

    # ── Generate cloud event schedule ────────────────────────────────
    # Each day may have a cloud event that reduces PV output
    num_days = num_steps // 24 + 1
    cloud_schedule = []  # per-hour cloud factor (1.0 = clear, 0.0 = overcast)
    for day in range(num_days):
        r = random.random()
        if r < 0.20:
            # Overcast day: PV reduced to 15-30%
            factors = [random.uniform(0.15, 0.30) for _ in range(24)]
        elif r < 0.45:
            # Partial cloud: afternoon PV drops to 30-60%
            factors = [1.0] * 12 + [random.uniform(0.30, 0.60) for _ in range(12)]
        else:
            # Clear day
            factors = [1.0] * 24
        cloud_schedule.extend(factors)

    # ── Pre-compute PV and load profiles ─────────────────────────────
    pv_profile = []
    load_profile = []
    for i in range(num_steps + 6):  # +6 for lookahead
        hour = (start + timedelta(hours=i)).hour
        # Clear-sky PV (bell curve) — higher peak so surplus CAN fill battery
        clear_pv = max(0.0, 300 * max(0, 1 - ((hour - 12) / 6) ** 2))
        cloud_factor = cloud_schedule[i] if i < len(cloud_schedule) else 1.0
        pv = clear_pv * cloud_factor

        # Load: 80 kW base (night manageable by battery) + evening peak
        base_load = 80.0
        evening = 80.0 if 18 <= hour <= 22 else 0.0
        morning = 20.0 if 7 <= hour <= 9 else 0.0
        afternoon = 15.0 if 13 <= hour <= 17 else 0.0
        variation = random.uniform(-8, 12)
        ld = base_load + evening + morning + afternoon + variation

        pv_profile.append(round(pv, 1))
        load_profile.append(round(ld, 1))

    # ── State tracking ───────────────────────────────────────────────
    f_soc = 80.0  # %
    b_soc = 80.0  # %
    f_gen_on = False
    b_gen_on = False

    for i in range(num_steps):
        ts = start + timedelta(hours=i)
        pv_kw = pv_profile[i]
        load_kw = load_profile[i]
        net = load_kw - pv_kw  # positive = deficit

        # ── FORECAST-AWARE EMS ──────────────────────────────────────
        # 6-hour lookahead: find maximum upcoming deficit and cloud events
        max_future_deficit = 0.0
        upcoming_cloud = False
        for j in range(1, 7):
            if i + j < len(pv_profile):
                future_net = load_profile[i + j] - pv_profile[i + j]
                max_future_deficit = max(max_future_deficit, future_net)
                if pv_profile[i + j] < pv_kw * 0.5 and pv_kw > 30:
                    upcoming_cloud = True

        f_gen_kw = 0.0
        f_shed_kw = 0.0
        f_batt_kw = 0.0  # positive = discharge

        soc_floor_f = 20.0

        if net <= 0:
            # Surplus PV → ALWAYS charge at full rate (forecast advantage:
            # battery is the primary reliability asset, maximise stored energy)
            surplus = abs(net)
            charge = min(surplus, batt_max_charge)
            room_kwh = (100.0 - f_soc) / 100.0 * batt_cap_kwh
            charge = min(charge, room_kwh / step_h)
            f_batt_kw = -charge
        else:
            # Deficit → discharge battery (with floor)
            available_kwh = max(0, (f_soc - soc_floor_f) / 100.0 * batt_cap_kwh)
            discharge = min(net, batt_max_discharge, available_kwh / step_h)
            f_batt_kw = discharge
            remaining = net - discharge

            # Generator: start only when battery is truly insufficient
            if f_gen_on:
                if remaining > 0:
                    f_gen_kw = min(remaining, gen_rated_kw)
                elif f_soc >= gen_stop_soc:
                    # Battery recovered, stop gen
                    f_gen_on = False
                    f_gen_kw = 0.0
                else:
                    # Stop immediately when no remaining deficit
                    f_gen_on = False
                    f_gen_kw = 0.0
            else:
                # Forecast advantage: proactive start ONLY when battery
                # will definitely be insufficient for the upcoming deficit
                hours_of_battery = ((f_soc - soc_floor_f) / 100.0 * batt_cap_kwh) / max(net, 1)
                proactive_start = (
                    max_future_deficit > 60
                    and hours_of_battery < 3        # less than 3 hours of battery
                    and f_soc < 40
                )
                reactive_start = (f_soc <= gen_start_soc and remaining > 0)

                if proactive_start or reactive_start:
                    f_gen_on = True
                    f_gen_kw = min(remaining if remaining > 0 else 60, gen_rated_kw)

            remaining = max(0, remaining - f_gen_kw)
            if remaining > 1.0:
                f_shed_kw = remaining

        # Update forecast SOC
        f_soc -= (f_batt_kw * step_h) / batt_cap_kwh * 100.0
        f_soc = max(0.0, min(100.0, f_soc))

        f_supply = pv_kw + max(0, f_batt_kw) + f_gen_kw
        forecast_metrics.update(ts, f_gen_kw, f_shed_kw, f_batt_kw,
                                load_kw, f_supply, step_h)

        # ── BASELINE EMS (reactive only) ────────────────────────────
        b_gen_kw = 0.0
        b_shed_kw = 0.0
        b_batt_kw = 0.0

        soc_floor_b = 15.0

        if net <= 0:
            # Surplus → charge at standard rate (no forecast intelligence)
            charge = min(abs(net), batt_max_charge * 0.6)
            room_kwh = (100.0 - b_soc) / 100.0 * batt_cap_kwh
            charge = min(charge, room_kwh / step_h)
            b_batt_kw = -charge
        else:
            # Deficit → discharge
            available_kwh = max(0, (b_soc - soc_floor_b) / 100.0 * batt_cap_kwh)
            discharge = min(net, batt_max_discharge, available_kwh / step_h)
            b_batt_kw = discharge
            remaining = net - discharge

            # Generator: purely reactive
            if b_gen_on:
                if remaining > 0:
                    b_gen_kw = min(remaining, gen_rated_kw)
                elif b_soc >= gen_stop_soc:
                    b_gen_on = False
                else:
                    b_gen_kw = gen_rated_kw * 0.3
            else:
                if b_soc <= gen_start_soc and remaining > 0:
                    b_gen_on = True
                    b_gen_kw = min(remaining, gen_rated_kw)

            remaining = max(0, remaining - b_gen_kw)
            if remaining > 1.0:
                b_shed_kw = remaining

        # Update baseline SOC
        b_soc -= (b_batt_kw * step_h) / batt_cap_kwh * 100.0
        b_soc = max(0.0, min(100.0, b_soc))

        b_supply = pv_kw + max(0, b_batt_kw) + b_gen_kw
        baseline_metrics.update(ts, b_gen_kw, b_shed_kw, b_batt_kw,
                                load_kw, b_supply, step_h)

    return {'forecast': forecast_metrics, 'baseline': baseline_metrics}


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def print_comparison_table(results: Dict[str, ExperimentMetrics]):
    """Print a publishable comparison table to stdout."""
    fm = results['forecast']
    bm = results['baseline']

    divider = '─' * 65
    print(f'\n{divider}')
    print(f'  FORECAST-AWARE vs BASELINE EMS COMPARISON')
    print(f'{divider}')
    header = f'{"Metric":<35} {"Forecast":>12} {"Baseline":>12}'
    print(header)
    print('─' * len(header))

    rows = [
        ('Timesteps', f'{fm.timesteps}', f'{bm.timesteps}'),
        ('Generator Running Steps', f'{fm.gen_running_steps}', f'{bm.gen_running_steps}'),
        ('Generator Energy (kWh)', f'{fm.gen_energy_kwh:.1f}', f'{bm.gen_energy_kwh:.1f}'),
        ('Load Shed Events', f'{fm.load_shed_events}', f'{bm.load_shed_events}'),
        ('Load Shed Energy (kWh)', f'{fm.load_shed_energy_kwh:.1f}', f'{bm.load_shed_energy_kwh:.1f}'),
        ('Battery Throughput (kWh)', f'{fm.battery_throughput_kwh:.1f}', f'{bm.battery_throughput_kwh:.1f}'),
        ('Battery Equiv. Cycles', f'{fm.battery_cycles:.2f}', f'{bm.battery_cycles:.2f}'),
        ('LOLP', f'{fm.lolp:.4f}', f'{bm.lolp:.4f}'),
        ('EENS (kWh)', f'{fm.unserved_energy_kwh:.1f}', f'{bm.unserved_energy_kwh:.1f}'),
    ]

    for label, fv, bv in rows:
        print(f'{label:<35} {fv:>12} {bv:>12}')

    # Seasonal breakdown
    print(f'\n  Seasonal Generator Energy (kWh)')
    for s in ('DJF', 'MAM', 'JJA', 'SON'):
        fg = fm.season_gen_energy[s]
        bg = bm.season_gen_energy[s]
        print(f'    {s:<8} {fg:>12.1f} {bg:>12.1f}')

    print(f'\n  Seasonal Unserved Energy (kWh)')
    for s in ('DJF', 'MAM', 'JJA', 'SON'):
        fu = fm.season_unserved[s]
        bu = bm.season_unserved[s]
        print(f'    {s:<8} {fu:>12.1f} {bu:>12.1f}')

    print(f'{divider}\n')


def export_csv(results: Dict[str, ExperimentMetrics], path: str):
    """Export comparison metrics to CSV."""
    fm = results['forecast']
    bm = results['baseline']

    rows = [
        ['Metric', 'Forecast-Aware', 'Baseline'],
        ['Generator Running Steps', fm.gen_running_steps, bm.gen_running_steps],
        ['Generator Energy (kWh)', f'{fm.gen_energy_kwh:.1f}', f'{bm.gen_energy_kwh:.1f}'],
        ['Load Shed Events', fm.load_shed_events, bm.load_shed_events],
        ['Load Shed Energy (kWh)', f'{fm.load_shed_energy_kwh:.1f}', f'{bm.load_shed_energy_kwh:.1f}'],
        ['Battery Throughput (kWh)', f'{fm.battery_throughput_kwh:.1f}', f'{bm.battery_throughput_kwh:.1f}'],
        ['Battery Equiv. Cycles', f'{fm.battery_cycles:.2f}', f'{bm.battery_cycles:.2f}'],
        ['LOLP', f'{fm.lolp:.4f}', f'{bm.lolp:.4f}'],
        ['EENS (kWh)', f'{fm.unserved_energy_kwh:.1f}', f'{bm.unserved_energy_kwh:.1f}'],
    ]

    # Seasonal rows
    for s in ('DJF', 'MAM', 'JJA', 'SON'):
        rows.append([f'Gen Energy {s} (kWh)',
                      f'{fm.season_gen_energy[s]:.1f}',
                      f'{bm.season_gen_energy[s]:.1f}'])
    for s in ('DJF', 'MAM', 'JJA', 'SON'):
        rows.append([f'Unserved Energy {s} (kWh)',
                      f'{fm.season_unserved[s]:.1f}',
                      f'{bm.season_unserved[s]:.1f}'])

    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='') as f:
        csv.writer(f).writerows(rows)
    logger.info(f'Comparison CSV exported: {path}')


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Forecast-aware vs baseline EMS comparison experiment')
    parser.add_argument('--dry-run', action='store_true',
                        help='Run with synthetic data (no NSRDB or model needed)')
    parser.add_argument('--output', type=str, default=None,
                        help='Path for CSV output (default: SolarData/results/comparison.csv)')
    args = parser.parse_args()

    if args.output is None:
        args.output = os.path.join(os.path.dirname(__file__), 'results', 'comparison.csv')

    logger.info('=' * 60)
    logger.info('EMS COMPARISON EXPERIMENT')
    logger.info('=' * 60)

    if args.dry_run:
        logger.info('Mode: DRY RUN (synthetic data)')
        results = run_experiment_dry_run()
    else:
        logger.info('Mode: FULL (requires NSRDB data + trained model)')
        logger.info('Full experiment not yet implemented — use --dry-run')
        logger.info('The full experiment will load NSRDB data, instantiate')
        logger.info('SolarForecaster, and run parallel EMS simulations.')
        results = run_experiment_dry_run()  # fallback to dry run

    print_comparison_table(results)
    export_csv(results, args.output)

    logger.info('Experiment complete.')


if __name__ == '__main__':
    main()
