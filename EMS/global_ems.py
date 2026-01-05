"""
Global EMS Coordinator
Coordinates multiple microgrids, runs local EMS for each, and proposes energy sharing.
"""
from dataclasses import dataclass
from typing import Dict, List, Tuple
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from DigitalTwin.data_fusion import FusedMicrogridState
from DigitalTwin.dr_alerts import DRAlert
from EMS.iems_orchestrator import IEMSCommand
from EMS.local_ems_manager import LocalEMSManager


@dataclass
class TransferProposal:
    """Represents a proposed power transfer between microgrids."""
    from_microgrid: str
    to_microgrid: str
    power_kw: float
    reason: str


class GlobalEMSCoordinator:
    """
    Global EMS that:
    1) Runs local EMS commands for each microgrid via LocalEMSManager
    2) Identifies surplus/deficit microgrids and proposes transfers
    """

    def __init__(self,
                 local_manager: LocalEMSManager,
                 transfer_threshold_kw: float = 50.0,
                 max_transfer_kw: float = 500.0):
        self.local_manager = local_manager
        self.transfer_threshold_kw = transfer_threshold_kw
        self.max_transfer_kw = max_transfer_kw

    def coordinate(self,
                   fused_states: Dict[str, FusedMicrogridState],
                   alerts_map: Dict[str, List[DRAlert]]) -> Tuple[Dict[str, List[IEMSCommand]], List[TransferProposal]]:
        # 1) Run local EMS for each microgrid
        commands_by_mg: Dict[str, List[IEMSCommand]] = {}
        for mg_id, state in fused_states.items():
            mg_alerts = alerts_map.get(mg_id, [])
            commands_by_mg[mg_id] = self.local_manager.generate_commands(mg_id, state, mg_alerts)

        # 2) Compute surpluses/deficits for sharing
        surpluses = []  # (mg_id, surplus_kw)
        deficits = []   # (mg_id, deficit_kw)
        for mg_id, state in fused_states.items():
            net_balance = state.measured_pv_kw + state.measured_battery_kw - state.measured_load_kw
            if net_balance > self.transfer_threshold_kw:
                surpluses.append((mg_id, net_balance))
            elif net_balance < -self.transfer_threshold_kw:
                deficits.append((mg_id, abs(net_balance)))

        # Sort: largest surplus first, largest deficit first
        surpluses.sort(key=lambda x: x[1], reverse=True)
        deficits.sort(key=lambda x: x[1], reverse=True)

        transfers: List[TransferProposal] = []
        si = 0
        di = 0
        while si < len(surpluses) and di < len(deficits):
            s_id, s_val = surpluses[si]
            d_id, d_val = deficits[di]

            transfer_kw = min(s_val, d_val, self.max_transfer_kw)
            if transfer_kw <= 0:
                break

            transfers.append(TransferProposal(
                from_microgrid=s_id,
                to_microgrid=d_id,
                power_kw=transfer_kw,
                reason="Balance deficits using surplus"
            ))

            s_val -= transfer_kw
            d_val -= transfer_kw
            surpluses[si] = (s_id, s_val)
            deficits[di] = (d_id, d_val)

            if s_val <= self.transfer_threshold_kw:
                si += 1
            if d_val <= self.transfer_threshold_kw:
                di += 1

        return commands_by_mg, transfers
