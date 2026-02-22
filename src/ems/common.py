from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

class MicrogridPriority(Enum):
    """City-wide microgrid priority levels"""
    CRITICAL = 1      # Hospital - Life safety, cannot fail
    HIGH = 2          # University - Education, research continuity
    MEDIUM = 3        # Industrial - Economic activity, employment
    LOW = 4           # Residential - Comfort, convenience

class CityOperationMode(Enum):
    """City-wide operation modes"""
    NORMAL = "normal"                          # All microgrids grid-connected
    PARTIAL_OUTAGE = "partial_outage"          # Some microgrids islanded
    WIDESPREAD_OUTAGE = "widespread_outage"    # All microgrids islanded
    EMERGENCY = "emergency"                    # Critical resource shortage
    RECOVERY = "recovery"                      # Transitioning back to normal

class ResiliencePolicy(Enum):
    """Resilience policy modes"""
    BALANCED = "balanced"              # Balance all priorities
    CRITICAL_FIRST = "critical_first"  # Maximize hospital/university survival
    ECONOMIC = "economic"              # Minimize economic impact
    EQUITABLE = "equitable"           # Equal treatment across microgrids

@dataclass
class MicrogridInfo:
    """Information about a registered microgrid"""
    microgrid_id: str
    microgrid_type: str  # "hospital", "university", "industrial", "residential"
    priority: MicrogridPriority
    location: Tuple[float, float]  # (latitude, longitude)
    
    # Capacity information
    critical_load_kw: float
    total_capacity_kw: float
    battery_capacity_kwh: float
    pv_capacity_kwp: float
    generator_capacity_kw: float
    
    # Operational constraints
    min_runtime_hours: float  # Minimum backup runtime required
    max_shed_percent: float   # Maximum allowable load shedding
    can_share_power: bool     # Can participate in power sharing
    
    # Current state
    is_islanded: bool = False
    battery_soc_percent: float = 80.0
    current_load_kw: float = 0.0
    available_capacity_kw: float = 0.0

@dataclass
class MicrogridStatus:
    """Status report from a single microgrid"""
    microgrid_id: str
    timestamp: datetime
    
    # Operation state
    operation_mode: str  # From local EMS
    is_islanded: bool
    grid_available: bool
    
    # Power balance
    total_load_kw: float
    critical_load_kw: float
    pv_generation_kw: float
    battery_power_kw: float  # Positive = discharge
    generator_power_kw: float
    grid_power_kw: float
    
    # Resource state
    battery_soc_percent: float
    battery_capacity_kwh: float
    fuel_remaining_liters: float

    # Shedding state
    load_shed_kw: float
    load_shed_percent: float
    critical_load_shed: bool  # Should ALWAYS be False
    
    # Predicted survivability
    estimated_runtime_hours: float
    resource_criticality: str  # "healthy", "warning", "critical", "emergency"
    
    # Fields with defaults must come last
    generator_capacity_kw: float = 0.0 # Asset capacity
    pv_capacity_kw: float = 0.0        # Asset capacity
    net_sharing_kw: float = 0.0  # Net import (+) or export (-) from inter-MG bus

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with dashboard-compatible field names"""
        d = vars(self).copy()
        # Field mapping for dashboard compatibility
        d['soc'] = self.battery_soc_percent
        d['pv_power_kw'] = self.pv_generation_kw
        
        # Ensure timestamp is ISO format for MQTT/JSON
        if isinstance(d.get('timestamp'), datetime):
            d['timestamp'] = d['timestamp'].isoformat()
            
        return d

@dataclass
class CityWideMeasurements:
    """Aggregated measurements from all microgrids"""
    timestamp: datetime
    microgrid_statuses: Dict[str, MicrogridStatus]
    
    # City-wide aggregates
    total_load_kw: float
    total_critical_load_kw: float
    total_generation_kw: float
    total_battery_energy_kwh: float
    total_fuel_liters: float
    
    # Outage information
    grid_outage_active: bool
    outage_start_time: Optional[datetime]
    outage_duration_hours: float
    
    # City-level health
    microgrids_islanded: int
    microgrids_in_emergency: int
    city_survivability_hours: float

@dataclass
class SupervisoryCommand:
    """Supervisory command to a single microgrid's local EMS"""
    microgrid_id: str
    timestamp: datetime
    
    # Load shedding override
    target_shed_percent: Optional[float] = None
    max_shed_limit_percent: Optional[float] = None
    
    # Battery dispatch guidance
    battery_soc_target_percent: Optional[float] = None
    battery_reserve_percent: Optional[float] = None
    
    # Generator dispatch guidance
    generator_enable: Optional[bool] = None
    generator_priority: Optional[int] = None
    
    # Power sharing (future capability)
    export_power_kw: Optional[float] = None
    import_power_kw: Optional[float] = None
    
    # Priority information
    emergency_mode: bool = False
    critical_only_mode: bool = False
    city_priority_level: int = 4
    
    # Demand Response integration
    dr_requested_reduction_kw: Optional[float] = None
    
    # Logic trace
    reason: str = "Standard coordination"
    
    # Explicit MPC Dispatch (for verification and high-fidelity control)
    mpc_gen_kw: Optional[float] = None
    mpc_batt_kw: Optional[float] = None      # positive = discharge, negative = charge
    mpc_shed_kw: Optional[float] = None
    mpc_dr_kw: Optional[float] = None
    mpc_export_kw: Optional[float] = None
    mpc_import_kw: Optional[float] = None

@dataclass
class CityControlOutputs:
    """Complete city-level control outputs"""
    timestamp: datetime
    city_mode: CityOperationMode
    active_policy: ResiliencePolicy
    
    # Supervisory commands to each microgrid
    supervisory_commands: Dict[str, SupervisoryCommand] = field(default_factory=dict)
    
    # City-level decisions
    load_shedding_allocation: Dict[str, float] = field(default_factory=dict)  # microgrid_id -> shed_percent
    resource_prioritization: List[str] = field(default_factory=list)  # Ordered list of microgrid_ids
    
    # DR commands (if DR events active)
    dr_commands: List = field(default_factory=list)  # List[DRCommand] if DR_AVAILABLE
    
    # Resource sharing transfers (if sharing active)
    resource_transfers: List = field(default_factory=list)  # List[TransferAllocation]
    
    # Alerts and information
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
