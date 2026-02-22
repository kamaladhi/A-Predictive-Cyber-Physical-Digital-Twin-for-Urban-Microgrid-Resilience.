"""
================================================================================
OPTIMIZATION-BASED CITY-LEVEL EMS
================================================================================

LP/MILP-driven predictive dispatch for multi-microgrid coordination.  Replaces
rule-based mode coordination in CityEMS with a single-solve optimization at
each 15-min timestep.

Architecture
------------
    CityEMS.update()
      └── OptimizationDispatcher.solve(state)
            ├── ForecastHorizon.build()       # solar/load lookahead
            ├── RobustReserveCalculator()     # uncertainty → SOC margin
            ├── DispatchProblem.formulate()   # build Ax ≤ b
            └── scipy.linprog / PuLP         # solve in <1 ms
                 └── DispatchSolution → SupervisoryCommands

Mathematical Formulation
------------------------
Minimise weighted multi-objective:
    α·fuel + β·priority-weighted shedding + γ·degradation + δ·transfer loss

Subject to:
    C1  Power balance per MG
    C2  SOC dynamics
    C3  SOC limits (priority-dependent)
    C4  Generator capacity
    C5  Exchange bus aggregate balance
    C6  Bus capacity limit
    C7  Critical load protection (hard)
    C8  Outage state (grid = 0 when islanded)
    C9  Priority ordering (soft / hard)
    C10 Fuel reserve floor

References
----------
[1] IEEE Std 1547-2018, Interconnection and Interoperability of DER.
[2] IEEE Std 1366-2012, Electric Power Distribution Reliability Indices.
[3] Parhizi et al., "State of the Art in Research on Microgrids," IEEE Access, 2015.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

logger = logging.getLogger(__name__)

# Optional solver imports
try:
    from scipy.optimize import linprog, LinearConstraint, OptimizeResult
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    logger.warning("scipy.optimize not available — LP solver disabled")

from src.ems.common import (
    MicrogridPriority, CityOperationMode, ResiliencePolicy,
    MicrogridInfo, MicrogridStatus, CityWideMeasurements,
    SupervisoryCommand, CityControlOutputs
)


# =============================================================================
# CONSTANTS
# =============================================================================

DT_HOURS = 0.25          # 15-min timestep
DEFAULT_HORIZON = 4      # 1-hour lookahead (4 × 15 min)
BUS_CAPACITY_KW = 200.0  # Exchange bus limit
BUS_EFFICIENCY = 0.95    # Transfer efficiency


# =============================================================================
# COST CONFIGURATION
# =============================================================================

@dataclass
class OptimizationCostConfig:
    """
    Cost coefficients for the multi-objective dispatch function.

    Fuel and penalty values are taken from the existing EMSCostConfig
    in hospital_ems.py.  Priority weights map to the city-level priority
    system defined in city_ems.py.

    Scalarisation weights (alpha–delta) control the trade-off and are
    pre-tuned per ResiliencePolicy.
    """

    # ── Per-unit costs ──────────────────────────────────────────────────
    fuel_cost_per_kwh: float = 0.30
    """Generator fuel cost.  Source: EMSCostConfig default."""

    shedding_penalty_per_kwh: float = 5.0
    """Base penalty for every kWh of unserved energy."""

    battery_degradation_per_kwh: float = 0.05
    """Cycle-wear cost.  Source: EMSCostConfig default."""

    transfer_loss_cost_per_kwh: float = (1 - BUS_EFFICIENCY) * 0.12
    """Cost of energy lost in bus transfer."""

    critical_penalty_multiplier: float = 10.0
    """Extra multiplier for critical-load shedding."""

    # ── Priority weights per MG type ────────────────────────────────────
    priority_weights: Dict[int, float] = field(default_factory=lambda: {
        1: 10.0,   # CRITICAL  (hospital)
        2:  3.5,   # HIGH      (university)
        3:  2.0,   # MEDIUM    (industrial)
        4:  1.0,   # LOW       (residential)
    })

    # ── Scalarisation weights ───────────────────────────────────────────
    alpha: float = 0.3   # fuel
    beta:  float = 0.4   # shedding
    gamma: float = 0.2   # degradation
    delta: float = 0.1   # transfer

    @classmethod
    def for_policy(cls, policy: ResiliencePolicy) -> "OptimizationCostConfig":
        """Return policy-specific cost configuration."""
        if policy == ResiliencePolicy.CRITICAL_FIRST:
            return cls(alpha=0.20, beta=0.60, gamma=0.10, delta=0.10)
        elif policy == ResiliencePolicy.ECONOMIC:
            return cls(alpha=0.50, beta=0.20, gamma=0.20, delta=0.10)
        elif policy == ResiliencePolicy.EQUITABLE:
            cfg = cls(alpha=0.30, beta=0.30, gamma=0.20, delta=0.20)
            cfg.priority_weights = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}
            return cfg
        else:  # BALANCED
            return cls(alpha=0.30, beta=0.30, gamma=0.20, delta=0.20)


# =============================================================================
# FORECAST HORIZON
# =============================================================================

@dataclass
class ForecastHorizon:
    """
    Multi-step lookahead for solar PV and load per microgrid.

    Solar:  uses the 1-h forecast from microgridMeasurements.pv_forecast_1h
            (from the LSTM solar forecaster with MC-Dropout uncertainty).
            Steps beyond the 1-h mark repeat the last known forecast value.

    Load:   persistence forecast — current load repeated for T steps
            with optional ±noise drawn from forecast_uncertainty.

    This class builds the arrays indexed [mg_index, step].
    """

    pv_forecast: np.ndarray     # shape (n_mg, T)
    load_forecast: np.ndarray   # shape (n_mg, T)
    uncertainty: np.ndarray     # shape (n_mg,)  — std. dev per MG
    mg_ids: List[str]           # ordered MG IDs
    horizon: int = DEFAULT_HORIZON

    @classmethod
    def build(
        cls,
        measurements: CityWideMeasurements,
        mg_registry: Dict[str, MicrogridInfo],
        horizon: int = DEFAULT_HORIZON,
    ) -> "ForecastHorizon":
        """
        Build forecast arrays from current measurements.

        Parameters
        ----------
        measurements : CityWideMeasurements
        mg_registry  : registered MicrogridInfo map
        horizon      : number of 15-min steps ahead

        Returns
        -------
        ForecastHorizon with pv, load, and uncertainty arrays.
        """
        mg_ids = sorted(mg_registry.keys())
        n = len(mg_ids)
        pv = np.zeros((n, horizon))
        load = np.zeros((n, horizon))
        unc = np.zeros(n)

        for idx, mg_id in enumerate(mg_ids):
            status = measurements.microgrid_statuses.get(mg_id)
            if status is None:
                continue

            # ── Solar forecast ──────────────────────────────────────────
            current_pv = max(status.pv_generation_kw, 0.0)
            # Use 1-h forecast if available, else persistence
            pv_1h = getattr(status, 'pv_forecast_1h', None)
            if pv_1h is not None and pv_1h > 0:
                # Linear ramp from current to 1-h forecast
                for t in range(horizon):
                    frac = (t + 1) / horizon
                    pv[idx, t] = current_pv + frac * (pv_1h - current_pv)
            else:
                pv[idx, :] = current_pv

            # ── Load forecast (persistence) ─────────────────────────────
            load[idx, :] = max(status.total_load_kw, 0.1)

            # ── Uncertainty ─────────────────────────────────────────────
            forecast_unc = getattr(status, 'forecast_uncertainty', None)
            unc[idx] = forecast_unc if forecast_unc is not None else 0.1

        return cls(
            pv_forecast=pv,
            load_forecast=load,
            uncertainty=unc,
            mg_ids=mg_ids,
            horizon=horizon,
        )


# =============================================================================
# ROBUST RESERVE CALCULATOR
# =============================================================================

class RobustReserveCalculator:
    """
    Convert forecast uncertainty to SOC reserve margins.

    Uses the safety-factor approach:
        SOC_target = SOC_min + σ_forecast × k_safety

    k_safety maps to the existing DispatchMode enum:
        CONSERVATIVE → 2.0
        BALANCED     → 1.5
        AGGRESSIVE   → 1.0
    """

    SAFETY_FACTORS = {
        "conservative": 2.0,
        "balanced":     1.5,
        "aggressive":   1.0,
    }

    # Priority-dependent SOC floors (fraction of capacity)
    SOC_MIN = {
        MicrogridPriority.CRITICAL: 0.40,
        MicrogridPriority.HIGH:     0.30,
        MicrogridPriority.MEDIUM:   0.20,
        MicrogridPriority.LOW:      0.20,
    }

    SOC_MAX = 1.0

    @classmethod
    def compute_soc_bounds(
        cls,
        mg_info: MicrogridInfo,
        forecast_uncertainty: float,
        dispatch_mode: str = "balanced",
        outage_preparation: bool = False,
    ) -> Tuple[float, float]:
        """
        Compute (soc_min, soc_max) in [0, 1] for a given MG.

        Parameters
        ----------
        mg_info : MicrogridInfo
        forecast_uncertainty : normalised std from MC-Dropout
        dispatch_mode : "conservative" | "balanced" | "aggressive"
        outage_preparation : if True, tighten floors by 0.15

        Returns
        -------
        (soc_min_fraction, soc_max_fraction)
        """
        k = cls.SAFETY_FACTORS.get(dispatch_mode, 1.5)
        base_min = cls.SOC_MIN.get(mg_info.priority, 0.20)

        # Reserve margin proportional to uncertainty
        margin = forecast_uncertainty * k * 0.1  # Scale into SOC fraction
        soc_min = min(base_min + margin, 0.80)

        if outage_preparation:
            soc_min = min(soc_min + 0.15, 0.85)

        return (soc_min, cls.SOC_MAX)


# =============================================================================
# DISPATCH SOLUTION
# =============================================================================

@dataclass
class MGDispatch:
    """Optimal dispatch for one microgrid at one timestep."""
    mg_id: str
    gen_kw: float = 0.0
    batt_kw: float = 0.0     # positive = discharge, negative = charge
    shed_kw: float = 0.0
    export_kw: float = 0.0
    import_kw: float = 0.0
    gen_on: bool = False


@dataclass
class DispatchSolution:
    """Complete solution from the optimizer for one timestep."""
    dispatches: Dict[str, MGDispatch]
    objective_value: float = 0.0
    solver_status: str = "optimal"
    solve_time_ms: float = 0.0
    cost_breakdown: Dict[str, float] = field(default_factory=dict)

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
# DISPATCH PROBLEM — LP FORMULATION
# =============================================================================

class DispatchProblem:
    """
    Build and solve the LP/MILP dispatch problem for one timestep.

    Decision variable layout (per MG, continuous):
        x = [P_gen_1, P_batt_1, P_shed_1, P_exp_1, P_imp_1,
             P_gen_2, P_batt_2, P_shed_2, P_exp_2, P_imp_2, ...]

    5 variables per MG → total = 5 × n_mg.

    Constraints are built as   A_ub @ x ≤ b_ub   and   A_eq @ x = b_eq.
    """

    VARS_PER_MG = 5  # gen, batt, shed, exp, imp
    # Variable offsets within each MG block
    IDX_GEN  = 0
    IDX_BATT = 1
    IDX_SHED = 2
    IDX_EXP  = 3
    IDX_IMP  = 4

    def __init__(
        self,
        mg_ids: List[str],
        mg_registry: Dict[str, MicrogridInfo],
        cost_config: OptimizationCostConfig,
    ):
        self.mg_ids = mg_ids
        self.n_mg = len(mg_ids)
        self.n_vars = self.VARS_PER_MG * self.n_mg
        self.registry = mg_registry
        self.cost = cost_config

    def _var_idx(self, mg_idx: int, var_offset: int) -> int:
        """Return index of variable in the flat decision vector."""
        return mg_idx * self.VARS_PER_MG + var_offset

    def formulate_and_solve(
        self,
        statuses: Dict[str, MicrogridStatus],
        pv_forecast: np.ndarray,      # shape (n_mg,) — current step
        load_forecast: np.ndarray,     # shape (n_mg,) — current step
        soc_bounds: Dict[str, Tuple[float, float]],
        outage_preparation: bool = False,
    ) -> DispatchSolution:
        """
        Formulate LP matrices and solve.

        Parameters
        ----------
        statuses : current MicrogridStatus per MG
        pv_forecast : predicted PV (kW) per MG for this step
        load_forecast : predicted load (kW) per MG for this step
        soc_bounds : (min, max) SOC fraction per MG
        outage_preparation : tighten export limits

        Returns
        -------
        DispatchSolution with optimal dispatch per MG.
        """
        import time
        t0 = time.perf_counter()

        # ── Build cost vector c ─────────────────────────────────────────
        c = np.zeros(self.n_vars)
        for i, mg_id in enumerate(self.mg_ids):
            info = self.registry[mg_id]
            w_i = self.cost.priority_weights.get(info.priority.value, 1.0)

            # Fuel cost
            c[self._var_idx(i, self.IDX_GEN)] = (
                self.cost.alpha * self.cost.fuel_cost_per_kwh * DT_HOURS
            )
            # Battery degradation (|P_batt|; for LP we penalise both
            # directions equally — this is an approximation, exact |·|
            # would need variable splitting)
            c[self._var_idx(i, self.IDX_BATT)] = (
                self.cost.gamma * self.cost.battery_degradation_per_kwh * DT_HOURS
            )
            # Shedding penalty (priority-weighted)
            c[self._var_idx(i, self.IDX_SHED)] = (
                self.cost.beta * self.cost.shedding_penalty_per_kwh * w_i * DT_HOURS
            )
            # Export cost (transfer loss)
            c[self._var_idx(i, self.IDX_EXP)] = (
                self.cost.delta * self.cost.transfer_loss_cost_per_kwh * DT_HOURS
            )
            # Import cost (marginal benefit → small negative or zero;
            # kept non-negative to avoid unbounded)
            c[self._var_idx(i, self.IDX_IMP)] = (
                self.cost.delta * 0.01 * DT_HOURS
            )

        # ── Build bounds ────────────────────────────────────────────────
        bounds = []
        for i, mg_id in enumerate(self.mg_ids):
            info = self.registry[mg_id]
            status = statuses.get(mg_id)
            if status is None:
                bounds.extend([(0, 0)] * self.VARS_PER_MG)
                continue

            gen_cap = info.generator_capacity_kw
            batt_cap_kw = info.battery_capacity_kwh / DT_HOURS  # max power
            # Limit battery power to a reasonable C-rate (1C max)
            batt_max = min(batt_cap_kw, info.battery_capacity_kwh)
            load_kw = load_forecast[i]

            # Export limit: tighten during outage preparation
            exp_limit = BUS_CAPACITY_KW
            if outage_preparation:
                exp_limit *= 0.50

            bounds.append((0, gen_cap))       # P_gen
            bounds.append((-batt_max, batt_max))  # P_batt (neg = charge)
            bounds.append((0, load_kw))       # P_shed
            bounds.append((0, exp_limit))     # P_exp
            bounds.append((0, BUS_CAPACITY_KW))  # P_imp

        # ── Equality constraints: power balance per MG (C1) ─────────────
        # P_pv + P_gen + P_batt + P_imp + P_grid = Load - P_shed + P_exp
        # Rearranged: P_gen + P_batt - P_shed + P_imp - P_exp = Load - P_pv - P_grid
        A_eq_rows = []
        b_eq_rows = []

        for i, mg_id in enumerate(self.mg_ids):
            status = statuses.get(mg_id)
            info = self.registry[mg_id]
            pv_kw = pv_forecast[i]
            ld_kw = load_forecast[i]

            # Grid power: 0 when islanded
            grid_kw = 0.0
            if status and not status.is_islanded and status.grid_available:
                grid_kw = status.grid_power_kw

            row = np.zeros(self.n_vars)
            row[self._var_idx(i, self.IDX_GEN)]  =  1.0
            row[self._var_idx(i, self.IDX_BATT)] =  1.0
            row[self._var_idx(i, self.IDX_SHED)] =  1.0  # shed reduces need
            row[self._var_idx(i, self.IDX_IMP)]  =  1.0
            row[self._var_idx(i, self.IDX_EXP)]  = -1.0

            rhs = ld_kw - pv_kw - grid_kw

            A_eq_rows.append(row)
            b_eq_rows.append(rhs)

        # ── Equality constraint: bus balance (C5) ───────────────────────
        # Σ P_exp × η = Σ P_imp
        bus_row = np.zeros(self.n_vars)
        for i in range(self.n_mg):
            bus_row[self._var_idx(i, self.IDX_EXP)] = BUS_EFFICIENCY
            bus_row[self._var_idx(i, self.IDX_IMP)] = -1.0
        A_eq_rows.append(bus_row)
        b_eq_rows.append(0.0)

        A_eq = np.array(A_eq_rows)
        b_eq = np.array(b_eq_rows)

        # ── Inequality constraints ──────────────────────────────────────
        A_ub_rows = []
        b_ub_rows = []

        for i, mg_id in enumerate(self.mg_ids):
            info = self.registry[mg_id]
            status = statuses.get(mg_id)
            if status is None:
                continue

            # C3: SOC limits — prevent battery from exceeding bounds
            # SOC_next = SOC_now - P_batt × Δt / E_batt
            # SOC_next ≤ SOC_max → -P_batt × Δt/E ≤ SOC_max - SOC_now
            # SOC_next ≥ SOC_min → P_batt × Δt/E ≤ SOC_now - SOC_min
            soc_now = status.battery_soc_percent / 100.0
            e_batt = max(info.battery_capacity_kwh, 0.1)
            soc_min, soc_max = soc_bounds.get(mg_id, (0.20, 1.0))

            # Upper SOC limit (prevent over-charge): -P_batt ≤ (SOC_max - SOC_now) × E / Δt
            row_upper = np.zeros(self.n_vars)
            row_upper[self._var_idx(i, self.IDX_BATT)] = -1.0
            A_ub_rows.append(row_upper)
            b_ub_rows.append((soc_max - soc_now) * e_batt / DT_HOURS)

            # Lower SOC limit (prevent over-discharge): P_batt ≤ (SOC_now - SOC_min) × E / Δt
            row_lower = np.zeros(self.n_vars)
            row_lower[self._var_idx(i, self.IDX_BATT)] = 1.0
            A_ub_rows.append(row_lower)
            b_ub_rows.append((soc_now - soc_min) * e_batt / DT_HOURS)

            # C7: Critical load protection — shed ≤ load - critical_load
            if info.priority == MicrogridPriority.CRITICAL:
                ld_kw = load_forecast[i]
                max_shed = max(ld_kw - info.critical_load_kw, 0.0)
                row_crit = np.zeros(self.n_vars)
                row_crit[self._var_idx(i, self.IDX_SHED)] = 1.0
                A_ub_rows.append(row_crit)
                b_ub_rows.append(max_shed)

            # C10: Fuel reserve floor (simplified)
            # P_gen × fuel_rate × Δt ≤ fuel_remaining - fuel_min
            if status.fuel_remaining_liters > 0:
                fuel_rate = 0.3  # L/kWh approximate
                fuel_min = 5.0  # reserve floor liters
                fuel_headroom = max(status.fuel_remaining_liters - fuel_min, 0)
                row_fuel = np.zeros(self.n_vars)
                row_fuel[self._var_idx(i, self.IDX_GEN)] = fuel_rate * DT_HOURS
                A_ub_rows.append(row_fuel)
                b_ub_rows.append(fuel_headroom)

        # C6: Bus capacity — Σ P_exp ≤ BUS_CAPACITY
        bus_cap_row = np.zeros(self.n_vars)
        for i in range(self.n_mg):
            bus_cap_row[self._var_idx(i, self.IDX_EXP)] = 1.0
        A_ub_rows.append(bus_cap_row)
        b_ub_rows.append(BUS_CAPACITY_KW)

        A_ub = np.array(A_ub_rows) if A_ub_rows else None
        b_ub = np.array(b_ub_rows) if b_ub_rows else None

        # ── Solve ───────────────────────────────────────────────────────
        solution = self._solve_scipy(c, A_ub, b_ub, A_eq, b_eq, bounds)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        if solution is None:
            # Infeasible → return zero dispatch (rule-based fallback)
            logger.warning("LP infeasible — returning zero dispatch")
            return DispatchSolution(
                dispatches={mg_id: MGDispatch(mg_id=mg_id) for mg_id in self.mg_ids},
                objective_value=float('inf'),
                solver_status="infeasible",
                solve_time_ms=elapsed_ms,
            )

        # ── Extract solution ────────────────────────────────────────────
        x = solution
        dispatches = {}
        cost_fuel = cost_shed = cost_deg = cost_trans = 0.0

        for i, mg_id in enumerate(self.mg_ids):
            gen_kw  = max(x[self._var_idx(i, self.IDX_GEN)], 0.0)
            batt_kw = x[self._var_idx(i, self.IDX_BATT)]
            shed_kw = max(x[self._var_idx(i, self.IDX_SHED)], 0.0)
            exp_kw  = max(x[self._var_idx(i, self.IDX_EXP)], 0.0)
            imp_kw  = max(x[self._var_idx(i, self.IDX_IMP)], 0.0)

            dispatches[mg_id] = MGDispatch(
                mg_id=mg_id,
                gen_kw=round(gen_kw, 2),
                batt_kw=round(batt_kw, 2),
                shed_kw=round(shed_kw, 2),
                export_kw=round(exp_kw, 2),
                import_kw=round(imp_kw, 2),
                gen_on=gen_kw > 1.0,
            )

            # Accumulate cost breakdown
            info = self.registry[mg_id]
            w_i = self.cost.priority_weights.get(info.priority.value, 1.0)
            cost_fuel  += self.cost.fuel_cost_per_kwh * gen_kw * DT_HOURS
            cost_shed  += self.cost.shedding_penalty_per_kwh * w_i * shed_kw * DT_HOURS
            cost_deg   += self.cost.battery_degradation_per_kwh * abs(batt_kw) * DT_HOURS
            cost_trans += self.cost.transfer_loss_cost_per_kwh * (exp_kw + imp_kw) * DT_HOURS

        return DispatchSolution(
            dispatches=dispatches,
            objective_value=round(float(c @ x), 6),
            solver_status="optimal",
            solve_time_ms=round(elapsed_ms, 3),
            cost_breakdown={
                'fuel': round(cost_fuel, 4),
                'shedding': round(cost_shed, 4),
                'degradation': round(cost_deg, 4),
                'transfer': round(cost_trans, 4),
            },
        )

    def _solve_scipy(
        self,
        c: np.ndarray,
        A_ub: Optional[np.ndarray],
        b_ub: Optional[np.ndarray],
        A_eq: np.ndarray,
        b_eq: np.ndarray,
        bounds: list,
    ) -> Optional[np.ndarray]:
        """Solve LP using scipy.optimize.linprog (HiGHS backend)."""
        if not HAS_SCIPY:
            logger.error("scipy not available for LP solve")
            return None

        try:
            result = linprog(
                c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                bounds=bounds, method='highs',
                options={'presolve': True, 'time_limit': 1.0},
            )
            if result.success:
                return result.x
            else:
                logger.warning(f"LP solver failed: {result.message}")
                return None
        except Exception as e:
            logger.error(f"LP solve error: {e}")
            return None


# =============================================================================
# DISPATCH RECORD — for academic analysis
# =============================================================================

@dataclass
class DispatchRecord:
    """
    Per-timestep record for post-hoc analysis and academic reporting.

    Stored by OptimizationDispatcher for export to DataFrame/CSV.
    """
    timestamp: datetime
    city_mode: str
    solver_status: str
    solve_time_ms: float
    objective_value: float
    total_shed_kw: float
    total_gen_kw: float
    total_export_kw: float
    cost_fuel: float
    cost_shedding: float
    cost_degradation: float
    cost_transfer: float
    per_mg: Dict[str, Dict[str, float]] = field(default_factory=dict)


# =============================================================================
# OPTIMIZATION DISPATCHER — main entry point
# =============================================================================

class OptimizationDispatcher:
    """
    Top-level dispatcher that orchestrates forecast, reserve calculation,
    problem formulation, and solution extraction.

    Produces SupervisoryCommand dicts identical in structure to those
    from the rule-based CityEMS, enabling drop-in replacement.

    Usage
    -----
    >>> dispatcher = OptimizationDispatcher(
    ...     mg_registry=city_ems.microgrids,
    ...     policy=ResiliencePolicy.CRITICAL_FIRST,
    ... )
    >>> commands = dispatcher.solve(measurements)
    """

    def __init__(
        self,
        mg_registry: Dict[str, MicrogridInfo],
        policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST,
        dispatch_mode: str = "balanced",
        horizon: int = DEFAULT_HORIZON,
    ):
        self.registry = mg_registry
        self.policy = policy
        self.dispatch_mode = dispatch_mode
        self.horizon = horizon
        self.cost_config = OptimizationCostConfig.for_policy(policy)

        # Runtime tracking
        self.records: List[DispatchRecord] = []
        self.solve_count = 0
        self.infeasible_count = 0
        self.total_solve_ms = 0.0

        logger.info(
            f"OptimizationDispatcher initialized: policy={policy.value}, "
            f"dispatch_mode={dispatch_mode}, horizon={horizon}"
        )

    def set_policy(self, policy: ResiliencePolicy):
        """Update resilience policy and re-derive cost config."""
        self.policy = policy
        self.cost_config = OptimizationCostConfig.for_policy(policy)
        logger.info(f"Optimization policy changed to: {policy.value}")

    def solve(
        self,
        measurements: CityWideMeasurements,
        city_mode: CityOperationMode = CityOperationMode.NORMAL,
        outage_preparation: bool = False,
    ) -> Tuple[Dict[str, SupervisoryCommand], DispatchSolution]:
        """
        Solve the dispatch problem for the current timestep.

        Parameters
        ----------
        measurements : CityWideMeasurements from the digital twin
        city_mode : current operation mode (for logging/record)
        outage_preparation : if True, tighten SOC floors and export limits

        Returns
        -------
        (commands, solution) where commands is Dict[str, SupervisoryCommand]
        and solution is the raw DispatchSolution.
        """
        # 1. Build forecast horizon
        forecast = ForecastHorizon.build(
            measurements, self.registry, self.horizon
        )

        # 2. Compute SOC bounds per MG
        soc_bounds: Dict[str, Tuple[float, float]] = {}
        for idx, mg_id in enumerate(forecast.mg_ids):
            info = self.registry.get(mg_id)
            if info:
                soc_bounds[mg_id] = RobustReserveCalculator.compute_soc_bounds(
                    info,
                    forecast.uncertainty[idx],
                    self.dispatch_mode,
                    outage_preparation,
                )

        # 3. Formulate and solve (use step 0 of forecast)
        problem = DispatchProblem(
            forecast.mg_ids, self.registry, self.cost_config
        )
        solution = problem.formulate_and_solve(
            statuses=measurements.microgrid_statuses,
            pv_forecast=forecast.pv_forecast[:, 0],
            load_forecast=forecast.load_forecast[:, 0],
            soc_bounds=soc_bounds,
            outage_preparation=outage_preparation,
        )

        # 4. Update runtime stats
        self.solve_count += 1
        self.total_solve_ms += solution.solve_time_ms
        if solution.solver_status != "optimal":
            self.infeasible_count += 1

        # 5. Convert to SupervisoryCommands
        commands = self._solution_to_commands(
            solution, measurements, city_mode
        )

        # 6. Record for analysis
        self._record(measurements, city_mode, solution)

        return commands, solution

    def _solution_to_commands(
        self,
        solution: DispatchSolution,
        measurements: CityWideMeasurements,
        city_mode: CityOperationMode,
    ) -> Dict[str, SupervisoryCommand]:
        """Convert DispatchSolution to SupervisoryCommand dict."""
        commands = {}

        for mg_id, dispatch in solution.dispatches.items():
            info = self.registry.get(mg_id)
            status = measurements.microgrid_statuses.get(mg_id)
            if info is None or status is None:
                continue

            # Shed percent
            load_kw = max(status.total_load_kw, 0.1)
            shed_pct = (dispatch.shed_kw / load_kw) * 100.0

            # SOC target: current SOC minus planned discharge/charge
            soc_now = status.battery_soc_percent
            if info.battery_capacity_kwh > 0:
                soc_delta = (dispatch.batt_kw * DT_HOURS / info.battery_capacity_kwh) * 100
                soc_target = soc_now - soc_delta
                soc_target = max(20.0, min(100.0, soc_target))
            else:
                soc_target = soc_now

            cmd = SupervisoryCommand(
                microgrid_id=mg_id,
                timestamp=measurements.timestamp,
                target_shed_percent=round(shed_pct, 2),
                battery_soc_target_percent=round(soc_target, 2),
                generator_enable=dispatch.gen_on,
                export_power_kw=round(dispatch.export_kw, 2),
                import_power_kw=round(dispatch.import_kw, 2),
                emergency_mode=(city_mode == CityOperationMode.EMERGENCY),
                critical_only_mode=(
                    city_mode == CityOperationMode.EMERGENCY
                    and info.priority != MicrogridPriority.CRITICAL
                ),
                city_priority_level=info.priority.value,
                reason=f"Optimized dispatch [{self.policy.value}]: "
                       f"gen={dispatch.gen_kw:.0f}kW, "
                       f"batt={dispatch.batt_kw:.0f}kW, "
                       f"shed={shed_pct:.1f}%",
            )
            commands[mg_id] = cmd

        return commands

    def _record(
        self,
        meas: CityWideMeasurements,
        city_mode: CityOperationMode,
        sol: DispatchSolution,
    ):
        """Store a DispatchRecord for post-hoc analysis."""
        per_mg = {}
        for mg_id, d in sol.dispatches.items():
            per_mg[mg_id] = {
                'gen_kw': d.gen_kw,
                'batt_kw': d.batt_kw,
                'shed_kw': d.shed_kw,
                'export_kw': d.export_kw,
                'import_kw': d.import_kw,
            }

        record = DispatchRecord(
            timestamp=meas.timestamp,
            city_mode=city_mode.value,
            solver_status=sol.solver_status,
            solve_time_ms=sol.solve_time_ms,
            objective_value=sol.objective_value,
            total_shed_kw=sol.total_shed_kw,
            total_gen_kw=sol.total_gen_kw,
            total_export_kw=sol.total_export_kw,
            cost_fuel=sol.cost_breakdown.get('fuel', 0),
            cost_shedding=sol.cost_breakdown.get('shedding', 0),
            cost_degradation=sol.cost_breakdown.get('degradation', 0),
            cost_transfer=sol.cost_breakdown.get('transfer', 0),
            per_mg=per_mg,
        )
        self.records.append(record)

    def get_statistics(self) -> Dict[str, Any]:
        """Return runtime statistics for academic reporting."""
        avg_ms = (
            self.total_solve_ms / self.solve_count
            if self.solve_count > 0 else 0
        )
        return {
            'total_solves': self.solve_count,
            'infeasible_count': self.infeasible_count,
            'infeasibility_rate': (
                self.infeasible_count / self.solve_count
                if self.solve_count > 0 else 0
            ),
            'avg_solve_time_ms': round(avg_ms, 3),
            'total_solve_time_ms': round(self.total_solve_ms, 3),
            'total_records': len(self.records),
        }

    def to_dataframe(self):
        """
        Export dispatch records as a pandas DataFrame.

        Falls back to list of dicts if pandas is unavailable.
        """
        rows = []
        for rec in self.records:
            row = {
                'timestamp': rec.timestamp,
                'city_mode': rec.city_mode,
                'solver_status': rec.solver_status,
                'solve_time_ms': rec.solve_time_ms,
                'objective_value': rec.objective_value,
                'total_shed_kw': rec.total_shed_kw,
                'total_gen_kw': rec.total_gen_kw,
                'total_export_kw': rec.total_export_kw,
                'cost_fuel': rec.cost_fuel,
                'cost_shedding': rec.cost_shedding,
                'cost_degradation': rec.cost_degradation,
                'cost_transfer': rec.cost_transfer,
            }
            # Flatten per-MG data
            for mg_id, mg_data in rec.per_mg.items():
                for key, val in mg_data.items():
                    row[f'{mg_id}_{key}'] = val
            rows.append(row)

        try:
            import pandas as pd
            return pd.DataFrame(rows)
        except ImportError:
            return rows
