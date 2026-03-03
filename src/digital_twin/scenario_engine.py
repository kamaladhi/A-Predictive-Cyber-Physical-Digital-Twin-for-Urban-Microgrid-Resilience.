from typing import List, Optional, Set
from datetime import datetime, timedelta
import logging
from src.digital_twin.outage_event_model import ScenarioConfig, OutageEvent, OutageType

logger = logging.getLogger(__name__)

class ScenarioEngine:
    """
    Drives the System-Level Digital Twin simulations by injecting 
    outage events and modifying environmental parameters.
    
    Enhanced with:
    - Dynamic event injection (for cascading failures)
    - cascade_sequence implementation
    - Active event tracking for CityEMS integration
    """
    
    def __init__(self, config: ScenarioConfig):
        self.config = config
        self.active_events: List[OutageEvent] = []
        self._injected_events: List[OutageEvent] = []  # Dynamically added
        self._cascade_processed: Set[str] = set()      # Prevent duplicates
        
    def get_grid_availability(self, current_time: datetime, microgrid_id: str) -> bool:
        """
        Determines if the grid is available for a specific microgrid at the current time.
        Considers all defined events AND dynamically injected cascade events.
        """
        all_events = list(self.config.outage_events) + self._injected_events
        
        for event in all_events:
            event_end_time = event.start_time + timedelta(hours=event.duration_hours)
            
            if event.start_time <= current_time < event_end_time:
                # Direct hit
                if microgrid_id in event.affected_microgrids:
                    return False
                
                # Check cascade_sequence for time-offset cascading
                if event.cascade_sequence and event.event_id not in self._cascade_processed:
                    for cascade_step in event.cascade_sequence:
                        offset = cascade_step.get('time_offset', 0)
                        cascade_time = event.start_time + timedelta(hours=offset)
                        cascade_mgs = cascade_step.get('microgrids', [])
                        
                        if cascade_time <= current_time and microgrid_id in cascade_mgs:
                            return False
                    
        return True

    def get_active_outage_type(self, current_time: datetime) -> Optional[OutageType]:
        """
        Returns the type of the dominant active outage, if any.
        Priority: CASCADING > FULL_BLACKOUT > PEAK_LOAD > PARTIAL
        """
        all_events = list(self.config.outage_events) + self._injected_events
        active_types = []
        
        for event in all_events:
            event_end_time = event.start_time + timedelta(hours=event.duration_hours)
            if event.start_time <= current_time < event_end_time:
                active_types.append(event.outage_type)
        
        if not active_types:
            return None
        
        # Return most severe type
        priority_order = [
            OutageType.CASCADING, OutageType.FULL_BLACKOUT,
            OutageType.PEAK_LOAD, OutageType.PARTIAL
        ]
        for otype in priority_order:
            if otype in active_types:
                return otype
        return active_types[0]
    
    def get_faulted_microgrids(self, current_time: datetime) -> Set[str]:
        """
        Returns set of microgrid IDs currently under outage.
        Used by CascadeController to know which MGs are faulted.
        """
        faulted = set()
        all_events = list(self.config.outage_events) + self._injected_events
        
        for event in all_events:
            event_end_time = event.start_time + timedelta(hours=event.duration_hours)
            if event.start_time <= current_time < event_end_time:
                faulted.update(event.affected_microgrids)
        return faulted
    
    def inject_event(self, event: OutageEvent):
        """
        Dynamically inject a new outage event (e.g., from CascadeController).
        """
        self._injected_events.append(event)
        logger.warning(
            f"INJECTED outage event: {event.event_id} "
            f"affecting {event.affected_microgrids} for {event.duration_hours}h"
        )
    
    def get_active_events(self, current_time: datetime) -> List[OutageEvent]:
        """Return all currently active outage events."""
        all_events = list(self.config.outage_events) + self._injected_events
        active = []
        for event in all_events:
            event_end_time = event.start_time + timedelta(hours=event.duration_hours)
            if event.start_time <= current_time < event_end_time:
                active.append(event)
        return active

    def predict_impact(self, current_state, lookahead_hours: float = 24.0):
        """
        Digital Twin Capability: Predict capability based on current scenario.
        (Placeholder for the predictive logic required by B. Digital Twin Core)
        """
        # This would interface with the DigitalTwinManager's shadow simulation
        pass

