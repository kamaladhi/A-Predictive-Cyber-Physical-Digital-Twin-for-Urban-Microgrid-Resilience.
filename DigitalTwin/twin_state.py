from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
from EMS.city_ems import MicrogridStatus, CityWideMeasurements, CityControlOutputs

@dataclass
class PhysicalState:
    """
    Mirrors the physical status of the microgrids.
    Aggregated from simulator outputs.
    """
    timestamp: datetime
    microgrid_states: Dict[str, MicrogridStatus]
    total_active_load_kw: float
    total_generation_kw: float
    total_battery_energy_kwh: float
    grid_connection_status: Dict[str, bool] # Real physical status

@dataclass
class CyberState:
    """
    Mirrors the control signals and EMS status.
    """
    city_ems_outputs: CityControlOutputs
    local_ems_decisions: Dict[str, dict] # Snapshot of local EMS decisions
    communication_health: Dict[str, bool] # Simulating comms availability (Assumption: Ideal for now)

@dataclass
class ResilienceState:
    """
    Real-time resilience metrics and health indicators.
    """
    city_survivability_index: float  # 0.0 to 1.0 (or hours)
    critical_load_at_risk_kw: float
    unserved_energy_kwh: float
    priority_violation_count: int
    current_survivability_horizon_hours: float

@dataclass
class TwinState:
    """
    The COMPLETE System-Level Digital Twin State.
    Wraps Physical, Cyber, and Resilience states.
    """
    timestamp: datetime
    sim_step: int
    
    physical: PhysicalState
    cyber: CyberState
    resilience: ResilienceState
    
    # Context
    is_outage_active: bool
    active_outage_id: Optional[str] = None
    
    # History (Optional: could be kept elsewhere to save memory)
    # kept light here.
