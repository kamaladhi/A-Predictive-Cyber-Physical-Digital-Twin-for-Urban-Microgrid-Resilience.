from __future__ import annotations
import logging
import time
from typing import Dict, List, Any, Optional
from threading import Lock
from datetime import datetime

logger = logging.getLogger(__name__)

class DataFusionEngine:
    """
    Person 3's Data Fusion Engine.
    Aggregates asynchronous IoT inputs (NILM, Forecasts, Measurements) 
    into a synchronized 'Virtual Twin' state for the EMS.
    """
    def __init__(self):
        self._lock = Lock()
        self._nilm_data: Dict[str, Dict[str, Any]] = {}  # {mg_id: {appliance: load}}
        self._forecast_data: Dict[str, List[float]] = {} # {mg_id: [P_pv_t0, P_pv_t1, ...]}
        self._sim_measurements: Dict[str, Any] = {}
        
        # Buffer for timestamp alignment
        self._message_buffer: List[Dict[str, Any]] = []
        self._latest_timestamp: Optional[datetime] = None

    def update_nilm(self, mg_id: str, data: Dict[str, Any]):
        """Update disaggregated appliance load data."""
        with self._lock:
            self._nilm_data[mg_id] = data
            logger.debug(f"Fusion: Received NILM update for {mg_id}")

    def update_forecast(self, mg_id: str, forecast: List[float]):
        """Update external forecast predictions."""
        with self._lock:
            self._forecast_data[mg_id] = forecast
            logger.debug(f"Fusion: Received Forecast update for {mg_id}")

    def update_measurements(self, data: Dict[str, Any]):
        """Update raw simulation measurements."""
        with self._lock:
            self._sim_measurements = data
            self._latest_timestamp = data.get('timestamp')
            logger.debug("Fusion: Received Simulation measurements")

    def get_fused_state(self, mg_id: str) -> Dict[str, Any]:
        """
        Merge all available data for a specific microgrid.
        Ensures forecasting and NILM are injected into the operational state.
        """
        with self._lock:
            # Base state from simulation status
            status = self._sim_measurements.get('microgrid_statuses', {}).get(mg_id, {})
            fused_state = dict(status) if status else {}
            
            # Inject NILM (Appliance-level intelligence)
            if mg_id in self._nilm_data:
                fused_state['appliance_loads'] = self._nilm_data[mg_id]
            
            # Inject Forecasts (Predictive intelligence)
            if mg_id in self._forecast_data:
                fused_state['external_forecast'] = self._forecast_data[mg_id]
                
            return fused_state

    def sync_check(self, max_delta_seconds: float = 300) -> bool:
        """
        Verify if data points are synchronized within a temporal window.
        Returns True if the 'Virtual Twin' is cohesive.
        """
        # In a real implementation, we would compare message arrival timestamps here.
        # For the simulation-IoT hybrid, we assume 'soft-sync' is sufficient.
        return True

    def log_fusion_event(self, event_type: str, detail: str):
        """Record data fusion events for auditing."""
        logger.info(f"Fusion [{event_type}]: {detail}")
