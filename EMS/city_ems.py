"""
City-Level Centralized Energy Management System (City-EMS)

This module implements a SUPERVISORY EMS that coordinates MULTIPLE heterogeneous microgrids
to enforce priority-aware resilience policies and improve city-level survivability.

Key Responsibilities:
- Coordinate 4 heterogeneous microgrids (Hospital, University, Industrial, Residential)
- Enforce priority-aware load shedding across city
- Manage inter-microgrid power sharing (if connected)
- Optimize city-wide resource allocation during outages
- Monitor and maintain critical infrastructure resilience
- Provide supervisory control signals to local EMSs

Architecture:
- City-EMS receives state from all local EMSs
- City-EMS sends supervisory commands to local EMSs
- City-EMS does NOT bypass local EMS safety logic
- City-EMS optimizes city-level objectives while respecting local constraints

Digital Twin Framework:
- Real-time coordination of heterogeneous microgrids
- Priority-based resource allocation
- Demonstrable improvement in city-level survivability
- Policy-driven resilience enforcement
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Import DR coordinator (if available)
try:
    from demand_response import (
        DemandResponseCoordinator, DREvent, DRCommand,
        DREventType, DREventPriority
    )
    DR_AVAILABLE = True
except ImportError:
    DR_AVAILABLE = False
    logger.warning("Demand Response module not available")


# =============================================================================
# CITY-LEVEL PRIORITY SYSTEM
# =============================================================================

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


# =============================================================================
# MICROGRID REGISTRY
# =============================================================================

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


# =============================================================================
# CITY-LEVEL MEASUREMENTS
# =============================================================================

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


# =============================================================================
# CITY-LEVEL CONTROL COMMANDS
# =============================================================================

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
    
    # Emergency overrides
    emergency_mode: bool = False
    critical_only_mode: bool = False
    
    # Informational
    city_priority_level: Optional[int] = None
    reason: str = ""


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
    
    # Alerts and information
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# CITY-LEVEL EMS STATE
# =============================================================================

@dataclass
class CityEMSState:
    """Persistent state for city-level EMS"""
    city_mode: CityOperationMode = CityOperationMode.NORMAL
    mode_entry_time: Optional[datetime] = None
    active_policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST
    
    # Outage tracking
    outage_detected: bool = False
    outage_start_time: Optional[datetime] = None
    
    # Resource tracking
    total_city_battery_kwh: float = 0.0
    total_city_fuel_liters: float = 0.0
    
    # Priority-based allocation history
    last_allocation_time: Optional[datetime] = None
    allocation_history: List[Dict[str, float]] = field(default_factory=list)
    
    def reset_mode(self, new_mode: CityOperationMode, timestamp: datetime):
        """Reset city mode and entry time"""
        self.city_mode = new_mode
        self.mode_entry_time = timestamp


# =============================================================================
# CITY-LEVEL CENTRALIZED EMS
# =============================================================================

class CityEMS:
    """
    City-Level Centralized Energy Management System
    
    Coordinates multiple heterogeneous microgrids to enforce priority-aware
    resilience policies and improve city-level survivability during grid outages.
    
    Key Features:
    - Priority-aware load shedding allocation
    - Resource optimization across microgrids
    - Critical infrastructure protection
    - City-wide survivability maximization
    - Policy-driven coordination
    """
    
    def __init__(self, resilience_policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST):
        """
        Initialize City-Level EMS
        
        Args:
            resilience_policy: Default resilience policy to enforce
        """
        self.state = CityEMSState(active_policy=resilience_policy)
        self.microgrids: Dict[str, MicrogridInfo] = {}
        
        # Initialize Demand Response Coordinator
        if DR_AVAILABLE:
            self.dr_coordinator = DemandResponseCoordinator()
            logger.info("Demand Response Coordinator enabled")
        else:
            self.dr_coordinator = None
            logger.info("Demand Response Coordinator not available")
        
        # Priority mapping
        self.priority_map = {
            "hospital": MicrogridPriority.CRITICAL,
            "university": MicrogridPriority.HIGH,
            "industrial": MicrogridPriority.MEDIUM,
            "residential": MicrogridPriority.LOW
        }
        
        # Policy-specific parameters
        self.policy_parameters = {
            ResiliencePolicy.CRITICAL_FIRST: {
                "priority_weights": {
                    MicrogridPriority.CRITICAL: 1.0,
                    MicrogridPriority.HIGH: 0.7,
                    MicrogridPriority.MEDIUM: 0.4,
                    MicrogridPriority.LOW: 0.2
                },
                "shed_order": [
                    MicrogridPriority.LOW,
                    MicrogridPriority.MEDIUM,
                    MicrogridPriority.HIGH,
                    MicrogridPriority.CRITICAL
                ]
            },
            ResiliencePolicy.BALANCED: {
                "priority_weights": {
                    MicrogridPriority.CRITICAL: 1.0,
                    MicrogridPriority.HIGH: 0.85,
                    MicrogridPriority.MEDIUM: 0.70,
                    MicrogridPriority.LOW: 0.55
                },
                "shed_order": [
                    MicrogridPriority.LOW,
                    MicrogridPriority.MEDIUM,
                    MicrogridPriority.HIGH,
                    MicrogridPriority.CRITICAL
                ]
            },
            ResiliencePolicy.EQUITABLE: {
                "priority_weights": {
                    MicrogridPriority.CRITICAL: 1.0,
                    MicrogridPriority.HIGH: 1.0,
                    MicrogridPriority.MEDIUM: 1.0,
                    MicrogridPriority.LOW: 1.0
                },
                "shed_order": [
                    MicrogridPriority.LOW,
                    MicrogridPriority.MEDIUM,
                    MicrogridPriority.HIGH,
                    MicrogridPriority.CRITICAL
                ]
            },
            ResiliencePolicy.ECONOMIC: {
                "priority_weights": {
                    MicrogridPriority.CRITICAL: 1.0,
                    MicrogridPriority.MEDIUM: 0.9,  # Industrial prioritized
                    MicrogridPriority.HIGH: 0.7,
                    MicrogridPriority.LOW: 0.3
                },
                "shed_order": [
                    MicrogridPriority.LOW,
                    MicrogridPriority.HIGH,
                    MicrogridPriority.MEDIUM,
                    MicrogridPriority.CRITICAL
                ]
            }
        }
        
        logger.info(f"City-Level EMS initialized with policy: {resilience_policy.value}")
    
    # =========================================================================
    # MICROGRID REGISTRATION
    # =========================================================================
    
    def register_microgrid(self, microgrid_info: MicrogridInfo):
        """
        Register a microgrid with the city-level EMS
        
        Args:
            microgrid_info: Information about the microgrid to register
        """
        self.microgrids[microgrid_info.microgrid_id] = microgrid_info
        logger.info(f"Registered microgrid: {microgrid_info.microgrid_id} "
                   f"(Type: {microgrid_info.microgrid_type}, "
                   f"Priority: {microgrid_info.priority.name})")
    
    # =========================================================================
    # MAIN UPDATE LOOP
    # =========================================================================
    
    def update(self, measurements: CityWideMeasurements) -> CityControlOutputs:
        """
        Main city-level EMS update - called every timestep
        
        Args:
            measurements: Aggregated measurements from all microgrids
            
        Returns:
            CityControlOutputs with supervisory commands for all microgrids
        """
        # Update internal state
        self._update_city_state(measurements)
        
        # Initialize outputs
        outputs = CityControlOutputs(
            timestamp=measurements.timestamp,
            city_mode=self.state.city_mode,
            active_policy=self.state.active_policy,
            supervisory_commands={},
            load_shedding_allocation={},
            resource_prioritization=[]
        )
        
        # Update DR events (if DR coordinator available)
        if self.dr_coordinator:
            dr_commands = self.dr_coordinator.update_dr_events(
                measurements.timestamp,
                measurements.microgrid_statuses
            )
            outputs.dr_commands = dr_commands
            if dr_commands:
                outputs.info.append(f"Active DR events: {len(dr_commands)} commands issued")
        
        # Run city-level state machine
        outputs = self._run_city_state_machine(measurements, outputs)
        
        # Execute mode-specific coordination
        if self.state.city_mode == CityOperationMode.NORMAL:
            outputs = self._coordinate_normal(measurements, outputs)
            
        elif self.state.city_mode == CityOperationMode.PARTIAL_OUTAGE:
            outputs = self._coordinate_partial_outage(measurements, outputs)
            
        elif self.state.city_mode == CityOperationMode.WIDESPREAD_OUTAGE:
            outputs = self._coordinate_widespread_outage(measurements, outputs)
            
        elif self.state.city_mode == CityOperationMode.EMERGENCY:
            outputs = self._coordinate_emergency(measurements, outputs)
            
        elif self.state.city_mode == CityOperationMode.RECOVERY:
            outputs = self._coordinate_recovery(measurements, outputs)
        
        # Calculate city-level metrics
        outputs.metrics = self._calculate_city_metrics(measurements, outputs)
        
        return outputs
    
    # =========================================================================
    # CITY-LEVEL STATE MACHINE
    # =========================================================================
    
    def _run_city_state_machine(self, meas: CityWideMeasurements,
                               outputs: CityControlOutputs) -> CityControlOutputs:
        """
        City-level operation mode state machine
        
        Transitions:
        NORMAL -> PARTIAL_OUTAGE (some microgrids islanded)
        PARTIAL_OUTAGE -> WIDESPREAD_OUTAGE (all microgrids islanded)
        WIDESPREAD_OUTAGE -> EMERGENCY (critical resource shortage)
        Any -> RECOVERY (grid restoration begins)
        RECOVERY -> NORMAL (all back to normal)
        """
        current_mode = self.state.city_mode
        
        # Check for outage conditions
        if current_mode == CityOperationMode.NORMAL:
            if meas.microgrids_islanded > 0:
                if meas.microgrids_islanded == len(meas.microgrid_statuses):
                    # All microgrids islanded
                    outputs.info.append("Widespread outage detected - all microgrids islanded")
                    self.state.reset_mode(CityOperationMode.WIDESPREAD_OUTAGE, meas.timestamp)
                    self.state.outage_start_time = meas.timestamp
                    outputs.city_mode = CityOperationMode.WIDESPREAD_OUTAGE
                else:
                    # Partial outage
                    outputs.info.append(f"Partial outage - {meas.microgrids_islanded} microgrids islanded")
                    self.state.reset_mode(CityOperationMode.PARTIAL_OUTAGE, meas.timestamp)
                    outputs.city_mode = CityOperationMode.PARTIAL_OUTAGE
        
        # Check for emergency conditions
        elif current_mode in [CityOperationMode.PARTIAL_OUTAGE, CityOperationMode.WIDESPREAD_OUTAGE]:
            if meas.microgrids_in_emergency > 0:
                outputs.warnings.append(f"EMERGENCY: {meas.microgrids_in_emergency} microgrids critical")
                self.state.reset_mode(CityOperationMode.EMERGENCY, meas.timestamp)
                outputs.city_mode = CityOperationMode.EMERGENCY
            
            # Check for recovery
            elif meas.microgrids_islanded < len(meas.microgrid_statuses):
                if current_mode == CityOperationMode.WIDESPREAD_OUTAGE:
                    outputs.info.append("Recovery initiated - some microgrids reconnecting")
                    self.state.reset_mode(CityOperationMode.RECOVERY, meas.timestamp)
                    outputs.city_mode = CityOperationMode.RECOVERY
        
        # Emergency mode
        elif current_mode == CityOperationMode.EMERGENCY:
            if meas.microgrids_in_emergency == 0:
                # Return to outage coordination
                if meas.microgrids_islanded == len(meas.microgrid_statuses):
                    self.state.reset_mode(CityOperationMode.WIDESPREAD_OUTAGE, meas.timestamp)
                    outputs.city_mode = CityOperationMode.WIDESPREAD_OUTAGE
                else:
                    self.state.reset_mode(CityOperationMode.PARTIAL_OUTAGE, meas.timestamp)
                    outputs.city_mode = CityOperationMode.PARTIAL_OUTAGE
        
        # Recovery mode
        elif current_mode == CityOperationMode.RECOVERY:
            if meas.microgrids_islanded == 0:
                outputs.info.append("Recovery complete - all microgrids grid-connected")
                self.state.reset_mode(CityOperationMode.NORMAL, meas.timestamp)
                outputs.city_mode = CityOperationMode.NORMAL
                self.state.outage_start_time = None
        
        return outputs
    
    # =========================================================================
    # COORDINATION: NORMAL MODE
    # =========================================================================
    
    def _coordinate_normal(self, meas: CityWideMeasurements,
                          outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordination for normal grid-connected operation
        
        Objectives:
        - Optimize city-wide energy costs
        - Prepare for potential outages
        - Balance battery SoC across microgrids
        - Monitor resource availability
        """
        for mg_id, status in meas.microgrid_statuses.items():
            cmd = SupervisoryCommand(
                microgrid_id=mg_id,
                timestamp=meas.timestamp,
                reason="Normal operation - maintain readiness"
            )
            
            # Ensure batteries charged for backup readiness
            mg_info = self.microgrids.get(mg_id)
            if mg_info:
                if mg_info.priority in [MicrogridPriority.CRITICAL, MicrogridPriority.HIGH]:
                    # Critical microgrids: maintain high SoC
                    cmd.battery_soc_target_percent = 85.0
                else:
                    # Lower priority: can use battery for cost savings
                    cmd.battery_soc_target_percent = 70.0
            
            outputs.supervisory_commands[mg_id] = cmd
        
        outputs.info.append("Normal operation - all microgrids grid-connected")
        return outputs
    
    # =========================================================================
    # COORDINATION: PARTIAL OUTAGE
    # =========================================================================
    
    def _coordinate_partial_outage(self, meas: CityWideMeasurements,
                                   outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordination for partial outage (some microgrids islanded)
        
        Objectives:
        - Support islanded microgrids
        - Prepare grid-connected microgrids for potential islanding
        - Allocate resources based on priority
        """
        # Identify islanded vs connected microgrids
        islanded_mgs = [mg_id for mg_id, status in meas.microgrid_statuses.items() 
                       if status.is_islanded]
        connected_mgs = [mg_id for mg_id, status in meas.microgrid_statuses.items() 
                        if not status.is_islanded]
        
        outputs.info.append(f"Partial outage: {len(islanded_mgs)} islanded, "
                          f"{len(connected_mgs)} connected")
        
        # For islanded microgrids: apply priority-based support
        for mg_id in islanded_mgs:
            outputs = self._apply_priority_coordination(mg_id, meas, outputs)
        
        # For connected microgrids: prepare for potential islanding
        for mg_id in connected_mgs:
            cmd = SupervisoryCommand(
                microgrid_id=mg_id,
                timestamp=meas.timestamp,
                battery_soc_target_percent=90.0,  # Charge up for potential outage
                reason="Prepare for potential islanding"
            )
            outputs.supervisory_commands[mg_id] = cmd
        
        return outputs
    
    # =========================================================================
    # COORDINATION: WIDESPREAD OUTAGE
    # =========================================================================
    
    def _coordinate_widespread_outage(self, meas: CityWideMeasurements,
                                     outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordination for widespread outage (all microgrids islanded)
        
        This is the PRIMARY RESILIENCE MODE where priority-aware policies
        are most critical for city-level survivability.
        
        Objectives:
        - Maximize city-wide survivability duration
        - Enforce priority-aware load shedding
        - Optimize resource allocation across microgrids
        - Protect critical infrastructure
        """
        outputs.info.append("Widespread outage - coordinating all microgrids")
        
        # Calculate city-wide resource situation
        city_resources = self._assess_city_resources(meas)
        
        # Determine priority-based load shedding allocation
        shedding_allocation = self._calculate_priority_shedding(meas, city_resources)
        outputs.load_shedding_allocation = shedding_allocation
        
        # Generate supervisory commands for each microgrid
        for mg_id, status in meas.microgrid_statuses.items():
            outputs = self._apply_priority_coordination(mg_id, meas, outputs, 
                                                       shedding_allocation.get(mg_id, 0))
        
        # Calculate and report city survivability
        survivability_hours = self._calculate_city_survivability(meas, shedding_allocation)
        outputs.metrics['city_survivability_hours'] = survivability_hours
        outputs.info.append(f"Estimated city survivability: {survivability_hours:.1f} hours")
        
        return outputs
    
    # =========================================================================
    # COORDINATION: EMERGENCY MODE
    # =========================================================================
    
    def _coordinate_emergency(self, meas: CityWideMeasurements,
                             outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordination for emergency conditions (critical resource shortage)
        
        Objectives:
        - Protect critical infrastructure at all costs
        - Aggressive load shedding on lower-priority microgrids
        - Emergency resource reallocation
        """
        outputs.warnings.append("EMERGENCY MODE - Critical resource shortage")
        
        # Identify microgrids in emergency
        emergency_mgs = [mg_id for mg_id, status in meas.microgrid_statuses.items()
                        if status.resource_criticality == "emergency"]
        
        # Apply emergency policies
        for mg_id, status in meas.microgrid_statuses.items():
            mg_info = self.microgrids.get(mg_id)
            if not mg_info:
                continue
            
            cmd = SupervisoryCommand(
                microgrid_id=mg_id,
                timestamp=meas.timestamp,
                emergency_mode=True
            )
            
            if mg_info.priority == MicrogridPriority.CRITICAL:
                # Critical microgrids: protect at all costs
                cmd.critical_only_mode = True
                cmd.reason = "EMERGENCY - Critical infrastructure protection"
            elif mg_info.priority == MicrogridPriority.HIGH:
                # High priority: shed 50% non-critical
                cmd.target_shed_percent = 50.0
                cmd.reason = "EMERGENCY - Aggressive load shedding"
            else:
                # Lower priority: shed 80% or more
                cmd.target_shed_percent = 80.0
                cmd.reason = "EMERGENCY - Maximum load shedding"
            
            outputs.supervisory_commands[mg_id] = cmd
        
        return outputs
    
    # =========================================================================
    # COORDINATION: RECOVERY MODE
    # =========================================================================
    
    def _coordinate_recovery(self, meas: CityWideMeasurements,
                            outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordination for recovery phase (grid restoration in progress)
        
        Objectives:
        - Orderly restoration of services
        - Priority-based reconnection sequencing
        - Prevent grid overload during recovery
        """
        outputs.info.append("Recovery mode - coordinating grid reconnection")
        
        # Prioritize reconnection order
        reconnection_order = self._calculate_reconnection_priority(meas)
        outputs.resource_prioritization = reconnection_order
        
        for mg_id, status in meas.microgrid_statuses.items():
            mg_info = self.microgrids.get(mg_id)
            if not mg_info:
                continue
            
            cmd = SupervisoryCommand(
                microgrid_id=mg_id,
                timestamp=meas.timestamp
            )
            
            if status.is_islanded:
                # Still islanded - prepare for reconnection
                cmd.battery_soc_target_percent = 80.0
                cmd.reason = "Prepare for grid reconnection"
            else:
                # Reconnected - gradual load restoration
                cmd.target_shed_percent = 0.0  # Restore all loads
                cmd.reason = "Grid restored - resume normal operation"
            
            outputs.supervisory_commands[mg_id] = cmd
        
        return outputs
    
    # =========================================================================
    # PRIORITY-BASED COORDINATION
    # =========================================================================
    
    def _apply_priority_coordination(self, mg_id: str, meas: CityWideMeasurements,
                                    outputs: CityControlOutputs,
                                    target_shed_percent: float = None) -> CityControlOutputs:
        """
        Apply priority-aware coordination to a specific microgrid
        """
        mg_info = self.microgrids.get(mg_id)
        status = meas.microgrid_statuses.get(mg_id)
        
        if not mg_info or not status:
            return outputs
        
        cmd = SupervisoryCommand(
            microgrid_id=mg_id,
            timestamp=meas.timestamp,
            city_priority_level=mg_info.priority.value
        )
        
        # Priority-based guidance
        if mg_info.priority == MicrogridPriority.CRITICAL:
            # Hospital: Protect at all costs
            cmd.battery_reserve_percent = 15.0  # Minimum SoC
            cmd.generator_enable = True
            cmd.max_shed_limit_percent = 0.0  # No shedding of critical loads
            cmd.reason = "Critical priority - maximum protection"
            
        elif mg_info.priority == MicrogridPriority.HIGH:
            # University: Protect with moderate margins
            cmd.battery_reserve_percent = 20.0
            cmd.generator_enable = True
            cmd.max_shed_limit_percent = 30.0
            cmd.reason = "High priority - moderate protection"
            
        elif mg_info.priority == MicrogridPriority.MEDIUM:
            # Industrial: Balance production and resource conservation
            cmd.battery_reserve_percent = 25.0
            cmd.generator_enable = status.battery_soc_percent < 35.0
            cmd.max_shed_limit_percent = 70.0
            cmd.reason = "Medium priority - balanced approach"
            
        else:  # LOW priority
            # Residential: Comfort loads sheddable
            cmd.battery_reserve_percent = 30.0
            cmd.generator_enable = status.battery_soc_percent < 25.0
            cmd.max_shed_limit_percent = 90.0
            cmd.reason = "Low priority - aggressive conservation"
        
        # Apply target shedding if specified
        if target_shed_percent is not None:
            cmd.target_shed_percent = min(target_shed_percent, cmd.max_shed_limit_percent)
        
        outputs.supervisory_commands[mg_id] = cmd
        return outputs
    
    # =========================================================================
    # RESOURCE ASSESSMENT AND ALLOCATION
    # =========================================================================
    
    def _assess_city_resources(self, meas: CityWideMeasurements) -> Dict[str, float]:
        """
        Assess total city-wide resource availability
        """
        total_battery_kwh = sum(
            status.battery_capacity_kwh * (status.battery_soc_percent / 100.0)
            for status in meas.microgrid_statuses.values()
        )
        
        total_fuel_liters = sum(
            status.fuel_remaining_liters
            for status in meas.microgrid_statuses.values()
        )
        
        total_generation_kw = sum(
            status.pv_generation_kw + status.generator_power_kw
            for status in meas.microgrid_statuses.values()
        )
        
        return {
            'total_battery_kwh': total_battery_kwh,
            'total_fuel_liters': total_fuel_liters,
            'total_generation_kw': total_generation_kw,
            'average_soc_percent': (total_battery_kwh / meas.total_battery_energy_kwh * 100.0) 
                                  if meas.total_battery_energy_kwh > 0 else 0
        }
    
    def _calculate_priority_shedding(self, meas: CityWideMeasurements,
                                    city_resources: Dict[str, float]) -> Dict[str, float]:
        """
        Calculate priority-aware load shedding allocation across microgrids
        
        This is the CORE RESILIENCE ALGORITHM that enforces priority policies.
        Strictly enforces: NEVER shed from higher priority if lower priority has headroom.
        
        KEY PRINCIPLE: Trigger shedding based on CRITICAL/HIGH priority MG health,
        then allocate shedding to LOWER priorities first. This prevents violations.
        """
        policy_params = self.policy_parameters[self.state.active_policy]
        shed_order = policy_params['shed_order']
        
        # PRIORITY-AWARE TRIGGER: Check health of CRITICAL and HIGH priority MGs
        # If they're struggling, shed from lower priorities to protect them.
        critical_high_min_soc = 100.0
        for mg_id, mg_info in self.microgrids.items():
            if mg_info.priority in [MicrogridPriority.CRITICAL, MicrogridPriority.HIGH]:
                status = meas.microgrid_statuses.get(mg_id)
                if status:
                    critical_high_min_soc = min(critical_high_min_soc, status.battery_soc_percent)
        
        # Conservative trigger: Only shed from lower priorities when higher priorities
        # are moderately stressed
        required_city_shed_percent = 0.0
        if critical_high_min_soc < 35:
            required_city_shed_percent = 60.0
        elif critical_high_min_soc < 45:
            required_city_shed_percent = 40.0
        elif critical_high_min_soc < 55:
            required_city_shed_percent = 25.0

        # Convert to kW target using current total load
        total_city_load_kw = sum(status.total_load_kw for status in meas.microgrid_statuses.values())
        required_city_shed_kw = (required_city_shed_percent / 100.0) * total_city_load_kw

        allocation: Dict[str, float] = {mg_id: 0.0 for mg_id in self.microgrids.keys()}
        remaining_kw = required_city_shed_kw

        # STRICT PRIORITY ENFORCEMENT: Allocate from LOW→HIGH, never skip tiers
        for priority in shed_order:
            if remaining_kw <= 1e-6:
                break
            
            # Get all MGs at this priority level
            priority_mgs = [(mg_id, mg_info) for mg_id, mg_info in self.microgrids.items() 
                           if mg_info.priority == priority]
            
            # Calculate total available headroom at this priority tier
            tier_available_kw = 0.0
            for mg_id, mg_info in priority_mgs:
                status = meas.microgrid_statuses.get(mg_id)
                if status and status.total_load_kw > 0:
                    max_shed_kw = (mg_info.max_shed_percent / 100.0) * status.total_load_kw
                    tier_available_kw += max_shed_kw
            
            # If this tier can handle all remaining, distribute proportionally within tier
            if tier_available_kw >= remaining_kw:
                for mg_id, mg_info in priority_mgs:
                    status = meas.microgrid_statuses.get(mg_id)
                    if not status or status.total_load_kw <= 0:
                        continue
                    max_shed_kw = (mg_info.max_shed_percent / 100.0) * status.total_load_kw
                    # Proportional share of remaining
                    share = (max_shed_kw / tier_available_kw) if tier_available_kw > 0 else 0
                    shed_kw = share * remaining_kw
                    allocation[mg_id] = (shed_kw / status.total_load_kw) * 100.0
                remaining_kw = 0.0
                break
            else:
                # Max out this entire tier and continue to next
                for mg_id, mg_info in priority_mgs:
                    status = meas.microgrid_statuses.get(mg_id)
                    if not status or status.total_load_kw <= 0:
                        continue
                    max_shed_kw = (mg_info.max_shed_percent / 100.0) * status.total_load_kw
                    allocation[mg_id] = mg_info.max_shed_percent
                    remaining_kw -= max_shed_kw

        return allocation

    def _calculate_city_survivability(self, meas: CityWideMeasurements,
                                     shedding_allocation: Dict[str, float]) -> float:
        """
        Calculate estimated city-wide survivability duration in hours
        """
        min_survivability = float('inf')
        
        for mg_id, status in meas.microgrid_statuses.items():
            mg_info = self.microgrids.get(mg_id)
            if not mg_info:
                continue
            
            # Only consider critical and high priority microgrids
            if mg_info.priority not in [MicrogridPriority.CRITICAL, MicrogridPriority.HIGH]:
                continue
            
            # Calculate effective load after shedding
            shed_percent = shedding_allocation.get(mg_id, 0)
            effective_load = status.total_load_kw * (1.0 - shed_percent / 100.0)
            
            # Calculate runtime with current resources
            if effective_load > 0:
                battery_runtime = (status.battery_capacity_kwh * status.battery_soc_percent / 100.0) / effective_load
                
                # Add generator contribution
                if status.fuel_remaining_liters > 0:
                    # Rough estimate: 0.25 L/kWh fuel consumption
                    gen_energy_kwh = status.fuel_remaining_liters / 0.25
                    gen_runtime = gen_energy_kwh / effective_load
                    total_runtime = battery_runtime + gen_runtime
                else:
                    total_runtime = battery_runtime
                
                min_survivability = min(min_survivability, total_runtime)
        
        return min_survivability if min_survivability != float('inf') else 0.0
    
    def _calculate_reconnection_priority(self, meas: CityWideMeasurements) -> List[str]:
        """
        Calculate priority order for grid reconnection during recovery
        """
        # Prioritize by microgrid priority, then by resource criticality
        priority_list = []
        
        for priority_level in [MicrogridPriority.CRITICAL, MicrogridPriority.HIGH,
                              MicrogridPriority.MEDIUM, MicrogridPriority.LOW]:
            for mg_id, mg_info in self.microgrids.items():
                if mg_info.priority == priority_level:
                    status = meas.microgrid_statuses.get(mg_id)
                    if status and status.is_islanded:
                        priority_list.append(mg_id)
        
        return priority_list
    
    # =========================================================================
    # STATE UPDATE AND METRICS
    # =========================================================================
    
    def _update_city_state(self, meas: CityWideMeasurements):
        """Update internal city-level state from measurements"""
        self.state.total_city_battery_kwh = meas.total_battery_energy_kwh
        self.state.total_city_fuel_liters = meas.total_fuel_liters
        
        if meas.grid_outage_active and not self.state.outage_detected:
            self.state.outage_detected = True
            self.state.outage_start_time = meas.timestamp
        elif not meas.grid_outage_active:
            self.state.outage_detected = False
    
    def _calculate_city_metrics(self, meas: CityWideMeasurements,
                               outputs: CityControlOutputs) -> Dict[str, Any]:
        """Calculate comprehensive city-level metrics"""
        metrics = {
            'timestamp': meas.timestamp.isoformat(),
            'city_mode': self.state.city_mode.value,
            'active_policy': self.state.active_policy.value,
            'total_microgrids': len(meas.microgrid_statuses),
            'microgrids_islanded': meas.microgrids_islanded,
            'microgrids_in_emergency': meas.microgrids_in_emergency,
            'total_load_kw': meas.total_load_kw,
            'total_critical_load_kw': meas.total_critical_load_kw,
            'total_generation_kw': meas.total_generation_kw,
            'average_battery_soc': (meas.total_battery_energy_kwh / 
                                   sum(mg.battery_capacity_kwh for mg in self.microgrids.values())
                                   * 100.0) if self.microgrids else 0,
            'city_survivability_hours': meas.city_survivability_hours,
            'outage_duration_hours': meas.outage_duration_hours,
        }
        
        # Per-priority metrics
        for priority in MicrogridPriority:
            priority_mgs = [mg for mg in self.microgrids.values() if mg.priority == priority]
            if priority_mgs:
                metrics[f'{priority.name.lower()}_count'] = len(priority_mgs)
                metrics[f'{priority.name.lower()}_islanded'] = sum(
                    1 for mg in priority_mgs 
                    if meas.microgrid_statuses.get(mg.microgrid_id, None) and 
                    meas.microgrid_statuses[mg.microgrid_id].is_islanded
                )
        
        return metrics
    
    # =========================================================================
    # POLICY MANAGEMENT
    # =========================================================================
    
    def set_resilience_policy(self, policy: ResiliencePolicy):
        """
        Change the active resilience policy
        
        Args:
            policy: New resilience policy to enforce
        """
        logger.info(f"Changing resilience policy from {self.state.active_policy.value} "
                   f"to {policy.value}")
        self.state.active_policy = policy
    
    def get_policy_description(self, policy: ResiliencePolicy = None) -> str:
        """Get description of a resilience policy"""
        if policy is None:
            policy = self.state.active_policy
        
        descriptions = {
            ResiliencePolicy.CRITICAL_FIRST: 
                "Prioritizes critical infrastructure (hospitals) above all else. "
                "Residential and industrial loads shed first.",
            ResiliencePolicy.BALANCED: 
                "Balanced approach with moderate prioritization. "
                "All microgrids receive fair treatment with priority weighting.",
            ResiliencePolicy.EQUITABLE: 
                "Equal treatment for all microgrids regardless of priority. "
                "Load shedding distributed proportionally.",
            ResiliencePolicy.ECONOMIC: 
                "Prioritizes economic activity (industrial) alongside critical services. "
                "Minimizes economic impact of outages."
        }
        
        return descriptions.get(policy, "Unknown policy")