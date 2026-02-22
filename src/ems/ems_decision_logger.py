"""
EMS Decision Logger — Research Experiment Logging
===================================================

Append-only JSONL logging of EMS decisions for research analysis.

Logs:
  - Forecast usage in decisions
  - Battery SOC changes
  - Load shedding events
  - Generator start/stop events
  - Forecast-influenced adjustments

Usage:
    logger = EMSDecisionLogger('logs/hospital_ems_decisions.jsonl')
    logger.log_decision(timestamp, measurements, outputs, forecast_info)
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class EMSDecisionLogger:
    """
    Structured JSONL logger for EMS decisions.

    Each line is a self-contained JSON object for easy post-processing
    with pandas (pd.read_json(path, lines=True)).

    Thread-safe via append-only writes. Safe if file doesn't exist.
    Logging failures never break the EMS.
    """

    def __init__(self, log_path: str):
        """
        Parameters
        ----------
        log_path : str
            Path to JSONL output file. Created if not exists.
        """
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path) if os.path.dirname(log_path) else '.', exist_ok=True)
        self._enabled = True

    def log_decision(self,
                     timestamp: datetime,
                     microgrid_type: str,
                     operation_mode: str,
                     battery_soc: float,
                     battery_command_kw: float,
                     gen1_running: bool,
                     gen2_running: bool,
                     gen1_command_kw: float,
                     gen2_command_kw: float,
                     pv_power_kw: float,
                     total_load_kw: float,
                     load_sheds: Dict[str, float],
                     forecast_info: Optional[Dict[str, Any]] = None,
                     decision_reasons: Optional[list] = None) -> None:
        """
        Log a single EMS decision to JSONL.

        Parameters
        ----------
        timestamp : datetime
            Simulation timestamp.
        microgrid_type : str
            'hospital', 'university', 'industry', 'residence'.
        operation_mode : str
            Current operation mode.
        battery_soc : float
            Battery state of charge (%).
        battery_command_kw : float
            Battery command (positive=discharge, negative=charge).
        gen1_running, gen2_running : bool
            Generator status.
        gen1_command_kw, gen2_command_kw : float
            Generator power setpoints.
        pv_power_kw : float
            Current PV power.
        total_load_kw : float
            Total load demand.
        load_sheds : dict
            Active load sheds {category: shed_kw}.
        forecast_info : dict or None
            Forecast data used in decision.
        decision_reasons : list or None
            Human-readable reasons for forecast-influenced decisions.
        """
        if not self._enabled:
            return

        record = {
            'timestamp': timestamp.isoformat(),
            'microgrid': microgrid_type,
            'mode': operation_mode,
            'battery_soc_pct': round(battery_soc, 1),
            'battery_cmd_kw': round(battery_command_kw, 1),
            'gen1_running': gen1_running,
            'gen2_running': gen2_running,
            'gen1_cmd_kw': round(gen1_command_kw, 1),
            'gen2_cmd_kw': round(gen2_command_kw, 1),
            'pv_kw': round(pv_power_kw, 1),
            'load_kw': round(total_load_kw, 1),
            'total_shed_kw': round(sum(load_sheds.values()), 1) if load_sheds else 0.0,
            'shed_categories': load_sheds if load_sheds else {},
        }

        if forecast_info:
            record['forecast'] = forecast_info

        if decision_reasons:
            record['forecast_decisions'] = decision_reasons

        try:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record) + '\n')
        except Exception as e:
            # Logging failures must NEVER break the EMS
            logger.debug(f"Decision log write failed: {e}")

    def disable(self):
        """Disable logging (for performance-critical runs)."""
        self._enabled = False

    def enable(self):
        """Re-enable logging."""
        self._enabled = True
