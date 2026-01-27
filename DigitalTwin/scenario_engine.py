from typing import List, Optional
from datetime import datetime, timedelta
import logging
from DigitalTwin.outage_event_model import ScenarioConfig, OutageEvent, OutageType

logger = logging.getLogger(__name__)

class ScenarioEngine:
    """
    Drives the System-Level Digital Twin simulations by injecting 
    outage events and modifying environmental parameters.
    """
    
    def __init__(self, config: ScenarioConfig):
        self.config = config
        self.active_events: List[OutageEvent] = []
        
    def get_grid_availability(self, current_time: datetime, microgrid_id: str) -> bool:
        """
        Determines if the grid is available for a specific microgrid at the current time.
        Considers all active outage scenarios.
        """
        is_available = True
        
        # Check against all defined events in the scenario
        for event in self.config.outage_events:
            # Check time overlap
            event_end_time = event.start_time + timedelta(hours=event.duration_hours)
            
            if event.start_time <= current_time < event_end_time:
                # Check if this microgrid is affected
                if microgrid_id in event.affected_microgrids:
                    is_available = False
                    break # One outage is enough to cut power
                
        return is_available

    def get_active_outage_type(self, current_time: datetime) -> Optional[OutageType]:
        """
        Returns the type of the dominant active outage, if any.
        """
        for event in self.config.outage_events:
            event_end_time = event.start_time + timedelta(hours=event.duration_hours)
            if event.start_time <= current_time < event_end_time:
                return event.outage_type
        return None

    def predict_impact(self, current_state, lookahead_hours: float = 24.0):
        """
        Digital Twin Capability: Predict capability based on current scenario.
        (Placeholder for the predictive logic required by B. Digital Twin Core)
        """
        # This would interface with the DigitalTwinManager's shadow simulation
        pass
