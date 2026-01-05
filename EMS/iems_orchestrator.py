"""
Integrated EMS (IEMS) Orchestrator
Maps fused state + DR alerts to actionable microgrid EMS commands.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from DigitalTwin.dr_alerts import AlertType, DRAlert
from DigitalTwin.data_fusion import FusedMicrogridState

logger = logging.getLogger(__name__)


class CommandType(Enum):
    """Types of EMS commands that can be issued."""
    BATTERY_SETPOINT = "battery_setpoint_kw"  # Positive = discharge, Negative = charge
    SHED_NON_CRITICAL = "shed_non_critical_kw"
    START_GENERATOR = "start_generator"
    STOP_GENERATOR = "stop_generator"
    GRID_IMPORT_CAP = "grid_import_cap_kw"


@dataclass
class IEMSCommand:
    """Command to be executed by a microgrid EMS."""
    command_id: str
    timestamp: datetime
    microgrid_id: str
    command_type: CommandType
    value_kw: Optional[float] = None  # For setpoints, shedding, caps
    reason: str = ""
    alert_ids: List[str] = field(default_factory=list)
    notes: Optional[str] = None


class IEMSOrchestrator:
    """
    Simple rule-based IEMS orchestrator that translates DR alerts and fused state
    into dispatch, shedding, and generator actions.
    """

    def __init__(self,
                 soc_floor_percent: float = 25.0,
                 soc_critical_percent: float = 15.0,
                 max_discharge_kw: float = 200.0,
                 max_charge_kw: float = 150.0,
                 shed_fraction: float = 0.4,
                 grid_import_cap_kw: Optional[float] = None):
        self.soc_floor_percent = soc_floor_percent
        self.soc_critical_percent = soc_critical_percent
        self.max_discharge_kw = max_discharge_kw
        self.max_charge_kw = max_charge_kw
        self.shed_fraction = shed_fraction
        self.grid_import_cap_kw = grid_import_cap_kw
        logger.info("✓ IEMS Orchestrator initialized")

    def orchestrate(self, fused_state: FusedMicrogridState, alerts: List[DRAlert]) -> List[IEMSCommand]:
        """
        Generate EMS commands based on fused state and active alerts.
        """
        commands: List[IEMSCommand] = []
        alert_ids = [a.alert_id for a in alerts]
        alert_types = {a.alert_type for a in alerts}

        load_kw = fused_state.measured_load_kw
        pv_kw = fused_state.measured_pv_kw
        soc = fused_state.measured_soc_percent
        non_critical_kw = fused_state.non_critical_load_kw

        # Net import estimation (positive = importing from grid)
        net_import_kw = max(0.0, load_kw - pv_kw)

        # Rule 1: Peak or high-cost → discharge battery within SOC guardrails
        if ({AlertType.HIGH_COST, AlertType.PEAK_PREDICTED} & alert_types) and soc > self.soc_floor_percent:
            discharge_kw = min(net_import_kw, self.max_discharge_kw)
            if discharge_kw > 0:
                commands.append(self._make_command(
                    fused_state.microgrid_id,
                    CommandType.BATTERY_SETPOINT,
                    discharge_kw,
                    "High cost/peak mitigation",
                    alert_ids,
                    notes=f"SOC {soc:.1f}%, net import {net_import_kw:.1f} kW"
                ))

        # Rule 2: Resilience degraded or battery low → shed non-critical load + consider generator
        if ({AlertType.RESILIENCE_DEGRADED, AlertType.BATTERY_LOW} & alert_types):
            shed_kw = min(non_critical_kw * self.shed_fraction, non_critical_kw)
            if shed_kw > 0:
                commands.append(self._make_command(
                    fused_state.microgrid_id,
                    CommandType.SHED_NON_CRITICAL,
                    shed_kw,
                    "Protect runtime / critical loads",
                    alert_ids,
                    notes=f"Non-critical available {non_critical_kw:.1f} kW"
                ))
            if soc <= self.soc_critical_percent:
                commands.append(self._make_command(
                    fused_state.microgrid_id,
                    CommandType.START_GENERATOR,
                    None,
                    "Critical SOC, start backup",
                    alert_ids,
                    notes=f"SOC {soc:.1f}%"
                ))

        # Rule 3: Off-peak / opportunity → charge battery if low
        if AlertType.COST_OPPORTUNITY in alert_types and soc < 80.0:
            charge_kw = min(self.max_charge_kw, max(0.0, net_import_kw))
            # Negative value to indicate charging setpoint
            commands.append(self._make_command(
                fused_state.microgrid_id,
                CommandType.BATTERY_SETPOINT,
                -charge_kw if charge_kw > 0 else -self.max_charge_kw / 2,
                "Charge during low cost window",
                alert_ids,
                notes=f"SOC {soc:.1f}%"
            ))

        # Rule 4: Optional grid import cap if configured and peak predicted
        if self.grid_import_cap_kw and AlertType.PEAK_PREDICTED in alert_types:
            commands.append(self._make_command(
                fused_state.microgrid_id,
                CommandType.GRID_IMPORT_CAP,
                self.grid_import_cap_kw,
                "Cap grid import during predicted peak",
                alert_ids,
                notes=f"Cap set to {self.grid_import_cap_kw} kW"
            ))

        return commands

    def _make_command(self, microgrid_id: str, cmd_type: CommandType, value_kw: Optional[float],
                      reason: str, alert_ids: List[str], notes: Optional[str] = None) -> IEMSCommand:
        return IEMSCommand(
            command_id=f"CMD_{microgrid_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            timestamp=datetime.now(),
            microgrid_id=microgrid_id,
            command_type=cmd_type,
            value_kw=value_kw,
            reason=reason,
            alert_ids=alert_ids,
            notes=notes
        )
