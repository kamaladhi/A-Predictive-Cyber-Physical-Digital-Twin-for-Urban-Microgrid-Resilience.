"""
Local EMS Manager
Handles per-microgrid EMS command generation using IEMSOrchestrator.
"""
from typing import Dict, List
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from EMS.iems_orchestrator import IEMSOrchestrator, IEMSCommand
from DigitalTwin.data_fusion import FusedMicrogridState
from DigitalTwin.dr_alerts import DRAlert


class LocalEMSManager:
    """Manage IEMS orchestrators for multiple microgrids."""

    def __init__(self, orchestrator_overrides: Dict[str, Dict] = None):
        # Allow per-microgrid parameter overrides (e.g., SOC floors, shed fraction)
        self.orchestrators: Dict[str, IEMSOrchestrator] = {}
        self.overrides = orchestrator_overrides or {}

    def get_orchestrator(self, microgrid_id: str) -> IEMSOrchestrator:
        if microgrid_id not in self.orchestrators:
            params = self.overrides.get(microgrid_id, {})
            self.orchestrators[microgrid_id] = IEMSOrchestrator(**params)
        return self.orchestrators[microgrid_id]

    def generate_commands(self,
                          microgrid_id: str,
                          fused_state: FusedMicrogridState,
                          alerts: List[DRAlert]) -> List[IEMSCommand]:
        orchestrator = self.get_orchestrator(microgrid_id)
        return orchestrator.orchestrate(fused_state, alerts)
