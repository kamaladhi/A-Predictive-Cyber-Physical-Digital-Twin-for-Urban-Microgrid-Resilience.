"""
================================================================================
OUTAGE EVENT MODEL & SCENARIO CONFIGURATION
================================================================================

Module Purpose:
    Defines the data structures for representing grid outage events and
    scenario configurations used by the Digital Twin for fault injection and
    resilience testing.

Key Components:
    1. OutageType Enum: Classifies outage scenarios
       - PARTIAL: Specific feeders or microgrids affected
       - FULL_BLACKOUT: Complete city grid failure
       - CASCADING: Sequential multi-node failures  
       - PEAK_LOAD: Forced outage due to demand exceeding supply
    
    2. OutageEvent: Individual outage specification with:
       - Timing (start_time, duration_hours)
       - Scope (affected_microgrids list)
       - Type (outage_type classification)
       - Sequence (cascade_sequence for multi-step failures)
    
    3. ScenarioConfig: Complete scenario specification for Digital Twin
       - scenario_id: Unique identifier
       - outage_events: List of OutageEvent objects
       - simulation_step_seconds: Control sampling rate
       - Metadata: name, description, start_time, duration

Usage Pattern:
    1. Define individual OutageEvent objects
    2. Create ScenarioConfig with list of events
    3. Pass to ScenarioEngine for time-based grid availability queries
    4. Pass to DigitalTwinManager for complete simulation

Example:
    event = OutageEvent(
        event_id='outage_001',
        outage_type=OutageType.FULL_BLACKOUT,
        start_time=datetime(2026, 1, 27, 14, 0),
        duration_hours=4.0,
        affected_microgrids=['hospital', 'university', 'industrial', 'residential'],
        description='City-wide peak load outage during afternoon peak'
    )
    
    config = ScenarioConfig(
        scenario_id='peak_outage_test',
        name='Peak Load Outage Test',
        description='4-hour citywide blackout at 14:00 during peak demand',
        start_time=datetime(2026, 1, 27, 12, 0),
        duration_hours=6.0,
        outage_events=[event]
    )

================================================================================
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
from datetime import datetime, timedelta

class OutageType(Enum):
    PARTIAL = "partial"             # Specific feeders/microgrids affected
    FULL_BLACKOUT = "full_blackout" # Entire city grid down
    CASCADING = "cascading"         # Sequential failure of nodes
    PEAK_LOAD = "peak_load"         # Forced outage due to overload

class OutageSeverity(Enum):
    """Severity levels for outage events."""
    MINOR = 1         # Single feeder, 1-2 MGs, <2h
    MODERATE = 2      # Distribution fault, 2-3 MGs, 2-6h
    MAJOR = 3         # Substation failure, all MGs, 6-24h
    CATASTROPHIC = 4  # Transmission failure, all MGs, 24-72h

@dataclass
class OutageEvent:
    """
    Represents a specific grid outage event.
    """
    event_id: str
    outage_type: OutageType
    start_time: datetime
    duration_hours: float
    affected_microgrids: List[str]  # List of microgrid_ids
    description: str
    
    # Severity classification
    severity: OutageSeverity = OutageSeverity.MODERATE
    
    # For cascading failures
    cascade_sequence: Optional[List[dict]] = None # [{'time_offset': 0.5, 'microgrids': ['industry']}]
    cascade_probability: float = 0.0              # Probability of triggering cascade
    propagation_delay_hours: float = 0.0           # Delay before cascading
    
    @property
    def end_time(self) -> datetime:
        return self.start_time + timedelta(hours=self.duration_hours)

@dataclass
class ScenarioConfig:
    """
    Configuration for a complete digital twin scenario.
    """
    scenario_id: str
    name: str
    description: str
    start_time: datetime
    duration_hours: float
    outage_events: List[OutageEvent]
    simulation_step_seconds: int = 900  # 15 minutes default
