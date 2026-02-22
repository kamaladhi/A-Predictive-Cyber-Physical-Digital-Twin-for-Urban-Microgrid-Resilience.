from __future__ import annotations
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
import numpy as np

logger = logging.getLogger(__name__)

# ─── Solver backend ─────────────────────────────────────────────────────────
try:
    from scipy.optimize import linprog
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

from src.ems.common import (
    MicrogridPriority, CityOperationMode, ResiliencePolicy,
    MicrogridInfo, MicrogridStatus, CityWideMeasurements,
    SupervisoryCommand, CityControlOutputs
)

# ─── Constants ──────────────────────────────────────────────────────────────
DT_HOURS = 0.25              # 15-min timestep
DEFAULT_HORIZON = 8          # 2-hour lookahead  (8 x 15 min)
MAX_HORIZON = 96             # 24 hours max
BUS_CAPACITY_KW = 200.0
BUS_EFFICIENCY = 0.95
BATT_CHARGE_EFF = 0.95       # Round-trip split
BATT_DISCHARGE_EFF = 0.95
FUEL_RATE_L_PER_KWH = 0.30   # Diesel consumption rate

# Value-of-Lost-Load multiplier for slack penalty
# Must be >> shedding penalty so slack is absolute last resort
VOLL_MULTIPLIER = 100.0


# =============================================================================
# COST CONFIGURATION
# =============================================================================

@dataclass
class PredictiveCostConfig:
    """
    Multi-objective cost coefficients for the rolling-horizon dispatch.

    alpha-epsilon are scalarisation weights. Per-unit costs are in $/kWh.
    Priority weights w_m penalise shedding of higher-priority MGs more.
    """
    fuel_cost_per_kwh: float = 0.30
    shedding_penalty_per_kwh: float = 10.0
    battery_degradation_per_kwh: float = 0.05
    transfer_loss_cost_per_kwh: float = (1 - BUS_EFFICIENCY) * 0.12
    critical_penalty_per_kwh: float = 500.0   # critical load shed penalty
    unmet_demand_penalty_per_kwh: float = 10000.0  # VOLL for slack
    dr_incentive_per_kwh: float = 0.60          # Voluntary DR incentive

    priority_weights: Dict[int, float] = field(default_factory=lambda: {
        1: 50.0,   # CRITICAL  (hospital)
        2: 10.0,   # HIGH      (university)
        3:  5.0,   # MEDIUM    (industrial)
        4:  1.0,   # LOW       (residential)
    })

    # Scalarisation weights
    alpha: float = 0.25   # fuel
    beta:  float = 0.35   # shedding
    gamma: float = 0.15   # degradation
    delta: float = 0.10   # transfer
    epsilon: float = 0.15 # critical load penalty

    # Discount factor: later steps weighted less (geometric)
    temporal_discount: float = 0.98

    @classmethod
    def for_policy(cls, policy: ResiliencePolicy) -> "PredictiveCostConfig":
        if policy == ResiliencePolicy.CRITICAL_FIRST:
            return cls(alpha=0.15, beta=0.40, gamma=0.10, delta=0.10, epsilon=0.25)
        elif policy == ResiliencePolicy.ECONOMIC:
            return cls(alpha=0.40, beta=0.20, gamma=0.15, delta=0.10, epsilon=0.15)
        elif policy == ResiliencePolicy.EQUITABLE:
            cfg = cls(alpha=0.25, beta=0.25, gamma=0.15, delta=0.15, epsilon=0.20)
            cfg.priority_weights = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}
            return cfg
        else:  # BALANCED
            return cls()


# =============================================================================
# FORECAST PROVIDER
# =============================================================================

class ForecastProvider:
    """
    Provides multi-step PV and load forecasts for the rolling horizon.

    Integration hierarchy:
      1. LSTM solar forecaster (if model available)       — best
      2. Statistical persistence + diurnal envelope       — fallback
      3. Flat persistence (current value repeated)        — last resort

    Load forecast uses parametric daily profiles with Gaussian noise.
    """

    # Typical load profile fractions by hour (24-element arrays)
    # Normalised to ~1.0 mean
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

    def __init__(self, mg_registry: Dict[str, MicrogridInfo], solar_provider: Any = None):
        self.registry = mg_registry
        self.solar_provider = solar_provider
        self._lstm_forecaster = None
        self._try_load_lstm()

    def _try_load_lstm(self):
        """Attempt to load trained LSTM solar forecaster."""
        if not self.solar_provider:
            return

        try:
            from src.solar.solar_forecasting import SolarForecaster
            # Walk up to find root if in EMS/
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            model_path = os.path.join(base_dir, 'SolarData', 'models', 'solar_lstm.pt')

            if os.path.exists(model_path):
                self._lstm_forecaster = SolarForecaster(model_path, self.solar_provider)
                logger.info("LSTM solar forecaster loaded for predictive dispatch")
            else:
                logger.info(f"LSTM model not found at {model_path} -- using statistical solar forecast")
        except Exception as e:
            # Silent fallback to statistical if model architecture mismatch or missing
            logger.info("LSTM forecaster unavailable (trained model out of sync) -- using statistical fallback")

    def forecast_solar(
        self,
        current_pv_kw: float,
        pv_capacity_kwp: float,
        timestamp: datetime,
        horizon: int,
    ) -> np.ndarray:
        """
        Forecast PV generation for T steps ahead.

        Tries LSTM first, then diurnal envelope fallback.
        """
        # 1. Try high-fidelity LSTM if available
        if self._lstm_forecaster:
            try:
                # SolarForecaster.predict returns Dict[str, float] for 1h, 6h, 24h
                # We interpolate for the horizon steps (15-min each)
                fc_dict = self._lstm_forecaster.predict(timestamp)
                ghi_1h = fc_dict.get('ghi_1h', 0.0)

                # Use 1h forecast to anchor the next 4 steps (15min each)
                # We still use the envelope to provide sub-hourly shape, but
                # scaled to the LSTM's GHI prediction.
                forecasts = np.zeros(horizon)
                for t in range(horizon):
                    future_time = timestamp + timedelta(hours=(t + 1) * DT_HOURS)
                    hour = future_time.hour + future_time.minute / 60.0
                    if 6.0 <= hour <= 18.0:
                        env = max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))
                    else:
                        env = 0.0

                    # Convert GHI 1h back to Power roughly, then scale.
                    # This is simpler than full pvlib but honors the LSTM's trend.
                    # ghi_1h is W/m2. STC is 1000 W/m2.
                    forecasts[t] = pv_capacity_kwp * (ghi_1h / 1000.0) * env
                return forecasts
            except Exception as e:
                logger.debug(f"LSTM prediction failed: {e}")

        # 2. Fallback to statistical diurnal envelope
        forecasts = np.zeros(horizon)
        for t in range(horizon):
            future_time = timestamp + timedelta(hours=(t + 1) * DT_HOURS)
            hour = future_time.hour + future_time.minute / 60.0

            # Solar envelope: sin curve 6am–6pm
            if 6.0 <= hour <= 18.0:
                envelope = max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))
            else:
                envelope = 0.0

            # Scale using current irradiance ratio as cloud proxy
            current_hour = timestamp.hour + timestamp.minute / 60.0
            if 6.0 <= current_hour <= 18.0:
                current_envelope = max(0.01, math.sin(math.pi * (current_hour - 6.0) / 12.0))
                cloud_factor = min(current_pv_kw / (pv_capacity_kwp * current_envelope + 0.01), 1.2)
            else:
                cloud_factor = 0.7  # Default clear-sky assumption for future

            forecasts[t] = max(0.0, pv_capacity_kwp * envelope * cloud_factor)

        return forecasts

    def forecast_load(
        self,
        mg_id: str,
        mg_type: str,
        current_load_kw: float,
        total_capacity_kw: float,
        timestamp: datetime,
        horizon: int,
    ) -> np.ndarray:
        """
        Forecast load demand for T steps ahead using parametric profiles.

        Returns shape (horizon,) in kW.
        """
        profile = self.LOAD_PROFILES.get(mg_type, self.LOAD_PROFILES['residential'])
        current_hour = timestamp.hour + timestamp.minute / 60.0
        current_hour_idx = int(current_hour) % 24
        current_profile_val = profile[current_hour_idx]

        # Scale factor: ratio of actual to profile-predicted
        if current_profile_val > 0.01:
            scale = current_load_kw / (total_capacity_kw * current_profile_val)
        else:
            scale = current_load_kw / (total_capacity_kw * 0.3)

        forecasts = np.zeros(horizon)
        for t in range(horizon):
            future_time = timestamp + timedelta(hours=(t + 1) * DT_HOURS)
            fh = int(future_time.hour + future_time.minute / 60.0) % 24
            forecasts[t] = max(0.1, total_capacity_kw * profile[fh] * scale)

        return forecasts

    def build_forecast_arrays(
        self,
        measurements: CityWideMeasurements,
        mg_registry: Dict[str, MicrogridInfo],
        horizon: int,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
        """
        Build full forecast arrays for all MGs.

        Returns
        -------
        pv_forecast   : shape (n_mg, horizon) kW
        load_forecast : shape (n_mg, horizon) kW
        uncertainty   : shape (n_mg,) normalised std
        mg_ids        : ordered MG IDs
        """
        mg_ids = sorted(mg_registry.keys())
        n = len(mg_ids)
        pv = np.zeros((n, horizon))
        load = np.zeros((n, horizon))
        unc = np.zeros(n)

        for idx, mg_id in enumerate(mg_ids):
            info = mg_registry[mg_id]
            status = measurements.microgrid_statuses.get(mg_id)
            if status is None:
                continue

            # Uncertainty assignment: lower for high-fidelity LSTM, higher for fallback
            unc[idx] = 0.05 if self._lstm_forecaster else 0.15
            
            pv[idx] = self.forecast_solar(
                status.pv_generation_kw, info.pv_capacity_kwp,
                measurements.timestamp, horizon
            )
            load[idx] = self.forecast_load(
                mg_id, info.microgrid_type,
                status.total_load_kw, info.total_capacity_kw,
                measurements.timestamp, horizon
            )

            # Uncertainty estimate (higher at night, with distance)
            base_unc = 0.10
            hour = measurements.timestamp.hour
            if hour < 6 or hour > 18:
                base_unc = 0.20  # Higher uncertainty for solar at night
            unc[idx] = base_unc

        return pv, load, unc, mg_ids


# =============================================================================
# UNCERTAINTY MODELLING
# =============================================================================

class RobustMarginCalculator:
    """
    Compute SOC floor margins and generation reserves from forecast uncertainty.

    Uses a scenario-based approach:
      - σ_forecast is the normalised forecast standard deviation
      - k_safety scales σ into SOC margin: margin = k · σ · E_batt / Δt
      - Priority-dependent base SOC floors (critical gets highest)

    For stochastic robustness, we add a "worst-case reserve" that ensures
    the system can survive a (1-α) quantile of forecast errors.
    """

    SAFETY_K = {"conservative": 2.0, "balanced": 1.5, "aggressive": 1.0}
    SOC_FLOOR = {
        MicrogridPriority.CRITICAL: 0.40,
        MicrogridPriority.HIGH:     0.30,
        MicrogridPriority.MEDIUM:   0.20,
        MicrogridPriority.LOW:      0.15,
    }

    @classmethod
    def compute_soc_bounds(
        cls,
        info: MicrogridInfo,
        uncertainty: float,
        dispatch_mode: str = "balanced",
        outage_active: bool = False,
        horizon_step: int = 0,
        current_soc: float = 0.5,
    ) -> Tuple[float, float]:
        """
        Returns (soc_min, soc_max) in [0,1].

        During grid-connected operation: SOC_min is raised to build reserves.
        During active outage: SOC_min is LOWERED to enable battery discharge
          for load service; the multi-step LP handles conservation itself.
        """
        k = cls.SAFETY_K.get(dispatch_mode, 1.5)

        if outage_active:
            # During outage: allow deeper discharge but keep a σ-dependent floor.
            # base: 5% + 1% per step growth
            base = 0.05 + 0.01 * horizon_step
            # Chance-constrained margin: z-score (k) * sigma * battery_size
            # We use a smaller multiplier since we're already in emergency discharge.
            margin = uncertainty * k * 0.05
            soc_min = min(base + margin, 0.40)
        else:
            # Grid-connected: build reserves proactively (Chance-Constrained)
            # base: priority-dependent floor (Hospital: 40%, Resid: 15%)
            base = cls.SOC_FLOOR.get(info.priority, 0.20)
            # Full probabilistic margin: z * sigma
            # Grows with horizon (+2% per step) as error accumulates
            margin = uncertainty * k * 1.5 * (1.0 + 0.05 * horizon_step)
            soc_min = min(base + margin, 0.90)

        # CRITICAL: never set soc_min above current SOC (LP would be infeasible
        # or forced to charge when we need to discharge)
        if soc_min > current_soc + 0.02:
            soc_min = max(current_soc - 0.05, 0.05)

        return (soc_min, 1.0)

    @classmethod
    def compute_generation_reserve(
        cls,
        total_load_kw: float,
        uncertainty: float,
        dispatch_mode: str = "balanced",
    ) -> float:
        """Spinning reserve requirement (kW) from uncertainty."""
        k = cls.SAFETY_K.get(dispatch_mode, 1.5)
        return total_load_kw * uncertainty * k * 0.5


# =============================================================================
# DISPATCH SOLUTION STRUCTURES
# =============================================================================

@dataclass
class StepDispatch:
    """Optimal dispatch for one MG at one horizon step."""
    mg_id: str = ""
    gen_kw: float = 0.0
    batt_kw: float = 0.0     # +discharge / -charge
    shed_kw: float = 0.0
    export_kw: float = 0.0
    import_kw: float = 0.0
    soc_next: float = 0.0
    gen_on: bool = False
    unmet_kw: float = 0.0    # unmet demand (slack)
    dr_kw: float = 0.0       # dynamic response reduction


@dataclass
class HorizonSolution:
    """Complete solution from the multi-step optimizer."""
    # Dispatch for step t=0 (to be applied)
    dispatches: Dict[str, StepDispatch]
    # Full horizon dispatches for logging
    full_horizon: Dict[str, List[StepDispatch]] = field(default_factory=dict)
    # Summary
    objective_value: float = 0.0
    solver_status: str = "optimal"
    solve_time_ms: float = 0.0
    cost_breakdown: Dict[str, float] = field(default_factory=dict)
    horizon_steps: int = 0
    n_variables: int = 0
    n_constraints: int = 0

    # Convergence info
    solver_iterations: int = 0
    constraint_violations: int = 0
    slack_used_kw: float = 0.0   # total unmet demand from slack

    @property
    def total_shed_kw(self) -> float:
        return sum(d.shed_kw for d in self.dispatches.values())

    @property
    def total_gen_kw(self) -> float:
        return sum(d.gen_kw for d in self.dispatches.values())

    @property
    def total_export_kw(self) -> float:
        return sum(d.export_kw for d in self.dispatches.values())


# =============================================================================
# MULTI-STEP LP FORMULATION
# =============================================================================

class RollingHorizonLP:
    """
    Build and solve the T-step LP for multi-microgrid predictive dispatch.

    Variable layout (flattened):
        For each step t in {0..T-1}, for each MG m in {0..M-1}:
            x[t * M * V + m * V + v]
        where V = 9:  P_gen, P_dis, P_chg, P_shed, P_dr, P_exp, P_imp, SOC, S_unmet

    Battery is split into discharge (>=0) and charge (>=0) to ensure
    degradation cost penalises cycling in both directions (LP trick for |x|).
    Net battery power: P_batt_net = P_dis - P_chg

    S_unmet is the always-feasible slack variable. It absorbs any power
    deficit that gen+batt+transfer cannot cover. Its cost (VOLL) ensures
    it is used only as a last resort.

    Total variables: T x M x 9
    """

    V = 9  # variables per MG per step
    IDX_GEN      = 0
    IDX_BATT_DIS = 1   # battery discharge (>= 0)
    IDX_BATT_CHG = 2   # battery charge    (>= 0)
    IDX_SHED     = 3
    IDX_DR       = 4   # voluntary demand response (>= 0)
    IDX_EXP      = 5
    IDX_IMP      = 6
    IDX_SOC      = 7
    IDX_SLACK    = 8   # unmet demand slack (>= 0)

    def __init__(
        self,
        mg_ids: List[str],
        mg_registry: Dict[str, MicrogridInfo],
        cost_config: PredictiveCostConfig,
        horizon: int = DEFAULT_HORIZON,
    ):
        self.mg_ids = mg_ids
        self.M = len(mg_ids)
        self.T = horizon
        self.N = self.T * self.M * self.V
        self.registry = mg_registry
        self.cost = cost_config

    def _idx(self, t: int, m: int, v: int) -> int:
        """Flat index into decision vector."""
        return t * self.M * self.V + m * self.V + v

    def solve(
        self,
        statuses: Dict[str, MicrogridStatus],
        pv_forecast: np.ndarray,       # (M, T)
        load_forecast: np.ndarray,     # (M, T)
        dr_forecast: np.ndarray,       # (M, T) - max allowed DR reduction
        soc_bounds: Dict[str, List[Tuple[float, float]]],  # mg_id → [(min,max)] per step
        outage_flags: Dict[str, bool],  # mg_id → is_islanded
        fuel_levels: Dict[str, float],  # mg_id → liters remaining
        failed_links: Set[str] = None,  # MGs with communication failure
    ) -> HorizonSolution:
        """
        Formulate and solve the rolling-horizon LP.

        Parameters
        ----------
        statuses      : current MicrogridStatus per MG
        pv_forecast   : predicted PV (kW) shape (M, T)
        load_forecast : predicted load (kW) shape (M, T)
        soc_bounds    : (min, max) SOC per MG per horizon step
        outage_flags  : which MGs are islanded
        fuel_levels   : remaining fuel per MG (liters)

        Returns
        -------
        HorizonSolution with optimal dispatch (t=0 for application).
        """
        t0 = time.perf_counter()

        if not HAS_SCIPY:
            raise RuntimeError(
                "scipy.optimize.linprog is REQUIRED for predictive dispatch. "
                "Install scipy: pip install scipy>=1.9"
            )

        # ── Cost vector c (N,) ──────────────────────────────────────────
        c = np.zeros(self.N)
        for t in range(self.T):
            discount = self.cost.temporal_discount ** t
            for m, mg_id in enumerate(self.mg_ids):
                info = self.registry[mg_id]
                w_m = self.cost.priority_weights.get(info.priority.value, 1.0)
                is_crit = info.priority == MicrogridPriority.CRITICAL

                # Fuel cost
                c[self._idx(t, m, self.IDX_GEN)] = (
                    discount * self.cost.alpha * self.cost.fuel_cost_per_kwh * DT_HOURS
                )
                # Battery degradation: BOTH charge and discharge penalised equally
                # (split-variable LP trick for |P_batt|)
                batt_deg = discount * self.cost.gamma * self.cost.battery_degradation_per_kwh * DT_HOURS
                c[self._idx(t, m, self.IDX_BATT_DIS)] = batt_deg
                c[self._idx(t, m, self.IDX_BATT_CHG)] = batt_deg

                # Shedding (priority-weighted + critical penalty)
                shed_cost = self.cost.beta * self.cost.shedding_penalty_per_kwh * w_m * DT_HOURS
                if is_crit:
                    shed_cost += self.cost.epsilon * self.cost.critical_penalty_per_kwh * DT_HOURS
                c[self._idx(t, m, self.IDX_SHED)] = discount * shed_cost

                # Export transfer loss
                c[self._idx(t, m, self.IDX_EXP)] = (
                    discount * self.cost.delta * self.cost.transfer_loss_cost_per_kwh * DT_HOURS
                )
                # DR Incentive (NEGATIVE cost because we want to maximise profit)
                c[self._idx(t, m, self.IDX_DR)] = (
                    -1.0 * discount * self.cost.dr_incentive_per_kwh * DT_HOURS
                )
                # Import (small cost to avoid unbounded)
                c[self._idx(t, m, self.IDX_IMP)] = discount * self.cost.delta * 0.01 * DT_HOURS

                # SOC: no direct cost (tracked via dynamics)
                c[self._idx(t, m, self.IDX_SOC)] = 0.0

                # Slack (VOLL): extremely expensive to force last-resort use
                # We use a raw high value to ensure it dominates all other costs
                slack_cost = self.cost.unmet_demand_penalty_per_kwh * w_m * DT_HOURS
                if is_crit:
                    slack_cost *= 10.0  # extreme penalty for critical facilities
                c[self._idx(t, m, self.IDX_SLACK)] = discount * slack_cost

        # ── Variable bounds ─────────────────────────────────────────────
        bounds = [(0.0, 0.0)] * self.N
        for t in range(self.T):
            for m, mg_id in enumerate(self.mg_ids):
                info = self.registry[mg_id]
                status = statuses.get(mg_id)
                if status is None:
                    for v in range(self.V):
                        bounds[self._idx(t, m, v)] = (0, 0)
                    continue

                gen_cap = info.generator_capacity_kw
                batt_max_kw = min(
                    info.battery_capacity_kwh,  # 1C rate
                    info.battery_capacity_kwh / max(DT_HOURS, 0.01)
                )
                load_kw = load_forecast[m, t]
                soc_min_t, soc_max_t = soc_bounds.get(mg_id, [(0.15, 1.0)] * self.T)[t]

                # Tighten export during outage
                exp_limit = BUS_CAPACITY_KW
                islanded = outage_flags.get(mg_id, False)
                if islanded:
                    exp_limit *= 0.5

                # Cyber-Resilience: block sharing if link is failed
                force_zero_sharing = failed_links is not None and mg_id in failed_links
                
                bounds[self._idx(t, m, self.IDX_GEN)]      = (0, gen_cap)
                bounds[self._idx(t, m, self.IDX_BATT_DIS)]  = (0, batt_max_kw)
                bounds[self._idx(t, m, self.IDX_BATT_CHG)]  = (0, batt_max_kw)
                bounds[self._idx(t, m, self.IDX_EXP)]       = (0, 0 if force_zero_sharing else exp_limit)
                bounds[self._idx(t, m, self.IDX_IMP)]       = (0, 0 if force_zero_sharing else BUS_CAPACITY_KW)
                bounds[self._idx(t, m, self.IDX_SOC)]       = (soc_min_t, soc_max_t)

                # DR voluntary reduction (based on event target)
                dr_max = dr_forecast[m, t]
                bounds[self._idx(t, m, self.IDX_DR)]        = (0, dr_max)

                # Shedding only meaningful when islanded
                if islanded:
                    bounds[self._idx(t, m, self.IDX_SHED)] = (0, load_kw)
                    # Slack absorbs any deficit gen+batt+transfer cannot cover
                    bounds[self._idx(t, m, self.IDX_SLACK)] = (0, load_kw)
                else:
                    bounds[self._idx(t, m, self.IDX_SHED)] = (0, 0)  # no shedding on grid
                    bounds[self._idx(t, m, self.IDX_SLACK)] = (0, 0)  # no slack on grid

        # ── Equality constraints ────────────────────────────────────────
        A_eq_rows = []
        b_eq_rows = []

        # Track grid-connected C1 rows to add as inequalities instead
        grid_ub_rows = []
        grid_ub_rhs = []

        for t in range(self.T):
            # C1: Power balance per MG
            # P_net = gen + batt_dis - batt_chg + shed + imp - exp
            # Islanded:       P_net =  load - pv  (equality)
            # Grid-connected: P_net <= load - pv  (inequality; grid absorbs deficit)
            for m, mg_id in enumerate(self.mg_ids):
                status = statuses.get(mg_id)
                pv_kw = pv_forecast[m, t]
                ld_kw = load_forecast[m, t]
                islanded = outage_flags.get(mg_id, False)

                row = np.zeros(self.N)
                row[self._idx(t, m, self.IDX_GEN)]      =  1.0
                row[self._idx(t, m, self.IDX_BATT_DIS)]  =  1.0   # discharge adds supply
                row[self._idx(t, m, self.IDX_BATT_CHG)]  = -1.0   # charge consumes power
                row[self._idx(t, m, self.IDX_SHED)]      =  1.0
                row[self._idx(t, m, self.IDX_DR)]        =  1.0
                row[self._idx(t, m, self.IDX_IMP)]       =  1.0
                row[self._idx(t, m, self.IDX_EXP)]       = -1.0
                row[self._idx(t, m, self.IDX_SLACK)]     =  1.0   # slack covers deficit
                rhs = ld_kw - pv_kw  # grid_kw NOT subtracted; grid is implicit slack

                if islanded:
                    # Strict power balance (grid unavailable)
                    A_eq_rows.append(row)
                    b_eq_rows.append(rhs)
                else:
                    # Grid covers any deficit -> inequality (<=)
                    grid_ub_rows.append(row)
                    grid_ub_rhs.append(rhs)

            # C5: Bus balance per step
            bus_row = np.zeros(self.N)
            for m in range(self.M):
                bus_row[self._idx(t, m, self.IDX_EXP)] = BUS_EFFICIENCY
                bus_row[self._idx(t, m, self.IDX_IMP)] = -1.0
            A_eq_rows.append(bus_row)
            b_eq_rows.append(0.0)

            # C2: SOC dynamics
            for m, mg_id in enumerate(self.mg_ids):
                info = self.registry[mg_id]
                status = statuses.get(mg_id)
                e_batt = max(info.battery_capacity_kwh, 0.1)

                row = np.zeros(self.N)
                # SOC[t] = SOC[t-1] - (P_dis - P_chg)·Δt / E_batt
                #   => SOC[t] + P_dis·Δt/E - P_chg·Δt/E = SOC[t-1]
                row[self._idx(t, m, self.IDX_SOC)]      =  1.0
                row[self._idx(t, m, self.IDX_BATT_DIS)]  =  DT_HOURS / e_batt   # discharge reduces SOC
                row[self._idx(t, m, self.IDX_BATT_CHG)]  = -DT_HOURS / e_batt   # charge increases SOC

                if t == 0:
                    soc_now = status.battery_soc_percent / 100.0 if status else 0.5
                    A_eq_rows.append(row)
                    b_eq_rows.append(soc_now)
                else:
                    # SOC[t] + (P_dis - P_chg)[t]·Δt/E = SOC[t-1]
                    row[self._idx(t - 1, m, self.IDX_SOC)] = -1.0
                    A_eq_rows.append(row)
                    b_eq_rows.append(0.0)

        A_eq = np.array(A_eq_rows) if A_eq_rows else None
        b_eq = np.array(b_eq_rows) if b_eq_rows else None

        # ── Inequality constraints ──────────────────────────────────────
        A_ub_rows = list(grid_ub_rows)   # grid-connected power balance (C1-ub)
        b_ub_rows = list(grid_ub_rhs)

        for t in range(self.T):
            # C6: Bus capacity per step
            bus_cap_row = np.zeros(self.N)
            for m in range(self.M):
                bus_cap_row[self._idx(t, m, self.IDX_EXP)] = 1.0
            A_ub_rows.append(bus_cap_row)
            b_ub_rows.append(BUS_CAPACITY_KW)

            for m, mg_id in enumerate(self.mg_ids):
                info = self.registry[mg_id]
                status = statuses.get(mg_id)
                if status is None:
                    continue

                # C7: Critical load protection
                if info.priority == MicrogridPriority.CRITICAL:
                    ld_kw = load_forecast[m, t]
                    max_shed = max(ld_kw - info.critical_load_kw, 0.0)
                    row = np.zeros(self.N)
                    row[self._idx(t, m, self.IDX_SHED)] = 1.0
                    A_ub_rows.append(row)
                    b_ub_rows.append(max_shed)

        # C9: Fuel reserve over full horizon
        for m, mg_id in enumerate(self.mg_ids):
            fuel_avail = fuel_levels.get(mg_id, 0.0)
            fuel_floor = 5.0  # reserve liters
            fuel_headroom = max(fuel_avail - fuel_floor, 0.0)

            row = np.zeros(self.N)
            for t in range(self.T):
                row[self._idx(t, m, self.IDX_GEN)] = FUEL_RATE_L_PER_KWH * DT_HOURS
            A_ub_rows.append(row)
            b_ub_rows.append(fuel_headroom)

        A_ub = np.array(A_ub_rows) if A_ub_rows else None
        b_ub = np.array(b_ub_rows) if b_ub_rows else None

        # ── Solve ───────────────────────────────────────────────────────
        try:
            result = linprog(
                c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                bounds=bounds, method='highs',
                options={'presolve': True, 'time_limit': 5.0, 'dual_feasibility_tolerance': 1e-7},
            )
        except Exception as e:
            logger.error(f"LP solve exception: {e}")
            elapsed = (time.perf_counter() - t0) * 1000
            return self._infeasible_solution(
                statuses, elapsed, f"exception: {e}"
            )

        elapsed_ms = (time.perf_counter() - t0) * 1000

        if not result.success:
            logger.warning(f"Rolling-horizon LP infeasible: {result.message}")
            return self._infeasible_solution(
                statuses, elapsed_ms, result.message
            )

        x = result.x

        # ── Extract dispatches ──────────────────────────────────────────
        step0_dispatches = {}
        full_horizon = {mg_id: [] for mg_id in self.mg_ids}
        cost_fuel = cost_shed = cost_deg = cost_trans = 0.0
        total_slack = 0.0

        for t in range(self.T):
            for m, mg_id in enumerate(self.mg_ids):
                info = self.registry[mg_id]
                w_m = self.cost.priority_weights.get(info.priority.value, 1.0)

                gen_kw   = max(x[self._idx(t, m, self.IDX_GEN)],  0.0)
                dis_kw   = max(x[self._idx(t, m, self.IDX_BATT_DIS)], 0.0)
                chg_kw   = max(x[self._idx(t, m, self.IDX_BATT_CHG)], 0.0)
                batt_kw  = dis_kw - chg_kw   # net: positive = discharge
                shed_kw  = max(x[self._idx(t, m, self.IDX_SHED)], 0.0)
                dr_kw    = max(x[self._idx(t, m, self.IDX_DR)], 0.0)
                exp_kw   = max(x[self._idx(t, m, self.IDX_EXP)],  0.0)
                imp_kw   = max(x[self._idx(t, m, self.IDX_IMP)],  0.0)
                soc_next = x[self._idx(t, m, self.IDX_SOC)]
                unmet_kw = max(x[self._idx(t, m, self.IDX_SLACK)], 0.0)

                total_slack += unmet_kw

                step_d = StepDispatch(
                    mg_id=mg_id,
                    gen_kw=round(gen_kw, 3),
                    batt_kw=round(batt_kw, 3),
                    shed_kw=round(shed_kw + unmet_kw, 3),  # total demand not served
                    dr_kw=round(dr_kw, 3),
                    export_kw=round(exp_kw, 3),
                    import_kw=round(imp_kw, 3),
                    soc_next=round(max(0, min(1, soc_next)), 4),
                    gen_on=gen_kw > 1.0,
                    unmet_kw=round(unmet_kw, 3),
                )
                full_horizon[mg_id].append(step_d)

                if t == 0:
                    step0_dispatches[mg_id] = step_d

                # Accumulate real costs (undiscounted)
                cost_fuel  += self.cost.fuel_cost_per_kwh * gen_kw * DT_HOURS
                cost_shed  += self.cost.shedding_penalty_per_kwh * w_m * shed_kw * DT_HOURS
                cost_deg   += self.cost.battery_degradation_per_kwh * (dis_kw + chg_kw) * DT_HOURS
                cost_trans += self.cost.transfer_loss_cost_per_kwh * (exp_kw + imp_kw) * DT_HOURS

        n_eq = A_eq.shape[0] if A_eq is not None else 0
        n_ub = A_ub.shape[0] if A_ub is not None else 0
        return HorizonSolution(
            dispatches=step0_dispatches,
            full_horizon=full_horizon,
            objective_value=round(float(c @ x), 6),
            solver_status="optimal" if total_slack < 0.1 else "optimal_with_slack",
            solve_time_ms=round(elapsed_ms, 3),
            cost_breakdown={
                'fuel': round(cost_fuel, 4),
                'shedding': round(cost_shed, 4),
                'degradation': round(cost_deg, 4),
                'transfer': round(cost_trans, 4),
            },
            horizon_steps=self.T,
            n_variables=self.N,
            n_constraints=n_eq + n_ub,
            solver_iterations=getattr(result, 'nit', 0),
            constraint_violations=0,
            slack_used_kw=round(total_slack, 3),
        )

    def _infeasible_solution(
        self, statuses: Dict[str, MicrogridStatus], elapsed_ms: float, msg: str
    ) -> HorizonSolution:
        """
        Emergency fallback: heuristic dispatch if LP completely fails.

        With slack variables this should NEVER be reached, but we keep it
        as a safety net. Uses generator at full capacity, battery discharge,
        minimal shedding.
        """
        dispatches = {}
        for mg_id in self.mg_ids:
            info = self.registry.get(mg_id)
            status = statuses.get(mg_id)
            if not info or not status:
                dispatches[mg_id] = StepDispatch(mg_id=mg_id)
                continue
            deficit = max(status.total_load_kw - status.pv_generation_kw, 0)
            gen_kw = min(deficit, info.generator_capacity_kw)
            remaining = max(deficit - gen_kw, 0)
            soc = status.battery_soc_percent / 100.0
            batt_kw = min(remaining, info.battery_capacity_kwh,
                          max((soc - 0.10) * info.battery_capacity_kwh / DT_HOURS, 0))
            remaining -= batt_kw
            shed_kw = max(remaining, 0)
            new_soc = soc - (batt_kw * DT_HOURS / max(info.battery_capacity_kwh, 0.1))
            dispatches[mg_id] = StepDispatch(
                mg_id=mg_id,
                gen_kw=round(gen_kw, 3),
                batt_kw=round(batt_kw, 3),
                shed_kw=round(shed_kw, 3),
                soc_next=round(max(0.05, new_soc), 4),
                gen_on=gen_kw > 1.0,
            )
        return HorizonSolution(
            dispatches={
                mg_id: StepDispatch(mg_id=mg_id) for mg_id in self.mg_ids
            },
            objective_value=float('inf'),
            solver_status=f"infeasible: {msg}",
            solve_time_ms=round(elapsed_ms, 3),
            horizon_steps=self.T,
            n_variables=self.N,
        )


# =============================================================================
# DISPATCH RECORD — for academic analysis
# =============================================================================

@dataclass
class PredictiveDispatchRecord:
    """Per-timestep record for post-hoc analysis and academic reporting."""
    timestamp: datetime
    city_mode: str
    solver_status: str
    solve_time_ms: float
    objective_value: float
    horizon_steps: int
    n_variables: int
    n_constraints: int
    solver_iterations: int
    total_shed_kw: float
    total_gen_kw: float
    total_export_kw: float
    cost_fuel: float
    cost_shedding: float
    cost_degradation: float
    cost_transfer: float
    per_mg: Dict[str, Dict[str, float]] = field(default_factory=dict)


# =============================================================================
# PREDICTIVE DISPATCHER — main entry point
# =============================================================================

class PredictiveDispatcher:
    """
    Top-level orchestrator for rolling-horizon predictive dispatch.

    This replaces OptimizationDispatcher from optimization_ems.py with a
    true multi-step MPC formulation.

    Usage
    -----
    >>> dispatcher = PredictiveDispatcher(
    ...     mg_registry=city_ems.microgrids,
    ...     policy=ResiliencePolicy.CRITICAL_FIRST,
    ... )
    >>> commands, solution = dispatcher.solve(measurements, city_mode)
    """

    def __init__(
        self,
        mg_registry: Dict[str, MicrogridInfo],
        policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST,
        dispatch_mode: str = "balanced",
        horizon: int = DEFAULT_HORIZON,
        solar_provider: Any = None,
        mqtt_publisher: Optional[Any] = None,   # MqttPublisher type
    ):
        if not HAS_SCIPY:
            raise RuntimeError(
                "CRITICAL: scipy.optimize is NOT installed. "
                "The predictive optimization EMS REQUIRES scipy. "
                "Install with: pip install scipy>=1.9\n"
                "This is NOT optional — the optimizer cannot fall back to rule-based."
            )

        self.registry = mg_registry
        self.policy = policy
        self.dispatch_mode = dispatch_mode
        self.horizon = horizon
        self.mg_ids = sorted(mg_registry.keys())
        self.cost_config = PredictiveCostConfig.for_policy(policy)

        # Initialize forecasting component
        self.forecaster = ForecastProvider(mg_registry, solar_provider)
        self.mqtt = mqtt_publisher

        # Runtime statistics
        self.records: List[PredictiveDispatchRecord] = []
        self.solve_count = 0
        self.optimal_count = 0
        self.infeasible_count = 0
        self.total_solve_ms = 0.0
        self.fallback_count = 0

        logger.info(
            f"PredictiveDispatcher initialized: policy={policy.value}, "
            f"horizon={self.horizon} steps ({self.horizon * DT_HOURS:.1f}h), "
            f"dispatch_mode={dispatch_mode}"
        )

    def set_policy(self, policy: ResiliencePolicy):
        """Update resilience policy and re-derive cost config."""
        self.policy = policy
        self.cost_config = PredictiveCostConfig.for_policy(policy)
        logger.info(f"Policy changed to: {policy.value}")

    def solve(
        self,
        measurements: CityWideMeasurements,
        city_mode: CityOperationMode = CityOperationMode.NORMAL,
        outage_preparation: bool = False,
        dr_targets: Optional[Dict[str, float]] = None,
        failed_links: Optional[Set[str]] = None,
        forced_uncertainty: Optional[float] = None,
    ) -> Tuple[Dict[str, SupervisoryCommand], HorizonSolution]:
        """
        Solve the rolling-horizon dispatch for the current timestep.

        Returns
        -------
        (commands, solution) — only step t=0 commands applied.
        """
        # 1. Build forecasts
        pv_fc, load_fc, unc_orig, mg_ids = self.forecaster.build_forecast_arrays(
            measurements, self.registry, self.horizon
        )
        # Apply forced uncertainty override if provided
        unc = np.full_like(unc_orig, forced_uncertainty) if forced_uncertainty is not None else unc_orig

        # 1.1 Build DR forecast (assume current target persists for horizon)
        dr_fc = np.zeros((len(mg_ids), self.horizon))
        if dr_targets:
            for idx, mg_id in enumerate(mg_ids):
                target = dr_targets.get(mg_id, 0.0)
                dr_fc[idx, :] = target

        # 2. Compute per-step SOC bounds
        soc_bounds: Dict[str, List[Tuple[float, float]]] = {}
        for idx, mg_id in enumerate(mg_ids):
            info = self.registry.get(mg_id)
            if info is None:
                soc_bounds[mg_id] = [(0.15, 1.0)] * self.horizon
                continue
            # Get current SOC for feasibility clamping
            status_mg = measurements.microgrid_statuses.get(mg_id)
            cur_soc = (status_mg.battery_soc_percent / 100.0) if status_mg else 0.5
            is_outage = status_mg.is_islanded if status_mg else False

            bounds_list = []
            for t in range(self.horizon):
                bounds_list.append(RobustMarginCalculator.compute_soc_bounds(
                    info, unc[idx], self.dispatch_mode,
                    outage_active=is_outage or outage_preparation,
                    horizon_step=t,
                    current_soc=cur_soc,
                ))
            soc_bounds[mg_id] = bounds_list

        # 3. Gather state
        outage_flags = {}
        fuel_levels = {}
        for mg_id in mg_ids:
            status = measurements.microgrid_statuses.get(mg_id)
            if status:
                outage_flags[mg_id] = status.is_islanded
                fuel_levels[mg_id] = status.fuel_remaining_liters
            else:
                outage_flags[mg_id] = False
                fuel_levels[mg_id] = 0.0

        # 4. Formulate and solve
        lp = RollingHorizonLP(
            mg_ids, self.registry, self.cost_config, self.horizon
        )
        solution = lp.solve(
            statuses=measurements.microgrid_statuses,
            pv_forecast=pv_fc,
            load_forecast=load_fc,
            dr_forecast=dr_fc,
            soc_bounds=soc_bounds,
            outage_flags=outage_flags,
            fuel_levels=fuel_levels,
            failed_links=failed_links,
        )

        # 5. Update stats
        self.solve_count += 1
        self.total_solve_ms += solution.solve_time_ms
        if "infeasible" in solution.solver_status:
            self.infeasible_count += 1
        else:
            self.optimal_count += 1

        # 6. Convert to SupervisoryCommands (step 0 only — MPC)
        commands = self._to_commands(solution, measurements, city_mode)

        # 8. Intelligence: Peak Prediction & Alerts
        self._check_peak_prediction(pv_fc, load_fc, measurements)

        # 9. IoT Sync: Broadcast state to Dashboard
        if self.mqtt:
            self.mqtt.broadcast_city_metrics(self.get_statistics())
            for mg_id in mg_ids:
                status = measurements.microgrid_statuses.get(mg_id)
                if status:
                    # Enrich with dispatcher info
                    state_payload = status.to_dict() if hasattr(status, 'to_dict') else vars(status)
                    self.mqtt.broadcast_state(mg_id, state_payload)

        # 7. Record
        self._record(measurements, city_mode, solution)

        return commands, solution

    def _check_peak_prediction(self, pv_fc: np.ndarray, load_fc: np.ndarray, measurements: CityWideMeasurements):
        """
        Scan the horizon for steps where total load exceeds generation + battery capacity.
        Triggers a proactive alert to the IoT bus.
        """
        if not self.mqtt:
            return

        for t in range(self.horizon):
            total_net_load = 0.0
            total_capacity = 0.0
            
            for m, mg_id in enumerate(self.mg_ids):
                info = self.registry[mg_id]
                status = measurements.microgrid_statuses.get(mg_id)
                
                # Available Gen + PV
                total_capacity += info.generator_capacity_kw + pv_fc[m, t]
                
                # Available Battery Discharge (estimate)
                if status:
                    # Simple estimate: 1C discharge rate restricted by SOC
                    batt_discharge = min(
                        info.battery_capacity_kwh, 
                        (status.battery_soc_percent / 100.0) * info.battery_capacity_kwh / DT_HOURS
                    )
                    total_capacity += batt_discharge
                
                total_net_load += load_fc[m, t]

            # Threshold check (95% stress level)
            if total_net_load > 0.95 * total_capacity:
                eta_minutes = int((t + 1) * DT_HOURS * 60)
                deficit = max(0, total_net_load - total_capacity)
                peak_time = (measurements.timestamp + timedelta(hours=(t+1)*DT_HOURS)).strftime("%H:%M")
                msg = f"Critical Peak Predicted at {peak_time} ({eta_minutes}m ETA). Est. Deficit: {deficit:.1f} kW"
                self.mqtt.broadcast_alert("warning", msg)
                logger.warning(f"Peak Prediction: {msg}")
                break  # Alert once per solve

    def _to_commands(
        self,
        solution: HorizonSolution,
        measurements: CityWideMeasurements,
        city_mode: CityOperationMode,
    ) -> Dict[str, SupervisoryCommand]:
        """Convert step-0 dispatches to SupervisoryCommand dicts."""
        commands = {}
        for mg_id, dispatch in solution.dispatches.items():
            info = self.registry.get(mg_id)
            status = measurements.microgrid_statuses.get(mg_id)
            if not info or not status:
                continue

            load_kw = max(status.total_load_kw, 0.1)
            shed_pct = (dispatch.shed_kw / load_kw) * 100.0
            soc_target = dispatch.soc_next * 100.0

            cmd = SupervisoryCommand(
                microgrid_id=mg_id,
                timestamp=measurements.timestamp,
                target_shed_percent=round(shed_pct, 2),
                battery_soc_target_percent=round(max(15, min(100, soc_target)), 2),
                generator_enable=dispatch.gen_on,
                export_power_kw=round(dispatch.export_kw, 2),
                import_power_kw=round(dispatch.import_power_kw if hasattr(dispatch, 'import_power_kw') else dispatch.import_kw, 2),
                emergency_mode=(city_mode == CityOperationMode.EMERGENCY),
                critical_only_mode=(
                    city_mode == CityOperationMode.EMERGENCY
                    and info.priority != MicrogridPriority.CRITICAL
                ),
                city_priority_level=info.priority.value,
                dr_requested_reduction_kw=round(dispatch.dr_kw, 2),
                reason=f"MPC dispatch [{self.policy.value}|H={self.horizon}]: "
                       f"gen={dispatch.gen_kw:.0f}kW, "
                       f"batt={dispatch.batt_kw:.0f}kW, "
                       f"dr={dispatch.dr_kw:.0f}kW, "
                       f"shed={shed_pct:.1f}%, "
                       f"exp={dispatch.export_kw:.0f}kW",
                # ── Explicit MPC dispatch ──
                mpc_gen_kw=round(dispatch.gen_kw, 3),
                mpc_batt_kw=round(dispatch.batt_kw, 3),
                mpc_shed_kw=round(dispatch.shed_kw, 3),
                mpc_dr_kw=round(dispatch.dr_kw, 3),
                mpc_export_kw=round(dispatch.export_kw, 3),
                mpc_import_kw=round(dispatch.import_kw, 3),
            )
            commands[mg_id] = cmd
        return commands

    def _record(self, meas, city_mode, sol):
        """Store dispatch record for post-hoc analysis."""
        per_mg = {}
        for mg_id, d in sol.dispatches.items():
            per_mg[mg_id] = {
                'gen_kw': d.gen_kw, 'batt_kw': d.batt_kw,
                'shed_kw': d.shed_kw, 'export_kw': d.export_kw,
                'import_kw': d.import_kw, 'soc_next': d.soc_next,
            }
        self.records.append(PredictiveDispatchRecord(
            timestamp=meas.timestamp,
            city_mode=city_mode.value,
            solver_status=sol.solver_status,
            solve_time_ms=sol.solve_time_ms,
            objective_value=sol.objective_value,
            horizon_steps=sol.horizon_steps,
            n_variables=sol.n_variables,
            n_constraints=sol.n_constraints,
            solver_iterations=sol.solver_iterations,
            total_shed_kw=sol.total_shed_kw,
            total_gen_kw=sol.total_gen_kw,
            total_export_kw=sol.total_export_kw,
            cost_fuel=sol.cost_breakdown.get('fuel', 0),
            cost_shedding=sol.cost_breakdown.get('shedding', 0),
            cost_degradation=sol.cost_breakdown.get('degradation', 0),
            cost_transfer=sol.cost_breakdown.get('transfer', 0),
            per_mg=per_mg,
        ))

    def get_statistics(self) -> Dict[str, Any]:
        """Runtime statistics for academic reporting."""
        avg_ms = self.total_solve_ms / max(self.solve_count, 1)
        return {
            'total_solves': self.solve_count,
            'optimal_count': self.optimal_count,
            'infeasible_count': self.infeasible_count,
            'infeasibility_rate': self.infeasible_count / max(self.solve_count, 1),
            'fallback_count': self.fallback_count,
            'avg_solve_time_ms': round(avg_ms, 3),
            'total_solve_time_ms': round(self.total_solve_ms, 3),
            'horizon_steps': self.horizon,
            'horizon_hours': self.horizon * DT_HOURS,
            'total_records': len(self.records),
        }

    def to_dataframe(self):
        """Export dispatch records as pandas DataFrame."""
        rows = []
        for rec in self.records:
            row = {
                'timestamp': rec.timestamp,
                'city_mode': rec.city_mode,
                'solver_status': rec.solver_status,
                'solve_time_ms': rec.solve_time_ms,
                'objective_value': rec.objective_value,
                'horizon_steps': rec.horizon_steps,
                'n_variables': rec.n_variables,
                'n_constraints': rec.n_constraints,
                'solver_iterations': rec.solver_iterations,
                'total_shed_kw': rec.total_shed_kw,
                'total_gen_kw': rec.total_gen_kw,
                'total_export_kw': rec.total_export_kw,
                'cost_fuel': rec.cost_fuel,
                'cost_shedding': rec.cost_shedding,
                'cost_degradation': rec.cost_degradation,
                'cost_transfer': rec.cost_transfer,
            }
            for mg_id, mg_data in rec.per_mg.items():
                for key, val in mg_data.items():
                    row[f'{mg_id}_{key}'] = val
            rows.append(row)
        try:
            import pandas as pd
            return pd.DataFrame(rows)
        except ImportError:
            return rows

    def export_convergence_log(self) -> List[Dict]:
        """Export convergence diagnostics for debugging."""
        return [
            {
                'step': i,
                'timestamp': str(r.timestamp),
                'status': r.solver_status,
                'objective': r.objective_value,
                'solve_ms': r.solve_time_ms,
                'iterations': r.solver_iterations,
                'n_vars': r.n_variables,
                'n_cons': r.n_constraints,
                'shed_kw': r.total_shed_kw,
                'gen_kw': r.total_gen_kw,
            }
            for i, r in enumerate(self.records)
        ]
