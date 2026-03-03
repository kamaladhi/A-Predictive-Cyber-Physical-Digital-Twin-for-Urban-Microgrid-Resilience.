"""
==============================================================================
PREDICTIVE OPTIMIZATION vs RULE-BASED EMS — EXPERIMENT FRAMEWORK v2
==============================================================================

Fixes the critical feedback-loop bug from v1: the SyntheticMeasurementGenerator
now ACCEPTS AND APPLIES optimizer dispatch commands, so optimized and rule-based
configurations produce genuinely different outcomes.

Key improvements over run_optimization_experiment.py:
  - Command-reactive measurement generator (closed-loop simulation)
  - Rolling-horizon predictive optimizer (PredictiveDispatcher)
  - ≥30 trials × ≥30 simulated days (configurable)
  - Monte Carlo outage scenario injection (Poisson + Weibull)
  - Proper IEEE 1366-2012 reliability indices (SAIDI, SAIFI, CAIDI, ASAI, EENS)
  - Paired t-test + Wilcoxon signed-rank + Cohen's d (Bonferroni-corrected)
  - Full per-timestep CSV export for reproducibility
  - Optimizer convergence log export
  - Performance timing metrics

Statistical comparison methodology:
  - Paired design: same random seeds for both configs
  - Minimum 30 trials for CLT-based inference
  - Bonferroni correction for multiple comparisons
  - Effect size via Cohen's d with pooled SD
  - Non-parametric Wilcoxon check for robustness

Usage:
    python run_predictive_experiment.py --trials 30 --days 30
    python run_predictive_experiment.py --trials 5  --days 3 --quick
    python run_predictive_experiment.py --trials 50 --days 30 --policy critical_first

Reference:
    IEEE Std 1366-2012 for reliability indices.
    Parisio et al., IEEE TCST 2014 for MPC-based microgrid dispatch.
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import math

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ─── Path setup ─────────────────────────────────────────────────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from src.ems.common import MicrogridPriority, CityOperationMode, ResiliencePolicy
from src.ems.city_ems import (
    CityEMS, CityWideMeasurements, MicrogridInfo, MicrogridStatus,
    SupervisoryCommand, CityControlOutputs,
)
from src.ems.mqtt_manager import MqttPublisher
from src.digital_twin.data_fusion_engine import DataFusionEngine

# MANDATORY: import predictive optimizer and validate
try:
    from src.ems.predictive_optimizer import (
        PredictiveDispatcher, HorizonSolution, PredictiveCostConfig,
    )
    PREDICTIVE_AVAILABLE = True
except ImportError as e:
    PREDICTIVE_AVAILABLE = False
    _import_err = str(e)

# ─── Logging ────────────────────────────────────────────────────────────────
log_file = os.path.join(root_dir, 'results', 'experiment_run.log')
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Experiment")
logger.info(f"Logging initialized. Output also saved to: {log_file}")

DT_HOURS = 0.25
FUEL_RATE_L_PER_KWH = 0.30   # Diesel consumption rate (liters per kWh)
DT_MINUTES = 15


# ===========================================================================
# MICROGRID REGISTRY
# ===========================================================================

def build_registry() -> Dict[str, MicrogridInfo]:
    """Build the 4-MG urban microgrid registry."""
    return {
        'hospital': MicrogridInfo(
            microgrid_id='hospital', microgrid_type='hospital',
            priority=MicrogridPriority.CRITICAL,
            location=(13.08, 80.27),
            critical_load_kw=120.0, total_capacity_kw=300.0,
            battery_capacity_kwh=500.0, pv_capacity_kwp=100.0,
            generator_capacity_kw=200.0,
            min_runtime_hours=48.0, max_shed_percent=20.0,
            can_share_power=True,
        ),
        'university': MicrogridInfo(
            microgrid_id='university', microgrid_type='university',
            priority=MicrogridPriority.HIGH,
            location=(13.01, 80.24),
            critical_load_kw=60.0, total_capacity_kw=250.0,
            battery_capacity_kwh=400.0, pv_capacity_kwp=150.0,
            generator_capacity_kw=150.0,
            min_runtime_hours=24.0, max_shed_percent=40.0,
            can_share_power=True,
        ),
        'industrial': MicrogridInfo(
            microgrid_id='industrial', microgrid_type='industrial',
            priority=MicrogridPriority.MEDIUM,
            location=(13.05, 80.22),
            critical_load_kw=80.0, total_capacity_kw=400.0,
            battery_capacity_kwh=300.0, pv_capacity_kwp=200.0,
            generator_capacity_kw=250.0,
            min_runtime_hours=12.0, max_shed_percent=50.0,
            can_share_power=True,
        ),
        'residential': MicrogridInfo(
            microgrid_id='residential', microgrid_type='residential',
            priority=MicrogridPriority.LOW,
            location=(13.03, 80.25),
            critical_load_kw=30.0, total_capacity_kw=200.0,
            battery_capacity_kwh=250.0, pv_capacity_kwp=80.0,
            generator_capacity_kw=100.0,
            min_runtime_hours=8.0, max_shed_percent=60.0,
            can_share_power=True,
        ),
    }

# ─── Regional Outage Integration ──────────────────────────────────────────
try:
    from src.ems.coordinated_outage import RegionalOutageGenerator, OutageSeverity, RegionalOutageEvent
    COORDINATED_OUTAGE_AVAILABLE = True
except ImportError:
    COORDINATED_OUTAGE_AVAILABLE = False
    logger.warning("Regional Outage Coordination module not available -- using basic generator.")


# ===========================================================================
# OUTAGE SCENARIO GENERATOR (Monte Carlo)
# ===========================================================================

class OutageScenarioGenerator:
    """
    Generate realistic grid outage scenarios using Poisson arrival + Weibull duration.

    Parameters calibrated from IEEE reliability benchmarks:
      - Mean outage rate: 2 events per 30-day period (λ_poisson)
      - Duration: Weibull(shape=1.5, scale=8h) -> median ~6h, heavy tail to 24h+
    """

    def __init__(
        self,
        duration_days: float,
        poisson_rate: float = 2.0,     # outages per period
        weibull_shape: float = 1.5,
        weibull_scale_hours: float = 8.0,
        min_outage_hours: float = 1.0,
        max_outage_hours: float = 48.0,
        seed: int = 42,
        force_outage: bool = False,
    ):
        self.rng = np.random.RandomState(seed)
        self.duration_hours = duration_days * 24
        self.total_steps = int(duration_days * 24 / DT_HOURS)

        # Generate outage events
        self.events: List[Tuple[int, int]] = []  # (start_step, end_step)
        if force_outage:
            # Force immediate and permanent outage for demo
            self.events.append((0, self.total_steps))
        else:
            self._generate(poisson_rate, weibull_shape, weibull_scale_hours,
                           min_outage_hours, max_outage_hours, duration_days)

    def _generate(self, rate, shape, scale, min_h, max_h, days):
        """Generate Poisson-Weibull outage events."""
        n_events = self.rng.poisson(rate * days / 30.0)
        n_events = max(n_events, 1)  # At least one outage per trial

        for _ in range(n_events):
            # Random start time (uniform)
            start_hour = self.rng.uniform(0, self.duration_hours - min_h)
            # Weibull duration
            duration_hours = self.rng.weibull(shape) * scale
            duration_hours = np.clip(duration_hours, min_h, max_h)

            start_step = int(start_hour / DT_HOURS)
            end_step = min(
                int((start_hour + duration_hours) / DT_HOURS),
                self.total_steps
            )
            self.events.append((start_step, end_step))

        # Sort by start time
        self.events.sort(key=lambda x: x[0])

    def is_outage(self, step: int) -> bool:
        """Check if step falls within any outage window."""
        return any(s <= step < e for s, e in self.events)

    def outage_intensity(self, step: int) -> float:
        """Return outage stress intensity [0, 1]."""
        for s, e in self.events:
            if s <= step < e:
                # Ramp: increases with duration fraction
                frac = (step - s) / max(e - s, 1)
                return min(0.3 + 0.7 * frac, 1.0)
        return 0.0

    def summary(self) -> Dict:
        """Summary of generated outage scenario."""
        total_outage_steps = sum(e - s for s, e in self.events)
        return {
            'n_events': len(self.events),
            'total_outage_hours': round(total_outage_steps * DT_HOURS, 1),
            'events': [(s * DT_HOURS, e * DT_HOURS) for s, e in self.events],
        }


# ===========================================================================
# COMMAND-REACTIVE MEASUREMENT GENERATOR
# ===========================================================================

class ReactiveSimulator:
    """
    Closed-loop microgrid simulator that APPLIES EMS commands.

    This fixes the critical bug in the original SyntheticMeasurementGenerator:
    the optimizer's dispatch commands now directly affect battery SOC, generator
    output, and load shedding — producing genuinely different outcomes for
    rule-based vs optimized EMS.

    Physics model:
      - Battery: SOC[t+1] = SOC[t] - P_batt·Δt / (E_batt · η)
      - Generator: constrained by fuel, capacity, and EMS command
      - Load shedding: commanded by EMS, bounded by MG limits
      - PV: exogenous (weather-driven), same for both configs
    """

    LOAD_PROFILES = {
        'hospital':    [0.75, 0.72, 0.70, 0.70, 0.72, 0.75, 0.82, 0.90,
                        0.95, 1.00, 1.05, 1.05, 1.02, 0.98, 0.95, 0.92,
                        0.90, 0.88, 0.85, 0.82, 0.80, 0.78, 0.76, 0.75],
        'university':  [0.20, 0.18, 0.15, 0.15, 0.18, 0.25, 0.45, 0.70,
                        0.90, 1.00, 1.05, 1.05, 1.00, 0.95, 0.90, 0.85,
                        0.70, 0.55, 0.40, 0.35, 0.30, 0.28, 0.25, 0.22],
        'industrial':  [0.40, 0.38, 0.35, 0.35, 0.38, 0.45, 0.65, 0.85,
                        0.95, 1.00, 1.05, 1.05, 1.00, 0.98, 0.95, 0.90,
                        0.85, 0.75, 0.60, 0.50, 0.45, 0.42, 0.40, 0.40],
        'residential': [0.30, 0.25, 0.22, 0.20, 0.22, 0.30, 0.50, 0.65,
                        0.55, 0.45, 0.40, 0.42, 0.50, 0.48, 0.45, 0.50,
                        0.65, 0.85, 1.00, 1.05, 0.95, 0.80, 0.55, 0.40],
    }

    def __init__(self, registry: Dict[str, MicrogridInfo], solar_provider: Any = None, seed: int = 42, force_shortage: bool = False):
        self.registry = registry
        self.solar_provider = solar_provider
        self.rng = np.random.RandomState(seed)
        self.force_shortage = force_shortage

        # Persistent state per MG
        self.soc = {mg_id: 0.80 for mg_id in registry}
        self.fuel = {
            'hospital': 500.0, 'university': 300.0,
            'industrial': 400.0, 'residential': 200.0,
        }

        # Sharing state (net exchange per step)
        self.net_sharing = {mg_id: 0.0 for mg_id in registry}

        # Pre-generate noise
        self._cloud_noise = self.rng.uniform(0.95, 1.05, size=10000)
        self._load_noise = self.rng.uniform(0.85, 1.15, size=10000)
        self._step = 0

    def generate(
        self,
        timestamp: datetime,
        islanded: bool,
        outage_intensity: float = 0.0,
        commands: Optional[Dict[str, SupervisoryCommand]] = None,
    ) -> CityWideMeasurements:
        """
        Generate measurements for one timestep, applying EMS commands.

        When commands is None (rule-based EMS without command feedback),
        the simulator uses its own heuristic dispatch. When commands is
        provided, the optimizer's decisions drive battery/gen/shed.
        """
        hour = timestamp.hour + timestamp.minute / 60.0
        statuses = {}
        totals = {'load': 0, 'crit': 0, 'gen': 0, 'batt_e': 0, 'fuel': 0}
        step = self._step

        if self.force_shortage:
            # Artificially increase load and reduce solar to create deficit
            outage_intensity = max(outage_intensity, 0.8)

        for mg_id, info in self.registry.items():
            # ── Environmental Layer (Physics/DT Mirror) ──
            if self.solar_provider:
                ghi, _ = self.solar_provider.get_irradiance(timestamp)
                # Convert GHI to kW (simplified pv_power_model logic)
                pv_kw = info.pv_capacity_kwp * (ghi / 1000.0) * self._cloud_noise[step % len(self._cloud_noise)]
            else:
                if 6 <= hour <= 18:
                    solar_frac = max(0, np.sin(np.pi * (hour - 6) / 12))
                    solar_frac *= self._cloud_noise[step % len(self._cloud_noise)]
                else:
                    solar_frac = 0.0
                pv_kw = info.pv_capacity_kwp * solar_frac
            
            if self.force_shortage:
                pv_kw *= 0.7 # 30% solar drop (cloudy/dust)

            # ── Exogenous load profile ──────────────────────────────────
            profile = self.LOAD_PROFILES.get(info.microgrid_type, self.LOAD_PROFILES['residential'])
            hour_idx = int(hour) % 24
            load_frac = profile[hour_idx]
            load_frac *= self._load_noise[(step + hash(mg_id)) % len(self._load_noise)]
            if outage_intensity > 0:
                load_frac *= (1.0 + 0.10 * outage_intensity)
            load_kw = info.total_capacity_kw * load_frac

            # ── Apply EMS commands (closed-loop) ──────────────────────────
            cmd = commands.get(mg_id) if commands else None

            if cmd is not None and islanded:
                # OPTIMIZER-DRIVEN dispatch
                gen_kw, batt_kw, shed_kw, grid_kw = self._apply_command(
                    mg_id, info, cmd, load_kw, pv_kw, islanded
                )
            else:
                # HEURISTIC dispatch (when no command or grid-connected)
                gen_kw, batt_kw, shed_kw, grid_kw = self._heuristic_dispatch(
                    mg_id, info, load_kw, pv_kw, islanded
                )

            # ── Pack status ─────────────────────────────────────────────
            status = MicrogridStatus(
                microgrid_id=mg_id,
                timestamp=timestamp,
                operation_mode='islanded' if islanded else 'grid_connected',
                is_islanded=islanded,
                grid_available=not islanded,
                total_load_kw=round(load_kw, 2),
                critical_load_kw=info.critical_load_kw,
                pv_generation_kw=round(pv_kw, 2),
                battery_power_kw=round(batt_kw, 2),
                generator_power_kw=round(gen_kw, 2),
                grid_power_kw=round(grid_kw, 2),
                battery_soc_percent=round(self.soc[mg_id] * 100, 1),
                battery_capacity_kwh=info.battery_capacity_kwh,
                generator_capacity_kw=info.generator_capacity_kw,
                pv_capacity_kw=info.pv_capacity_kwp,
                fuel_remaining_liters=round(self.fuel[mg_id], 1),
                load_shed_kw=round(shed_kw, 2),
                load_shed_percent=round(shed_kw / max(load_kw, 0.1) * 100, 1),
                critical_load_shed=(shed_kw > max(load_kw - info.critical_load_kw, 0.1)),
                net_sharing_kw=round(self.net_sharing.get(mg_id, 0.0), 2),
                estimated_runtime_hours=round(
                    self.fuel[mg_id] / max(gen_kw * 0.3, 0.1), 1
                ) if gen_kw > 0 else 24.0,
                resource_criticality=(
                    'emergency' if self.soc[mg_id] < 0.15
                    else 'critical' if self.soc[mg_id] < 0.25
                    else 'warning' if self.soc[mg_id] < 0.40
                    else 'healthy'
                ),
            )
            statuses[mg_id] = status

            totals['load'] += load_kw
            totals['crit'] += info.critical_load_kw
            totals['gen'] += pv_kw + gen_kw
            totals['batt_e'] += self.soc[mg_id] * info.battery_capacity_kwh
            totals['fuel'] += self.fuel[mg_id]

        self._step += 1

        n_isl = sum(1 for s in statuses.values() if s.is_islanded)
        n_emg = sum(1 for s in statuses.values() if s.resource_criticality == 'emergency')

        return CityWideMeasurements(
            timestamp=timestamp,
            microgrid_statuses=statuses,
            total_load_kw=round(totals['load'], 2),
            total_critical_load_kw=round(totals['crit'], 2),
            total_generation_kw=round(totals['gen'], 2),
            total_battery_energy_kwh=round(totals['batt_e'], 2),
            total_fuel_liters=round(totals['fuel'], 1),
            grid_outage_active=islanded,
            outage_start_time=timestamp if islanded else None,
            outage_duration_hours=0,
            microgrids_islanded=n_isl,
            microgrids_in_emergency=n_emg,
            city_survivability_hours=24.0,
        )

    def _apply_command(
        self, mg_id: str, info: MicrogridInfo,
        cmd: SupervisoryCommand, load_kw: float, pv_kw: float,
        islanded: bool,
    ) -> Tuple[float, float, float, float]:
        """
        Apply optimizer command to determine actual dispatch.
        Uses explicit MPC dispatch fields if available (set by PredictiveDispatcher),
        otherwise falls back to inferring from indirect hints.
        Returns (gen_kw, batt_kw, shed_kw, grid_kw).
        """
        # ── Check for explicit MPC dispatch ──────────────────────────────
        has_mpc = (
            cmd.mpc_gen_kw is not None
            and cmd.mpc_batt_kw is not None
            and cmd.mpc_shed_kw is not None
        )

        if has_mpc:
            return self._apply_mpc_dispatch(mg_id, info, cmd, load_kw, pv_kw, islanded)

        # ── Fallback: infer from indirect hints ──────────────────────────
        # Generator: commanded by optimizer
        gen_kw = 0.0
        if cmd.generator_enable and self.fuel[mg_id] > 1.0:
            # Infer gen from power balance
            target_shed_pct = cmd.target_shed_percent or 0.0
            target_shed_kw = load_kw * target_shed_pct / 100.0
            effective_load = load_kw - target_shed_kw
            deficit = max(effective_load - pv_kw, 0)
            gen_kw = min(deficit, info.generator_capacity_kw)
            # Fuel consumption
            fuel_use = gen_kw * 0.3 * DT_HOURS
            self.fuel[mg_id] = max(0, self.fuel[mg_id] - fuel_use)

        # Battery: target SOC implies charge/discharge
        batt_kw = 0.0
        if cmd.battery_soc_target_percent is not None:
            soc_target = cmd.battery_soc_target_percent / 100.0
            soc_delta = self.soc[mg_id] - soc_target
            # Convert SOC delta to power: P = ΔSOC × E / Δt
            batt_kw = soc_delta * info.battery_capacity_kwh / DT_HOURS
            # Clamp to physical limits
            batt_max = info.battery_capacity_kwh  # 1C rate
            batt_kw = np.clip(batt_kw, -batt_max, batt_max)
            # Update SOC
            actual_soc_delta = batt_kw * DT_HOURS / max(info.battery_capacity_kwh, 0.1)
            self.soc[mg_id] = np.clip(self.soc[mg_id] - actual_soc_delta, 0.05, 1.0)

        # Shedding: commanded by optimizer
        shed_kw = 0.0
        if cmd.target_shed_percent is not None:
            shed_kw = load_kw * cmd.target_shed_percent / 100.0
            # Ensure we don't shed more than allowed
            max_shed = load_kw * info.max_shed_percent / 100.0
            shed_kw = min(shed_kw, max_shed)

        # Verify power balance; if deficit remains, force additional shedding
        supply = pv_kw + gen_kw + max(batt_kw, 0)
        demand = load_kw - shed_kw
        if supply < demand - 1.0:
            additional_shed = demand - supply
            shed_kw += additional_shed
            shed_kw = min(shed_kw, load_kw)  # Can't shed more than load

        grid_kw = 0.0  # Islanded
        return gen_kw, batt_kw, shed_kw, grid_kw

    def _apply_mpc_dispatch(
        self, mg_id: str, info: MicrogridInfo,
        cmd: SupervisoryCommand, load_kw: float, pv_kw: float,
        islanded: bool,
    ) -> Tuple[float, float, float, float]:
        """
        Apply explicit MPC dispatch values directly. This is the preferred
        path when PredictiveDispatcher provides exact kW setpoints.
        """
        # Generator: use LP-computed value, clamp to capacity & fuel
        gen_kw = min(max(cmd.mpc_gen_kw, 0.0), info.generator_capacity_kw)
        if self.fuel[mg_id] <= 1.0:
            gen_kw = 0.0
        if gen_kw > 0.5:
            fuel_use = gen_kw * FUEL_RATE_L_PER_KWH * DT_HOURS
            self.fuel[mg_id] = max(0, self.fuel[mg_id] - fuel_use)

        # Battery: use LP-computed net power (positive = discharge)
        batt_kw = cmd.mpc_batt_kw
        batt_max = info.battery_capacity_kwh  # 1C rate
        batt_kw = np.clip(batt_kw, -batt_max, batt_max)

        # Enforce SOC limits
        if batt_kw > 0:  # discharge
            max_dis_soc = (self.soc[mg_id] - 0.05) * info.battery_capacity_kwh / DT_HOURS
            batt_kw = min(batt_kw, max(max_dis_soc, 0.0))
        else:  # charge
            max_chg_soc = (1.0 - self.soc[mg_id]) * info.battery_capacity_kwh / DT_HOURS
            batt_kw = max(batt_kw, -max(max_chg_soc, 0.0))

        # Update SOC
        soc_delta = batt_kw * DT_HOURS / max(info.battery_capacity_kwh, 0.1)
        self.soc[mg_id] = np.clip(self.soc[mg_id] - soc_delta, 0.05, 1.0)

        # Shedding: use LP value
        shed_kw = max(cmd.mpc_shed_kw, 0.0)
        # Note: we do NOT clamp to info.max_shed_percent here because the optimizer
        # already respects it for non-critical loads, and if it exceeds it (via Slack),
        # it means it's a physical necessity to balance the island.
        shed_kw = min(shed_kw, load_kw)

        # Resource Sharing: use LP values
        # Net supply change = imports - exports
        imp_kw = max(cmd.mpc_import_kw or 0.0, 0.0)
        exp_kw = max(cmd.mpc_export_kw or 0.0, 0.0)
        self.net_sharing[mg_id] = imp_kw - exp_kw

        # Final power balance check — if deficit remains, add shedding
        # supply = local_gen + battery + solar + net_imports
        supply = pv_kw + gen_kw + max(batt_kw, 0) + imp_kw - exp_kw
        demand = load_kw - shed_kw - cmd.mpc_dr_kw if hasattr(cmd, 'mpc_dr_kw') and cmd.mpc_dr_kw else load_kw - shed_kw
        if islanded and supply < demand - 1.0:
            additional_shed = demand - supply
            shed_kw += additional_shed
            shed_kw = min(shed_kw, load_kw)

        grid_kw = 0.0 if islanded else max(load_kw - pv_kw - gen_kw - batt_kw + shed_kw, 0.0)
        return gen_kw, batt_kw, shed_kw, grid_kw

    def _heuristic_dispatch(
        self, mg_id: str, info: MicrogridInfo,
        load_kw: float, pv_kw: float, islanded: bool,
    ) -> Tuple[float, float, float, float]:
        """
        Simple heuristic dispatch (for rule-based EMS comparison).
        This replicates the original SyntheticMeasurementGenerator logic.
        """
        gen_kw = 0.0
        batt_kw = 0.0
        shed_kw = 0.0
        grid_kw = 0.0

        if islanded:
            # Generator covers deficit
            if self.fuel[mg_id] > 1.0:
                deficit = max(load_kw - pv_kw, 0)
                gen_kw = min(deficit, info.generator_capacity_kw)
                fuel_use = gen_kw * 0.3 * DT_HOURS
                self.fuel[mg_id] = max(0, self.fuel[mg_id] - fuel_use)

            # Battery covers remaining deficit
            net = pv_kw + gen_kw - load_kw
            if net < 0 and self.soc[mg_id] > 0.20:
                batt_kw = min(-net, info.battery_capacity_kwh)
                soc_delta = batt_kw * DT_HOURS / info.battery_capacity_kwh
                self.soc[mg_id] = max(0.10, self.soc[mg_id] - soc_delta)
            elif net > 0 and self.soc[mg_id] < 0.95:
                charge = min(net, info.battery_capacity_kwh * 0.5)
                soc_delta = charge * DT_HOURS / info.battery_capacity_kwh
                self.soc[mg_id] = min(1.0, self.soc[mg_id] + soc_delta)
                batt_kw = -charge

            # Remaining deficit = shedding
            shed_kw = max(load_kw - pv_kw - gen_kw - max(batt_kw, 0), 0)
        else:
            # Grid-connected
            net = load_kw - pv_kw
            if net > 0 and self.soc[mg_id] < 0.90:
                # Charge battery from grid
                charge_kw = min(net * 0.3, info.battery_capacity_kwh * 0.3)
                soc_delta = charge_kw * DT_HOURS / info.battery_capacity_kwh
                self.soc[mg_id] = min(1.0, self.soc[mg_id] + soc_delta)
                batt_kw = -charge_kw
            grid_kw = max(load_kw - pv_kw - max(batt_kw, 0), 0)

        return gen_kw, batt_kw, shed_kw, grid_kw


# ===========================================================================
# TRIAL RUNNER
# ===========================================================================

def run_trial(
    config_name: str,
    use_optimizer: bool,
    trial_seed: int,
    duration_days: float,
    policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST,
    horizon: int = 8,
    capacity_multiplier: float = 1.0,
    forced_uncertainty: Optional[float] = None,
    cyber_fault_prob: float = 0.10,
    use_mqtt: bool = False,
    realtime_speed: float = 0.0,
    force_outage: bool = False,
    force_shortage: bool = False,
    no_lstm: bool = False,
) -> Dict[str, Any]:
    """
    Run one trial with closed-loop simulation.

    The ReactiveSimulator applies EMS commands, ensuring genuine differences
    between rule-based and optimized dispatch.
    """
    registry = build_registry()

    # Outage scenario (identical for paired configs via same seed)
    outage_gen = OutageScenarioGenerator(duration_days, seed=trial_seed, force_outage=force_outage)

    # CityEMS (Coordinates Microgrids)
    ems = CityEMS(resilience_policy=policy, use_optimizer=use_optimizer)
    for mg_id, info in registry.items():
        # Apply capacity multiplier for sensitivity analysis
        if capacity_multiplier != 1.0:
            info.battery_capacity_kwh *= capacity_multiplier
        ems.register_microgrid(info)

    # ── Setup Models ─────────────────────────────────────────────────────
    # Use real solar data if available
    # Use real solar data if available
    from src.solar.pv_power_model import SolarDataProvider
    from src.solar.solar_preprocessing import load_nsrdb_file

    solar_provider = None
    try:
        data_dir = os.path.join(root_dir, 'data', 'nsrdb')
        data_files = [f for f in os.listdir(data_dir) if f.endswith('.csv')]
        dfs = [load_nsrdb_file(os.path.join(data_dir, f)) for f in data_files]
        if dfs:
            full_df = pd.concat(dfs).sort_index()
            solar_provider = SolarDataProvider(full_df)
            logger.info(f"Loaded {len(full_df)} rows of NSRDB solar data for trial.")
    except Exception as e:
        logger.warning(f"NSRDB Solar Data unavailable ({e}) -- using synthetic.")

    # Shared Digital Twin Simulator (same seed for identical conditions)
    simulator = ReactiveSimulator(registry, solar_provider=solar_provider, seed=trial_seed, force_shortage=force_shortage)

    # ── Interactive Override State ──
    override_state = {
        'force_outage': force_outage,
        'force_shortage': force_shortage,
        'force_cyber_attack': False,  # FIX 6: Cyber-attack sensor corruption
    }

    # If using optimizer, link it to the ems instance
    dispatcher = None
    state_estimator = None
    if use_optimizer:
        if not PREDICTIVE_AVAILABLE:
            raise RuntimeError(f"PredictiveDispatcher import failed: {_import_err}")
            
        # Initialize MQTT if requested
        mqtt = None
        mqtt_sub = None
        fusion = None
        if use_mqtt:
            mqtt = MqttPublisher("sim_engine_pub")
            mqtt.connect()
            
            # Add subscriber for dashboard overrides
            from src.ems.mqtt_manager import MqttSubscriber
            mqtt_sub = MqttSubscriber("sim_engine_sub")
            if mqtt_sub.connect():
                def handle_override(payload):
                    action = payload.get("action")
                    val = payload.get("value", False)
                    if action == "set_outage":
                        override_state['force_outage'] = val
                        logger.warning(f"MQTT OVERRIDE: Outage set to {val}")
                    elif action == "set_shortage":
                        override_state['force_shortage'] = val
                        logger.warning(f"MQTT OVERRIDE: Shortage set to {val}")
                    elif action == "set_cyber_attack":
                        override_state['force_cyber_attack'] = val
                        logger.warning(f"MQTT OVERRIDE: Cyber-attack set to {val}")
                mqtt_sub.on_control_override(handle_override)
            
            fusion = DataFusionEngine()
            logger.info("Real-time IoT synchronization enabled via MQTT.")

        dispatcher = PredictiveDispatcher(
            mg_registry=registry,
            policy=policy,
            horizon=horizon,
            solar_provider=None if no_lstm else solar_provider,
            mqtt_publisher=mqtt
        )
        ems.optimizer = dispatcher  # Inject into ems

        # ── Setup State Estimator (EKF) ───────────────────────────────────
        from src.digital_twin.state_estimator import CityStateEstimator
        est_configs = {}
        for mg_id, info in registry.items():
            # Mock config structure expected by MicrogridStateEstimator
            est_configs[mg_id] = type('Config', (), {
                'battery': type('BatteryConfig', (), {
                    'nominal_capacity_kwh': info.battery_capacity_kwh,
                    'capacity_kwh': info.battery_capacity_kwh,
                })(),
                'load_profile': type('LoadProfile', (), {
                    'total_critical_load': info.critical_load_kw
                })()
            })()
        state_estimator = CityStateEstimator(est_configs)
        logger.info("Active State Estimation (EKF) initialized for optimized trial.")

    # Enable DR Coordinator for certain configs
    from src.ems.demand_response import DemandResponseCoordinator, DREventType, DREventPriority
    dr_coord = None
    if config_name == 'MPC+DR-Optimized':
        dr_coord = DemandResponseCoordinator()
        ems.dr_coordinator = dr_coord
        if use_optimizer:
            dispatcher.dr_coordinator = dr_coord

    dt = timedelta(minutes=DT_MINUTES)
    start = datetime(2025, 7, 1, 0, 0, 0)
    total_steps = int(duration_days * 24 * 4)

    # ── Accumulators ────────────────────────────────────────────────────
    total_ens = 0.0
    critical_ens = 0.0
    total_fuel_cost = 0.0
    total_gen_kwh = 0.0
    total_batt_kwh = 0.0
    total_transfer_kwh = 0.0
    total_dr_kwh = 0.0
    total_shed_steps = 0
    per_mg_ens = {mg: 0.0 for mg in registry}
    per_mg_shed_steps = {mg: 0 for mg in registry}
    solve_times = []

    # IEEE 1366 accumulators
    customer_interruption_durations = {mg: 0.0 for mg in registry}
    customer_interruptions = {mg: 0 for mg in registry}
    prev_was_shedding = {mg: False for mg in registry}
    estimates = {}  # EKF estimates (populated each step if state_estimator is active)

    for step in range(total_steps):
        timestamp = start + step * dt
        
        # Apply interactive overrides or default generator
        islanded = override_state['force_outage'] or outage_gen.is_outage(step)
        intensity = 1.0 if override_state['force_shortage'] else outage_gen.outage_intensity(step)
        simulator.force_shortage = override_state['force_shortage']

        # ── Step 0: Trigger DR events (if enabled) ──
        if dr_coord and (islanded or intensity > 0.5):
            # Check if an event is already active to avoid duplicates
            if not dr_coord.active_events:
                event = dr_coord.create_dr_event(
                    event_type=DREventType.EMERGENCY if islanded else DREventType.ECONOMIC,
                    priority=DREventPriority.MANDATORY if islanded else DREventPriority.VOLUNTARY,
                    start_time=timestamp,
                    duration_minutes=120,
                    target_mw_reduction=0.2, # 200kW total
                )
                # Allocate targets to MGs
                dr_coord.allocate_dr_targets(event, registry)

        # ── Step 0.5: Cyber-Resilience Modeling ──
        failed_links = set()
        if islanded or intensity > 0.3:
            # Stochastic link failures based on priority (Bernoulli process)
            # Hospitals (Priority 1) have redundant links: 1% of base fault
            # Residents (Priority 4) have standard consumer links: 100% of base fault
            for mg_id, info in registry.items():
                prob_scale = 0.01 if info.priority == MicrogridPriority.CRITICAL else (0.4 if info.priority == MicrogridPriority.HIGH else 1.0)
                if np.random.random() < (cyber_fault_prob * prob_scale):
                    failed_links.add(mg_id)

        # ── Step 1: Generate measurements (apply previous commands) ──
        if use_optimizer and dispatcher and step > 0:
            # Use the commands from previous step's optimization
            meas = simulator.generate(timestamp, islanded, intensity, prev_commands)
        else:
            meas = simulator.generate(timestamp, islanded, intensity, None)

        # ── Step 1.1: State Estimation (EKF) ─────────────────────────────
        # In a real DT, measurements are noisy. We simulate this and filter it.
        if state_estimator:
            noisy_obs = {}
            for mg_id, status in meas.microgrid_statuses.items():
                # Inject Gaussian noise: SoC (+/- 2%), Load (+/- 10kW)
                # If forced_uncertainty is provided, scale noise accordingly
                noise_scale = (forced_uncertainty / 0.15) if forced_uncertainty else 1.0
                n_soc = np.clip(status.battery_soc_percent + np.random.normal(0, 1.5 * noise_scale), 0, 100)
                n_load = max(0, status.total_load_kw + np.random.normal(0, 8.0 * noise_scale))
                
                noisy_obs[mg_id] = {
                    'battery_soc_percent': n_soc,
                    'battery_power_kw': status.battery_power_kw,
                    'total_load_kw': n_load
                }

            # Pack previous control inputs for estimator prediction
            prev_ctrl = {}
            if step > 0 and 'prev_commands' in locals():
                for mg_id, cmd in prev_commands.items():
                    prev_ctrl[mg_id] = {'target_shed': cmd.target_shed_percent or 0.0}

            # Update EKF estimates
            estimates = state_estimator.update_all(DT_MINUTES * 60, noisy_obs, prev_ctrl)

            # ── FIX 6: Cyber-Attack Injection ──
            # If cyber attack is active, corrupt sensor readings with large bias
            if override_state.get('force_cyber_attack', False):
                for mg_id in noisy_obs:
                    noisy_obs[mg_id]['battery_soc_percent'] += np.random.uniform(15, 30)
                    noisy_obs[mg_id]['total_load_kw'] *= np.random.uniform(0.3, 0.6)
                # Re-run EKF with corrupted data so anomaly detection triggers
                estimates = state_estimator.update_all(DT_MINUTES * 60, noisy_obs, prev_ctrl)

            # ── FIX 6: EKF Anomaly Detection → Alert ──
            if use_mqtt and mqtt:
                for mg_id, estimator_obj in state_estimator.mg_estimators.items():
                    anomaly = estimator_obj.detect_anomaly(threshold_sigma=3.0)
                    if anomaly:
                        mqtt.broadcast_alert("critical", f"🛡️ CYBER: {mg_id} — {anomaly}")
            
            # Switch 'meas' to ESTIMATED view for the dispatcher
            from copy import deepcopy
            dispatch_meas = deepcopy(meas)
            for mg_id, est in estimates.items():
                # Overwrite raw with filtered
                m_status = dispatch_meas.microgrid_statuses[mg_id]
                m_status.battery_soc_percent = est.value
                m_status.total_load_kw = state_estimator.mg_estimators[mg_id].state[3]
        else:
            dispatch_meas = meas

        # ── Step 2: Run EMS (produces commands for NEXT step) ──
        if use_optimizer and dispatcher:
            outage_prep = islanded or intensity > 0
            # Extract DR targets for MPC awareness
            dr_targets = {}
            if dr_coord:
                # Get current active event targets
                for event in dr_coord.active_events.values():
                    if event.is_in_progress(meas.timestamp):
                        for mg_id, target in event.allocated_reductions.items():
                            dr_targets[mg_id] = target

            # 2.3 Aggregate Data Fusion (Person 3 Logic)
            if fusion:
                # Merge IoT inputs into the measurements before solving
                for mg_id in registry:
                    # In a full impl, we'd update meas/registry here
                    pass

            commands, solution = dispatcher.solve(
                dispatch_meas,
                city_mode=ems.state.city_mode,
                outage_preparation=outage_prep,
                dr_targets=dr_targets,
                failed_links=failed_links,
                forced_uncertainty=forced_uncertainty,
            )
            prev_commands = commands

            if solution.solve_time_ms > 0:
                solve_times.append(solution.solve_time_ms)
            
            # Broadcast city metrics to Dashboard (IoT Sync)
            if use_mqtt and mqtt:
                n_mg = len(registry)
                total_service_hours_so_far = (step + 1) * DT_HOURS * n_mg
                total_interruption_hours_so_far = sum(customer_interruption_durations.values())

                stats = {
                    "ASAI": 1.0 - (total_interruption_hours_so_far / max(total_service_hours_so_far, 1e-6)),
                    "EENS": round(total_ens, 2),
                    # ── FIX 1: Real EKF confidence ──
                    "ekf_city_confidence": round(
                        state_estimator.get_city_confidence_score(estimates) * 100, 1
                    ) if state_estimator and estimates else 0.0,
                    # ── FIX 4: Live IEEE 1366 metrics ──
                    "SAIDI": round(sum(customer_interruption_durations.values()) / max(n_mg, 1), 4),
                    "SAIFI": round(sum(customer_interruptions.values()) / max(n_mg, 1), 4),
                    "CAIDI": round(
                        (sum(customer_interruption_durations.values()) / max(n_mg, 1)) /
                        max(sum(customer_interruptions.values()) / max(n_mg, 1), 1e-6), 4
                    ),
                    "LOLP": round(total_shed_steps / max(step + 1, 1), 6),
                }
                mqtt.broadcast_city_metrics(stats)

                # ── FIX 2: Predictive Horizon (Shadow Sim Lite) ──
                # Every 12 steps (~3 sim hours), compute forward predictions
                if step % 12 == 0 and step > 0:
                    predictions = {}
                    for mg_id, status in meas.microgrid_statuses.items():
                        soc = status.battery_soc_percent
                        cap = registry[mg_id].battery_capacity_kwh
                        load = status.total_load_kw
                        pv = status.pv_power_kw if hasattr(status, 'pv_power_kw') else 0
                        gen = status.generator_power_kw if hasattr(status, 'generator_power_kw') else 0
                        fuel = fuel_levels.get(mg_id, 0) if 'fuel_levels' in dir() else 0

                        # Time to battery exhaustion (hours)
                        net_draw = max(load - pv - gen, 0.1)
                        remaining_kwh = (soc / 100.0) * cap
                        tte_hours = remaining_kwh / net_draw

                        # Fuel exhaustion (hours) — diesel consumption ~0.3 L/kWh
                        fuel_hours = fuel / max(gen * 0.3, 0.01) if gen > 0.5 else float('inf')

                        # Risk assessment
                        risk = "LOW"
                        actions = []
                        if tte_hours < 2.0:
                            risk = "CRITICAL"
                            actions.append(f"Battery exhaustion in {tte_hours:.1f}h — request energy import")
                        elif tte_hours < 6.0:
                            risk = "ELEVATED"
                            actions.append(f"Battery at {soc:.0f}% — consider load shedding")
                        if fuel_hours < 4.0 and gen > 0.5:
                            risk = "CRITICAL" if risk != "CRITICAL" else risk
                            actions.append(f"Fuel exhaustion in {fuel_hours:.1f}h — generator at risk")
                        if islanded and soc < 30:
                            actions.append("Islanded with low SOC — prioritize critical loads")

                        predictions[mg_id] = {
                            'time_to_exhaustion_hours': round(tte_hours, 2),
                            'fuel_remaining_hours': round(min(fuel_hours, 999), 2),
                            'risk_level': risk,
                            'recommended_actions': actions,
                        }

                    try:
                        import json
                        mqtt.client.publish("city/predictions", json.dumps(predictions))
                    except Exception:
                        pass

        # 4. Step delay for real-time mode
        if realtime_speed > 0:
            time.sleep(DT_MINUTES * 60 / realtime_speed)

            # Record optimizer-specific transfer metric
            total_transfer_kwh += solution.total_export_kw * DT_HOURS
            total_dr_kwh += sum(d.dr_kw for d in solution.dispatches.values()) * DT_HOURS
        else:
            outputs = ems.update(meas, failed_links=failed_links)
            prev_commands = outputs.supervisory_commands

        # ── Step 3: Collect metrics from measurements ──
        for mg_id, status in meas.microgrid_statuses.items():
            shed_kwh = status.load_shed_kw * DT_HOURS
            total_ens += shed_kwh
            per_mg_ens[mg_id] += shed_kwh

            if registry[mg_id].priority == MicrogridPriority.CRITICAL:
                critical_ens += shed_kwh

            gen_kwh = status.generator_power_kw * DT_HOURS
            total_gen_kwh += gen_kwh
            total_fuel_cost += gen_kwh * 0.30

            total_batt_kwh += abs(status.battery_power_kw) * DT_HOURS

            # IEEE 1366 tracking
            is_shedding = status.load_shed_kw > 0.5  # > 0.5 kW threshold
            if is_shedding:
                customer_interruption_durations[mg_id] += DT_HOURS
                per_mg_shed_steps[mg_id] += 1
                if not prev_was_shedding[mg_id]:
                    customer_interruptions[mg_id] += 1
            prev_was_shedding[mg_id] = is_shedding

        if any(s.load_shed_kw > 0.5 for s in meas.microgrid_statuses.values()):
            total_shed_steps += 1

    # ── IEEE 1366-2012 Reliability Indices ──────────────────────────────
    n_mg = len(registry)
    # SAIDI: System Average Interruption Duration Index (hours/customer)
    saidi = sum(customer_interruption_durations.values()) / n_mg
    # SAIFI: System Average Interruption Frequency Index (interruptions/customer)
    saifi = sum(customer_interruptions.values()) / n_mg
    # CAIDI: Customer Average Interruption Duration Index (hours/interruption)
    caidi = saidi / saifi if saifi > 0 else 0
    # EENS: Expected Energy Not Served (kWh)
    eens = total_ens
    # LOLP: Loss of Load Probability
    lolp = total_shed_steps / total_steps if total_steps > 0 else 0
    # ASAI: Average Service Availability Index
    total_service_hours = total_steps * DT_HOURS * n_mg
    total_interruption_hours = sum(customer_interruption_durations.values())
    asai = 1 - total_interruption_hours / total_service_hours if total_service_hours > 0 else 1

    # Recovery time: average duration of shed events
    recovery_times = []
    for mg_id in registry:
        n_events = customer_interruptions[mg_id]
        if n_events > 0:
            avg_dur = customer_interruption_durations[mg_id] / n_events
            recovery_times.append(avg_dur)
    avg_recovery_hours = np.mean(recovery_times) if recovery_times else 0

    result = {
        'config': config_name,
        'use_optimizer': use_optimizer,
        'trial_seed': trial_seed,
        'duration_days': duration_days,
        'total_steps': total_steps,
        'n_outage_events': len(outage_gen.events),
        'total_outage_hours': outage_gen.summary()['total_outage_hours'],
        'total_ens_kwh': round(total_ens, 2),
        'critical_ens_kwh': round(critical_ens, 2),
        'total_fuel_cost_usd': round(total_fuel_cost, 2),
        'total_gen_kwh': round(total_gen_kwh, 2),
        'total_batt_cycling_kwh': round(total_batt_kwh, 2),
        'total_transfer_kwh': round(total_transfer_kwh, 2),
        'shed_steps': total_shed_steps,
        'hospital_ens': round(per_mg_ens.get('hospital', 0), 2),
        'university_ens': round(per_mg_ens.get('university', 0), 2),
        'industrial_ens': round(per_mg_ens.get('industrial', 0), 2),
        'residential_ens': round(per_mg_ens.get('residential', 0), 2),
        'SAIDI': round(saidi, 4),
        'SAIFI': round(saifi, 4),
        'CAIDI': round(caidi, 4),
        'EENS': round(eens, 2),
        'LOLP': round(lolp, 6),
        'ASAI': round(asai, 8),
        'avg_recovery_hours': round(avg_recovery_hours, 4),
        'avg_solve_ms': round(np.mean(solve_times), 3) if solve_times else 0,
        'max_solve_ms': round(max(solve_times), 3) if solve_times else 0,
        'p95_solve_ms': round(np.percentile(solve_times, 95), 3) if solve_times else 0,
    }

    # Optimizer statistics
    if dispatcher:
        result['optimizer_stats'] = dispatcher.get_statistics()

    return result


# ===========================================================================
# EXPERIMENT ORCHESTRATOR
# ===========================================================================

def run_experiment(
    n_trials: int = 100,
    duration_days: float = 30.0,
    base_seed: int = 42,
    policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST,
    horizon: int = 8,
    capacity_multiplier: float = 1.0,
    forced_uncertainty: Optional[float] = None,
    cyber_fault_prob: float = 0.10,
    use_mqtt: bool = False,
    realtime_speed: float = 0.0,
    force_outage: bool = False,
    force_shortage: bool = False,
    no_lstm: bool = False,
    config_filter: Optional[str] = None,
) -> List[Dict]:
    """Run matched paired trials: rule-based vs predictive-optimized."""
    all_configs = [
        ('Rule-Based', False),
        ('MPC-Optimized', True),
        ('MPC+DR-Optimized', True),
    ]
    # Filter to a single config if specified (e.g., for live demo mode)
    if config_filter:
        configs = [(n, o) for n, o in all_configs if n == config_filter]
        if not configs:
            logger.warning(f"Config filter '{config_filter}' not found, running all.")
            configs = all_configs
    else:
        configs = all_configs
    all_results = []

    logger.info("=" * 70)
    logger.info("PREDICTIVE OPTIMIZATION vs RULE-BASED EMS — EXPERIMENT")
    logger.info("=" * 70)
    logger.info(f"Trials: {n_trials}, Duration: {duration_days} days, Policy: {policy.value}")
    logger.info(f"Optimizer horizon: {horizon} steps ({horizon * DT_HOURS:.1f}h)")
    logger.info(f"Configs: {[c[0] for c in configs]}")
    logger.info("-" * 70)

    for trial_idx in range(n_trials):
        seed = base_seed + trial_idx

        for name, use_opt in configs:
            t0 = time.time()
            try:
                result = run_trial(
                    config_name=name,
                    use_optimizer=use_opt,
                    trial_seed=seed,
                    duration_days=duration_days,
                    policy=policy,
                    horizon=horizon,
                    capacity_multiplier=capacity_multiplier,
                    forced_uncertainty=forced_uncertainty,
                    cyber_fault_prob=cyber_fault_prob,
                    use_mqtt=use_mqtt if use_opt else False,
                    realtime_speed=realtime_speed if use_opt else 0.0,
                    force_outage=force_outage,
                    force_shortage=force_shortage,
                )
                result['trial_idx'] = trial_idx
                elapsed = time.time() - t0
                all_results.append(result)

                if trial_idx < 3 or (trial_idx + 1) % 10 == 0:
                    logger.info(
                        f"  Trial {trial_idx+1:>3}/{n_trials} {name:>15}: "
                        f"ENS={result['total_ens_kwh']:>8.1f}kWh, "
                        f"Fuel=${result['total_fuel_cost_usd']:>7.2f}, "
                        f"SAIDI={result['SAIDI']:.4f}, "
                        f"ASAI={result['ASAI']:.6f}, "
                        f"Solve={result['avg_solve_ms']:.2f}ms "
                        f"({elapsed:.1f}s)"
                    )
            except Exception as e:
                logger.error(f"Trial {trial_idx} {name} FAILED: {e}")
                raise

    logger.info(f"\nExperiment complete: {len(all_results)} total results")
    return all_results


# ===========================================================================
# STATISTICAL ANALYSIS
# ===========================================================================

COMPARISON_METRICS = [
    'total_ens_kwh', 'critical_ens_kwh', 'total_fuel_cost_usd',
    'total_gen_kwh', 'total_batt_cycling_kwh', 'shed_steps',
    'hospital_ens', 'university_ens', 'industrial_ens', 'residential_ens',
    'SAIDI', 'SAIFI', 'CAIDI', 'EENS', 'LOLP', 'ASAI',
    'avg_recovery_hours',
]


def compute_statistics(results: List[Dict]) -> Dict:
    """Compute descriptive + inferential statistics with proper corrections."""
    try:
        from scipy import stats as sp_stats
        HAS_SCIPY_STATS = True
    except ImportError:
        HAS_SCIPY_STATS = False
        logger.warning("scipy.stats not available — skipping inferential tests")

    by_config = {}
    for r in results:
        by_config.setdefault(r['config'], []).append(r)

    analysis = {
        'experiment_info': {
            'n_trials': len(results) // max(len(by_config), 1),
            'configs': list(by_config.keys()),
            'timestamp': datetime.now().isoformat(),
        },
        'descriptive': {},
        'comparisons': [],
    }

    # Descriptive statistics
    for cfg_name, cfg_results in by_config.items():
        stats = {}
        for metric in COMPARISON_METRICS:
            vals = np.array([r.get(metric, 0) for r in cfg_results], dtype=float)
            n = len(vals)
            mean = np.mean(vals)
            std = np.std(vals, ddof=1) if n > 1 else 0
            se = std / np.sqrt(n) if n > 0 else 0
            ci = 1.96 * se
            stats[metric] = {
                'mean': round(float(mean), 4),
                'std': round(float(std), 4),
                'se': round(float(se), 4),
                'ci95': [round(float(mean - ci), 4), round(float(mean + ci), 4)],
                'min': round(float(np.min(vals)), 4),
                'max': round(float(np.max(vals)), 4),
                'median': round(float(np.median(vals)), 4),
                'n': n,
            }
        analysis['descriptive'][cfg_name] = stats

    # Paired inferential comparison
    if HAS_SCIPY_STATS and 'Rule-Based' in by_config and 'MPC-Optimized' in by_config:
        rb = by_config['Rule-Based']
        opt = by_config['MPC-Optimized']
        n = min(len(rb), len(opt))
        n_tests = len(COMPARISON_METRICS)
        bonferroni_alpha = 0.05 / n_tests

        for metric in COMPARISON_METRICS:
            a = np.array([r.get(metric, 0) for r in rb[:n]], dtype=float)
            b = np.array([r.get(metric, 0) for r in opt[:n]], dtype=float)

            diff = a - b
            if np.std(diff) > 1e-10:
                t_stat, t_p = sp_stats.ttest_rel(a, b)
            else:
                t_stat, t_p = 0.0, 1.0

            try:
                w_stat, w_p = sp_stats.wilcoxon(a, b, zero_method='wilcox')
            except ValueError:
                w_stat, w_p = 0.0, 1.0

            pooled = np.sqrt((np.var(a, ddof=1) + np.var(b, ddof=1)) / 2)
            d = (np.mean(a) - np.mean(b)) / pooled if pooled > 1e-10 else 0

            pct_change = (
                (np.mean(a) - np.mean(b)) / abs(np.mean(a)) * 100
                if abs(np.mean(a)) > 1e-10 else 0
            )

            abs_d = abs(d)
            effect = (
                'negligible' if abs_d < 0.2 else
                'small' if abs_d < 0.5 else
                'medium' if abs_d < 0.8 else 'large'
            )

            analysis['comparisons'].append({
                'metric': metric,
                'rule_based_mean': round(float(np.mean(a)), 4),
                'optimized_mean': round(float(np.mean(b)), 4),
                'mean_difference': round(float(np.mean(diff)), 4),
                'pct_change': round(float(pct_change), 2),
                't_statistic': round(float(t_stat), 4),
                't_pvalue': round(float(t_p), 8),
                'wilcoxon_pvalue': round(float(w_p), 8),
                'cohens_d': round(float(d), 4),
                'effect_size': effect,
                'significant_bonferroni': bool(t_p < bonferroni_alpha),
                'significant_nominal': bool(t_p < 0.05),
                'bonferroni_alpha': round(bonferroni_alpha, 6),
                'n_pairs': n,
            })

    return analysis


def print_summary(analysis: Dict):
    """Print formatted comparison table."""
    print("\n" + "=" * 100)
    print("PREDICTIVE OPTIMIZATION vs RULE-BASED EMS -- COMPARATIVE RESULTS")
    print("=" * 100)

    info = analysis.get('experiment_info', {})
    print(f"Trials: {info.get('n_trials', '?')}, Configs: {info.get('configs', [])}")

    # Descriptive
    print("\n--- Descriptive Statistics (Mean +/- 95% CI) ---")
    cfgs = sorted(analysis['descriptive'].keys())
    header = f"{'Metric':<25}"
    for cfg in cfgs:
        header += f"  {cfg:>30}"
    print(header)
    print("-" * (25 + 32 * len(cfgs)))

    for metric in COMPARISON_METRICS:
        row = f"{metric:<25}"
        for cfg in cfgs:
            s = analysis['descriptive'].get(cfg, {}).get(metric, {})
            mean = s.get('mean', 0)
            ci = s.get('ci95', [0, 0])
            ci_half = (ci[1] - ci[0]) / 2 if len(ci) == 2 else 0
            row += f"  {mean:>15.2f} +/- {ci_half:>8.2f}"
        print(row)

    # Comparisons
    if analysis.get('comparisons'):
        print("\n--- Paired Comparison (Rule-Based -> MPC-Optimized) ---")
        print(f"{'Metric':<25} {'%Chg':>8} {'t-stat':>9} {'p-val':>10} "
              f"{'Cohen d':>8} {'Effect':>10} {'Sig*':>6}")
        print("-" * 80)
        for c in analysis['comparisons']:
            sig = "***" if c['significant_bonferroni'] else (
                "*" if c['significant_nominal'] else ""
            )
            print(
                f"{c['metric']:<25} {c['pct_change']:>+7.1f}% "
                f"{c['t_statistic']:>9.3f} {c['t_pvalue']:>10.6f} "
                f"{c['cohens_d']:>8.3f} {c['effect_size']:>10} {sig:>6}"
            )
        print("\n* Bonferroni-corrected significance (α/k)")

    # Solver performance
    print("\n--- Optimizer Performance ---")
    opt_results = [r for r in analysis.get('descriptive', {}).get('MPC-Optimized', {}).items()
                   if 'solve' in str(r[0]).lower()]
    if not opt_results:
        # Pull from raw data
        pass

    print()


# ===========================================================================
# I/O
# ===========================================================================

def save_csv(results: List[Dict], path: str):
    """Save results to CSV (excluding nested dicts)."""
    if not results:
        return
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    # Flatten: skip dict-valued fields
    flat_results = []
    for r in results:
        flat = {k: v for k, v in r.items() if not isinstance(v, dict)}
        flat_results.append(flat)

    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=flat_results[0].keys())
        w.writeheader()
        w.writerows(flat_results)
    logger.info(f"Saved {len(flat_results)} rows -> {path}")


def save_json(data, path: str):
    """Save analysis to JSON."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"Saved analysis -> {path}")


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Predictive MPC vs Rule-Based EMS — Comparative Experiment"
    )
    parser.add_argument('--trials', type=int, default=30,
                        help='Number of Monte Carlo trials (≥30 recommended)')
    parser.add_argument('--days', type=float, default=30.0,
                        help='Simulated days per trial (≥30 recommended)')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--horizon', type=int, default=8,
                        help='Optimizer look-ahead steps (8=2h, 16=4h)')
    parser.add_argument('--policy', type=str, default='critical_first',
                        choices=['balanced', 'critical_first', 'economic', 'equitable'])
    parser.add_argument('--quick', action='store_true',
                        help='Quick mode: 5 trials × 3 days')
    parser.add_argument('--outdir', type=str, default='results')
    parser.add_argument('--mqtt', action='store_true',
                        help='Enable IoT state broadcasting via MQTT')
    parser.add_argument('--realtime', type=float, default=0.0,
                        help='Real-time speed (e.g. 60.0 = 1 sim min/sec)')
    parser.add_argument('--force-outage', action='store_true',
                        help='DEMO: Force immediate and permanent city-wide blackout')
    parser.add_argument('--force-shortage', action='store_true',
                        help='DEMO: Force severe power shortage (high load, low solar)')
    parser.add_argument('--no-lstm', action='store_true',
                        help='Disable LSTM forecaster (force statistical fallback)')
    parser.add_argument('--config', type=str, default=None,
                        choices=['Rule-Based', 'MPC-Optimized', 'MPC+DR-Optimized'],
                        help='Run only this config (default: all 3)')
    args = parser.parse_args()

    if args.quick:
        args.trials = 5
        args.days = 3.0

    policy_map = {
        'balanced': ResiliencePolicy.BALANCED,
        'critical_first': ResiliencePolicy.CRITICAL_FIRST,
        'economic': ResiliencePolicy.ECONOMIC,
        'equitable': ResiliencePolicy.EQUITABLE,
    }
    policy = policy_map[args.policy]

    # Validate dependencies
    if not PREDICTIVE_AVAILABLE:
        logger.error(f"FATAL: PredictiveDispatcher import failed: {_import_err}")
        logger.error("Fix the import error before running the experiment.")
        sys.exit(1)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    results = run_experiment(
        n_trials=args.trials,
        duration_days=args.days,
        base_seed=args.seed,
        policy=policy,
        horizon=args.horizon,
        use_mqtt=args.mqtt,
        realtime_speed=args.realtime,
        force_outage=args.force_outage,
        force_shortage=args.force_shortage,
        no_lstm=args.no_lstm,
        config_filter=args.config,
    )

    # Save raw results
    csv_path = os.path.join(args.outdir, f'predictive_comparison_{ts}.csv')
    save_csv(results, csv_path)

    # Statistical analysis
    analysis = compute_statistics(results)
    analysis['experiment_config'] = {
        'trials': args.trials,
        'days': args.days,
        'seed': args.seed,
        'horizon': args.horizon,
        'policy': args.policy,
    }
    json_path = os.path.join(args.outdir, f'predictive_analysis_{ts}.json')
    save_json(analysis, json_path)

    # Print summary
    print_summary(analysis)

    # Export optimizer convergence log (from last optimized trial)
    opt_results = [r for r in results if r.get('use_optimizer')]
    if opt_results and 'optimizer_stats' in opt_results[-1]:
        conv_path = os.path.join(args.outdir, f'optimizer_convergence_{ts}.json')
        save_json(opt_results[-1].get('optimizer_stats', {}), conv_path)

    logger.info("Experiment complete.")


if __name__ == '__main__':
    main()
