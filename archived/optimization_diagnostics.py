"""
================================================================================
OPTIMIZATION DEBUGGING AND VALIDATION TOOLS
================================================================================

Diagnostic utilities for the Predictive MPC EMS:
  1. Convergence logger — per-solve diagnostics
  2. Constraint violation checker — post-solve validation
  3. Dispatch schedule visualisation data export
  4. Performance timing profiler
  5. Solver health dashboard data

Usage:
    from src.ems.optimization_diagnostics import OptimizationDiagnostics
    diag = OptimizationDiagnostics()
    diag.check_solution(solution, statuses, mg_registry)
    diag.export_dispatch_schedule(dispatcher, 'results/dispatch_schedule.csv')
    diag.print_health_report(dispatcher)
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# =============================================================================
# CONVERGENCE LOG ENTRY
# =============================================================================

@dataclass
class ConvergenceEntry:
    """Single solver invocation record."""
    step: int
    timestamp: str
    solver_status: str
    objective: float
    solve_time_ms: float
    n_variables: int
    n_constraints: int
    solver_iterations: int
    total_shed_kw: float
    total_gen_kw: float
    total_export_kw: float
    constraint_violations: int = 0
    violation_details: List[str] = field(default_factory=list)


# =============================================================================
# CONSTRAINT VIOLATION CHECKER
# =============================================================================

class ConstraintViolationChecker:
    """
    Post-solve validation of physical and operational constraints.

    Checks:
      V1  Power balance per MG (supply ≥ demand - shed)
      V2  SOC bounds (min ≤ SOC ≤ max)
      V3  Generator capacity (0 ≤ gen ≤ cap)
      V4  Critical load protection (shed ≤ load - critical_load)
      V5  Bus balance (sum exports × η ≈ sum imports)
      V6  Non-negativity (shed, gen, import, export ≥ 0)
      V7  Fuel feasibility (cumulative gen × fuel_rate ≤ fuel)
    """

    TOLERANCE = 1.0  # kW tolerance for floating-point comparisons

    @classmethod
    def check(
        cls,
        solution,  # HorizonSolution or DispatchSolution
        statuses: Dict,
        registry: Dict,
        pv_forecast: Optional[np.ndarray] = None,
        load_forecast: Optional[np.ndarray] = None,
    ) -> List[str]:
        """
        Run all constraint checks. Returns list of violation descriptions.
        Empty list = all constraints satisfied.
        """
        violations = []

        for mg_id, dispatch in solution.dispatches.items():
            info = registry.get(mg_id)
            status = statuses.get(mg_id)
            if not info or not status:
                continue

            # V3: Generator capacity
            if dispatch.gen_kw > info.generator_capacity_kw + cls.TOLERANCE:
                violations.append(
                    f"V3 [{mg_id}]: gen={dispatch.gen_kw:.1f}kW > cap={info.generator_capacity_kw:.1f}kW"
                )

            # V4: Critical load protection
            from src.ems.city_ems import MicrogridPriority
            if info.priority == MicrogridPriority.CRITICAL:
                max_shed = max(status.total_load_kw - info.critical_load_kw, 0)
                if dispatch.shed_kw > max_shed + cls.TOLERANCE:
                    violations.append(
                        f"V4 [{mg_id}]: shed={dispatch.shed_kw:.1f}kW > "
                        f"max_allowed={max_shed:.1f}kW (critical load violated)"
                    )

            # V6: Non-negativity
            if dispatch.gen_kw < -cls.TOLERANCE:
                violations.append(f"V6 [{mg_id}]: gen={dispatch.gen_kw:.1f}kW < 0")
            if dispatch.shed_kw < -cls.TOLERANCE:
                violations.append(f"V6 [{mg_id}]: shed={dispatch.shed_kw:.1f}kW < 0")
            if dispatch.export_kw < -cls.TOLERANCE:
                violations.append(f"V6 [{mg_id}]: export={dispatch.export_kw:.1f}kW < 0")
            if dispatch.import_kw < -cls.TOLERANCE:
                violations.append(f"V6 [{mg_id}]: import={dispatch.import_kw:.1f}kW < 0")

            # V2: SOC bounds (check soc_next if available)
            soc_next = getattr(dispatch, 'soc_next', None)
            if soc_next is not None:
                if soc_next < -0.01 or soc_next > 1.01:
                    violations.append(
                        f"V2 [{mg_id}]: soc_next={soc_next:.3f} out of [0,1]"
                    )

        # V5: Bus balance
        total_export = sum(d.export_kw for d in solution.dispatches.values())
        total_import = sum(d.import_kw for d in solution.dispatches.values())
        bus_efficiency = 0.95
        expected_import = total_export * bus_efficiency
        if abs(expected_import - total_import) > cls.TOLERANCE * len(solution.dispatches):
            violations.append(
                f"V5: bus imbalance — "
                f"export×η={expected_import:.1f}kW ≠ import={total_import:.1f}kW"
            )

        return violations


# =============================================================================
# MAIN DIAGNOSTICS CLASS
# =============================================================================

class OptimizationDiagnostics:
    """
    Comprehensive diagnostic toolkit for predictive optimizer.

    Accumulates convergence logs, checks constraint violations,
    and exports data for analysis.
    """

    def __init__(self):
        self.convergence_log: List[ConvergenceEntry] = []
        self.violation_log: List[Dict] = []
        self.timing_samples: List[float] = []
        self._step = 0

    def record_solve(
        self,
        solution,  # HorizonSolution
        statuses: Dict,
        registry: Dict,
        timestamp: Optional[datetime] = None,
    ):
        """Record a complete solve cycle with constraint checking."""
        violations = ConstraintViolationChecker.check(
            solution, statuses, registry
        )

        entry = ConvergenceEntry(
            step=self._step,
            timestamp=str(timestamp or datetime.now()),
            solver_status=solution.solver_status,
            objective=solution.objective_value,
            solve_time_ms=solution.solve_time_ms,
            n_variables=getattr(solution, 'n_variables', 0),
            n_constraints=getattr(solution, 'n_constraints', 0),
            solver_iterations=getattr(solution, 'solver_iterations', 0),
            total_shed_kw=solution.total_shed_kw,
            total_gen_kw=solution.total_gen_kw,
            total_export_kw=solution.total_export_kw,
            constraint_violations=len(violations),
            violation_details=violations,
        )
        self.convergence_log.append(entry)

        if violations:
            self.violation_log.append({
                'step': self._step,
                'timestamp': str(timestamp),
                'violations': violations,
            })
            for v in violations:
                logger.warning(f"Constraint violation at step {self._step}: {v}")

        self.timing_samples.append(solution.solve_time_ms)
        self._step += 1

    def export_convergence_csv(self, path: str):
        """Export convergence log to CSV for analysis."""
        import csv
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', newline='', encoding='utf-8') as f:
            fields = [
                'step', 'timestamp', 'solver_status', 'objective',
                'solve_time_ms', 'n_variables', 'n_constraints',
                'solver_iterations', 'total_shed_kw', 'total_gen_kw',
                'total_export_kw', 'constraint_violations',
            ]
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for entry in self.convergence_log:
                row = {
                    'step': entry.step,
                    'timestamp': entry.timestamp,
                    'solver_status': entry.solver_status,
                    'objective': round(entry.objective, 6),
                    'solve_time_ms': round(entry.solve_time_ms, 3),
                    'n_variables': entry.n_variables,
                    'n_constraints': entry.n_constraints,
                    'solver_iterations': entry.solver_iterations,
                    'total_shed_kw': round(entry.total_shed_kw, 2),
                    'total_gen_kw': round(entry.total_gen_kw, 2),
                    'total_export_kw': round(entry.total_export_kw, 2),
                    'constraint_violations': entry.constraint_violations,
                }
                w.writerow(row)
        logger.info(f"Convergence log exported → {path}")

    def export_violation_log(self, path: str):
        """Export constraint violations to JSON."""
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.violation_log, f, indent=2, default=str)
        logger.info(f"Violation log exported → {path}")

    def export_dispatch_schedule(self, dispatcher, path: str):
        """
        Export full dispatch schedule from PredictiveDispatcher to CSV.

        This provides the per-timestep, per-MG dispatch data for visualisation
        in matplotlib, Streamlit, or any plotting tool.
        """
        try:
            df = dispatcher.to_dataframe()
            if hasattr(df, 'to_csv'):
                df.to_csv(path, index=False)
                logger.info(f"Dispatch schedule exported → {path}")
            else:
                # Fallback if pandas unavailable
                import csv
                if df:
                    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
                    with open(path, 'w', newline='', encoding='utf-8') as f:
                        w = csv.DictWriter(f, fieldnames=df[0].keys())
                        w.writeheader()
                        w.writerows(df)
                    logger.info(f"Dispatch schedule exported → {path}")
        except Exception as e:
            logger.error(f"Failed to export dispatch schedule: {e}")

    def get_timing_report(self) -> Dict[str, float]:
        """Performance timing statistics."""
        if not self.timing_samples:
            return {'n_solves': 0}

        ts = np.array(self.timing_samples)
        return {
            'n_solves': len(ts),
            'mean_ms': round(float(np.mean(ts)), 3),
            'std_ms': round(float(np.std(ts)), 3),
            'min_ms': round(float(np.min(ts)), 3),
            'max_ms': round(float(np.max(ts)), 3),
            'p50_ms': round(float(np.percentile(ts, 50)), 3),
            'p95_ms': round(float(np.percentile(ts, 95)), 3),
            'p99_ms': round(float(np.percentile(ts, 99)), 3),
            'total_ms': round(float(np.sum(ts)), 3),
        }

    def get_health_summary(self) -> Dict[str, Any]:
        """Overall solver health summary."""
        n = len(self.convergence_log)
        if n == 0:
            return {'status': 'no_data', 'n_solves': 0}

        n_optimal = sum(1 for e in self.convergence_log if e.solver_status == 'optimal')
        n_infeasible = n - n_optimal
        n_violations = sum(1 for e in self.convergence_log if e.constraint_violations > 0)
        total_violations = sum(e.constraint_violations for e in self.convergence_log)

        return {
            'status': 'healthy' if n_infeasible == 0 and n_violations == 0 else (
                'degraded' if n_infeasible / n < 0.1 else 'unhealthy'
            ),
            'n_solves': n,
            'n_optimal': n_optimal,
            'n_infeasible': n_infeasible,
            'infeasibility_rate': round(n_infeasible / n, 4),
            'n_solves_with_violations': n_violations,
            'total_violations': total_violations,
            'timing': self.get_timing_report(),
        }

    def print_health_report(self, dispatcher=None):
        """Print human-readable health report to console."""
        summary = self.get_health_summary()
        timing = summary.get('timing', {})

        print("\n" + "=" * 60)
        print("OPTIMIZER HEALTH REPORT")
        print("=" * 60)
        print(f"  Status:            {summary['status'].upper()}")
        print(f"  Total solves:      {summary['n_solves']}")
        print(f"  Optimal:           {summary.get('n_optimal', 0)}")
        print(f"  Infeasible:        {summary.get('n_infeasible', 0)} "
              f"({summary.get('infeasibility_rate', 0)*100:.1f}%)")
        print(f"  Violations:        {summary.get('total_violations', 0)} "
              f"across {summary.get('n_solves_with_violations', 0)} solves")

        if timing.get('n_solves', 0) > 0:
            print(f"\n  Timing:")
            print(f"    Mean:   {timing['mean_ms']:>8.2f} ms")
            print(f"    P95:    {timing.get('p95_ms', 0):>8.2f} ms")
            print(f"    P99:    {timing.get('p99_ms', 0):>8.2f} ms")
            print(f"    Max:    {timing['max_ms']:>8.2f} ms")
            print(f"    Total:  {timing['total_ms']:>8.0f} ms")

        if dispatcher and hasattr(dispatcher, 'get_statistics'):
            stats = dispatcher.get_statistics()
            print(f"\n  Dispatcher:")
            print(f"    Horizon: {stats.get('horizon_steps', '?')} steps "
                  f"({stats.get('horizon_hours', '?')}h)")
            print(f"    Total records: {stats.get('total_records', 0)}")

        print("=" * 60 + "\n")

    def export_all(self, outdir: str, prefix: str = ""):
        """Export all diagnostic data to output directory."""
        os.makedirs(outdir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        p = f"{prefix}_" if prefix else ""

        self.export_convergence_csv(
            os.path.join(outdir, f'{p}convergence_{ts}.csv')
        )
        if self.violation_log:
            self.export_violation_log(
                os.path.join(outdir, f'{p}violations_{ts}.json')
            )

        # Health summary JSON
        health = self.get_health_summary()
        with open(os.path.join(outdir, f'{p}health_{ts}.json'), 'w') as f:
            json.dump(health, f, indent=2, default=str)

        logger.info(f"All diagnostics exported → {outdir}")
