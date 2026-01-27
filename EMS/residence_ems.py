"""
Residential Microgrid Energy Management System (EMS)

This module implements a LOCAL EMS for a SINGLE residential microgrid.
The EMS is responsible for control decisions ONLY - not physics simulation.

Key Responsibilities:
- Operation mode management (grid-connected, islanded, transitions)
- Grid fault detection and islanding logic
- Battery dispatch decisions
- Generator dispatch decisions
- Load shedding and restoration
- Critical load protection (ALWAYS)

Architecture:
- EMS receives measurements from components
- EMS outputs control commands (setpoints, start/stop, shed amounts)
- EMS does NOT modify component internals
- EMS does NOT simulate energy flows

Residential-specific features:
- Time-of-use electricity rate awareness
- EV charging management (delay/curtail during outages)
- Comfort load prioritization (AC, washing machines)
- Resident-friendly load shedding (minimize disruption)
- Peak demand management for community
- Community services protection (lifts, water pumps, security)
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# OPERATION MODES
# =============================================================================

class OperationMode(Enum):
    """Microgrid operation modes"""
    GRID_CONNECTED = "grid_connected"
    ISLANDED = "islanded"
    TRANSITION_TO_ISLAND = "transition_to_island"
    TRANSITION_TO_GRID = "transition_to_grid"
    FAULT = "fault"


# =============================================================================
# CONTROL COMMANDS
# =============================================================================

@dataclass
class BatteryCommand:
    """Battery control commands"""
    power_setpoint_kw: float  # Positive = discharge, Negative = charge
    enable: bool = True
    
    
@dataclass
class GeneratorCommand:
    """Generator control commands"""
    start: bool = False
    stop: bool = False
    power_setpoint_kw: float = 0.0
    enable: bool = False


@dataclass
class LoadSheddingCommand:
    """Load shedding control commands"""
    category: str
    shed_amount_kw: float
    shed_percent: float
    restore: bool = False


@dataclass
class EMSControlOutputs:
    """Complete EMS control outputs for one timestep"""
    timestamp: datetime
    operation_mode: OperationMode
    battery_command: BatteryCommand
    gen1_command: GeneratorCommand
    gen2_command: GeneratorCommand
    load_shedding_commands: List[LoadSheddingCommand] = field(default_factory=list)
    grid_breaker_close: bool = False
    grid_breaker_open: bool = False
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)


# =============================================================================
# EMS STATE TRACKING
# =============================================================================

@dataclass
class EMSState:
    """Internal EMS state (persistent across timesteps)"""
    operation_mode: OperationMode = OperationMode.GRID_CONNECTED
    mode_entry_time: Optional[datetime] = None
    
    # Fault detection state
    grid_voltage_pu: float = 1.0
    grid_frequency_hz: float = 50.0
    consecutive_fault_counts: int = 0
    fault_detected: bool = False
    fault_detection_time: Optional[datetime] = None
    
    # Reconnection state
    grid_stable_since: Optional[datetime] = None
    reconnection_eligible: bool = False
    
    # Generator state tracking
    gen1_running: bool = False
    gen2_running: bool = False
    gen1_start_time: Optional[datetime] = None
    gen2_start_time: Optional[datetime] = None
    gen1_stop_time: Optional[datetime] = None
    gen2_stop_time: Optional[datetime] = None
    
    # Load shedding state
    active_load_sheds: Dict[str, float] = field(default_factory=dict)  # category -> shed_kw
    last_shed_time: Optional[datetime] = None
    last_restore_time: Optional[datetime] = None
    
    # Battery state tracking
    battery_soc_percent: float = 85.0
    battery_power_kw: float = 0.0
    
    # Load tracking
    total_load_kw: float = 0.0
    critical_load_kw: float = 100.0
    
    # Residential-specific tracking
    time_period: str = "day"  # night, morning, day, evening
    peak_rate_period: bool = False
    ev_charging_active: bool = False
    
    def reset_mode(self, new_mode: OperationMode, timestamp: datetime):
        """Reset mode and entry time"""
        self.operation_mode = new_mode
        self.mode_entry_time = timestamp


# =============================================================================
# MEASUREMENTS FROM COMPONENTS
# =============================================================================

@dataclass
class MicrogridMeasurements:
    """Measurements from microgrid components (inputs to EMS)"""
    timestamp: datetime
    
    # Grid measurements
    grid_available: bool
    grid_voltage_pu: float
    grid_frequency_hz: float
    grid_power_kw: float
    
    # Battery measurements
    battery_soc_percent: float
    battery_power_kw: float  # Positive = discharge
    battery_available: bool
    
    # PV measurements
    pv_available_power_kw: float
    pv_actual_power_kw: float
    
    # Generator measurements
    gen1_running: bool
    gen1_power_kw: float
    gen2_running: bool
    gen2_power_kw: float
    
    # Load measurements
    total_load_demand_kw: float
    critical_load_kw: float
    non_critical_load_kw: float
    active_load_sheds: Dict[str, float]  # category -> currently_shed_kw


# =============================================================================
# RESIDENTIAL MICROGRID EMS
# =============================================================================

class ResidenceEMS:
    """
    Local Energy Management System for Residential Microgrid
    
    This EMS makes control decisions based on measurements.
    It does NOT simulate physics or manage component internals.
    
    Residential community-specific features:
    - Time-of-use electricity rate optimization
    - EV charging smart management (delay/curtail during outages)
    - Comfort load prioritization (minimize resident disruption)
    - Community services always protected (lifts, water, security)
    - Peak demand charge management for entire community
    - Solar self-consumption maximization
    """
    
    def __init__(self, config):
        """
        Initialize Residential EMS
        
        Args:
            config: MicrogridConfig object with all parameters
        """
        self.config = config
        self.state = EMSState()
        
        # Extract key parameters for quick access
        self.critical_load_kw = config.load_profile.total_critical_load
        self.load_categories = config.load_profile.load_categories
        
        # Protection thresholds
        self.over_freq_hz = config.protection.over_frequency_hz
        self.under_freq_hz = config.protection.under_frequency_hz
        self.over_voltage_pu = config.protection.over_voltage_pu
        self.under_voltage_pu = config.protection.under_voltage_pu
        
        # Reconnection windows
        self.recon_v_min, self.recon_v_max = config.protection.reconnection_voltage_window_pu
        self.recon_f_min, self.recon_f_max = config.protection.reconnection_frequency_window_hz
        self.reconnection_delay_sec = config.control.reconnection_delay_seconds
        
        # Generator thresholds
        self.gen_auto_start_soc = config.generator.auto_start_soc_threshold
        self.gen_auto_stop_soc = config.generator.auto_stop_soc_threshold
        self.gen_min_on_time = timedelta(minutes=config.generator.min_on_time_minutes)
        self.gen_min_off_time = timedelta(minutes=config.generator.min_off_time_minutes)
        
        # Load shedding parameters
        self.restore_time_min = config.control.restore_time_min
        self.restore_margin_ratio = config.control.restore_margin_ratio
        
        # Residential-specific time periods
        self.time_periods = {
            'night': (0, 5),      # 12 AM - 5 AM (low usage, off-peak rates)
            'morning': (5, 9),    # 5 AM - 9 AM (high usage, peak rates)
            'day': (9, 17),       # 9 AM - 5 PM (medium usage, off-peak rates)
            'evening': (17, 23),  # 5 PM - 11 PM (high usage, peak rates)
            'late': (23, 24)      # 11 PM - 12 AM (medium usage)
        }
        
        # Peak electricity rate hours (highest utility costs)
        self.peak_rate_hours = [(5, 9), (17, 23)]  # Morning and evening peaks
        
        logger.info(f"Residential EMS initialized for {config.facility_name}")
        logger.info(f"  Community critical services: {self.critical_load_kw} kW")
        logger.info(f"  Generator auto-start SoC: {self.gen_auto_start_soc}%")
        
    # =========================================================================
    # MAIN UPDATE LOOP
    # =========================================================================
    
    def update(self, measurements: MicrogridMeasurements) -> EMSControlOutputs:
        """
        Main EMS update - called every timestep
        
        Args:
            measurements: Current measurements from all components
            
        Returns:
            EMSControlOutputs with all control decisions
        """
        # Update internal state with measurements
        self._update_state_from_measurements(measurements)
        
        # Update residential context (time periods, peak rates)
        self._update_residential_context(measurements.timestamp)
        
        # Initialize output structure
        outputs = EMSControlOutputs(
            timestamp=measurements.timestamp,
            operation_mode=self.state.operation_mode,
            battery_command=BatteryCommand(power_setpoint_kw=0.0),
            gen1_command=GeneratorCommand(),
            gen2_command=GeneratorCommand()
        )
        
        # Run operation mode state machine
        outputs = self._run_state_machine(measurements, outputs)
        
        # Execute mode-specific control logic
        if self.state.operation_mode == OperationMode.GRID_CONNECTED:
            outputs = self._control_grid_connected(measurements, outputs)
            
        elif self.state.operation_mode == OperationMode.TRANSITION_TO_ISLAND:
            outputs = self._control_transition_to_island(measurements, outputs)
            
        elif self.state.operation_mode == OperationMode.ISLANDED:
            outputs = self._control_islanded(measurements, outputs)
            
        elif self.state.operation_mode == OperationMode.TRANSITION_TO_GRID:
            outputs = self._control_transition_to_grid(measurements, outputs)
            
        elif self.state.operation_mode == OperationMode.FAULT:
            outputs = self._control_fault(measurements, outputs)
        
        return outputs
    
    # =========================================================================
    # RESIDENTIAL CONTEXT MANAGEMENT
    # =========================================================================
    
    def _update_residential_context(self, timestamp: datetime):
        """
        Update residential-specific context (time period, peak rates)
        """
        hour = timestamp.hour
        
        # Determine current time period
        if 0 <= hour < 5:
            self.state.time_period = 'night'
        elif 5 <= hour < 9:
            self.state.time_period = 'morning'
        elif 9 <= hour < 17:
            self.state.time_period = 'day'
        elif 17 <= hour < 23:
            self.state.time_period = 'evening'
        else:
            self.state.time_period = 'late'
        
        # Check if in peak rate period
        self.state.peak_rate_period = any(
            start <= hour < end for start, end in self.peak_rate_hours
        )
    
    # =========================================================================
    # STATE MACHINE
    # =========================================================================
    
    def _run_state_machine(self, meas: MicrogridMeasurements, 
                          outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Operation mode state machine transitions
        
        State transitions:
        GRID_CONNECTED -> TRANSITION_TO_ISLAND (on grid fault)
        TRANSITION_TO_ISLAND -> ISLANDED (after protection delay)
        ISLANDED -> TRANSITION_TO_GRID (when grid stable)
        TRANSITION_TO_GRID -> GRID_CONNECTED (after reconnection delay)
        Any state -> FAULT (on critical fault)
        """
        current_mode = self.state.operation_mode
        
        # Check for grid faults (in grid-connected mode)
        if current_mode == OperationMode.GRID_CONNECTED:
            if self._detect_grid_fault(meas):
                outputs.info.append("Grid fault detected - initiating islanding")
                outputs.grid_breaker_open = True
                self.state.reset_mode(OperationMode.TRANSITION_TO_ISLAND, meas.timestamp)
                self.state.fault_detection_time = meas.timestamp
                outputs.operation_mode = OperationMode.TRANSITION_TO_ISLAND
        
        # Transition to island complete
        elif current_mode == OperationMode.TRANSITION_TO_ISLAND:
            # Immediate transition (protection delay handled by component)
            outputs.info.append("Islanding transition complete")
            self.state.reset_mode(OperationMode.ISLANDED, meas.timestamp)
            outputs.operation_mode = OperationMode.ISLANDED
        
        # Check for grid restoration (in islanded mode)
        elif current_mode == OperationMode.ISLANDED:
            if self._check_reconnection_eligible(meas):
                outputs.info.append("Grid stable - eligible for reconnection")
                self.state.reset_mode(OperationMode.TRANSITION_TO_GRID, meas.timestamp)
                outputs.operation_mode = OperationMode.TRANSITION_TO_GRID
        
        # Reconnection delay
        elif current_mode == OperationMode.TRANSITION_TO_GRID:
            time_in_mode = (meas.timestamp - self.state.mode_entry_time).total_seconds()
            if time_in_mode >= self.reconnection_delay_sec:
                outputs.info.append("Reconnection delay complete - closing grid breaker")
                outputs.grid_breaker_close = True
                self.state.reset_mode(OperationMode.GRID_CONNECTED, meas.timestamp)
                outputs.operation_mode = OperationMode.GRID_CONNECTED
                self.state.grid_stable_since = None
            else:
                # Check grid remains stable during reconnection delay
                if not self._is_grid_stable(meas):
                    outputs.warnings.append("Grid unstable during reconnection - aborting")
                    self.state.reset_mode(OperationMode.ISLANDED, meas.timestamp)
                    outputs.operation_mode = OperationMode.ISLANDED
        
        return outputs
    
    # =========================================================================
    # GRID FAULT DETECTION
    # =========================================================================
    
    def _detect_grid_fault(self, meas: MicrogridMeasurements) -> bool:
        """
        Detect grid faults based on voltage and frequency
        
        Returns True if fault detected
        """
        if not meas.grid_available:
            self.state.fault_detected = True
            return True
        
        # Voltage fault
        if (meas.grid_voltage_pu > self.over_voltage_pu or 
            meas.grid_voltage_pu < self.under_voltage_pu):
            logger.warning(f"Grid voltage fault: {meas.grid_voltage_pu:.3f} pu")
            self.state.fault_detected = True
            return True
        
        # Frequency fault
        if (meas.grid_frequency_hz > self.over_freq_hz or 
            meas.grid_frequency_hz < self.under_freq_hz):
            logger.warning(f"Grid frequency fault: {meas.grid_frequency_hz:.2f} Hz")
            self.state.fault_detected = True
            return True
        
        self.state.fault_detected = False
        return False
    
    def _is_grid_stable(self, meas: MicrogridMeasurements) -> bool:
        """Check if grid is within reconnection windows"""
        if not meas.grid_available:
            return False
        
        v_ok = self.recon_v_min <= meas.grid_voltage_pu <= self.recon_v_max
        f_ok = self.recon_f_min <= meas.grid_frequency_hz <= self.recon_f_max
        
        return v_ok and f_ok
    
    def _check_reconnection_eligible(self, meas: MicrogridMeasurements) -> bool:
        """
        Check if grid has been stable long enough for reconnection
        """
        if not self._is_grid_stable(meas):
            self.state.grid_stable_since = None
            return False
        
        # Start tracking stable time
        if self.state.grid_stable_since is None:
            self.state.grid_stable_since = meas.timestamp
        
        # Check if stable for required duration
        stable_duration = (meas.timestamp - self.state.grid_stable_since).total_seconds()
        
        # Require grid stable for at least 60 seconds before reconnection
        return stable_duration >= 60.0
    
    # =========================================================================
    # CONTROL: GRID-CONNECTED MODE
    # =========================================================================
    
    def _control_grid_connected(self, meas: MicrogridMeasurements,
                               outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Control logic for grid-connected operation
        
        Residential priorities:
        1. Maximize solar self-consumption (reduce electricity bills)
        2. Peak rate avoidance (discharge battery during peak hours)
        3. Off-peak charging (charge battery during low-cost periods)
        4. EV charging management (smart scheduling)
        5. Maintain battery ready for backup
        """
        # Restore any load sheds
        if self.state.active_load_sheds:
            outputs = self._restore_all_loads(meas, outputs)
        
        # Ensure generators are off in grid-connected mode
        if self.state.gen1_running:
            if self._can_stop_generator('gen1', meas.timestamp):
                outputs.gen1_command.stop = True
                outputs.info.append("Stopping Gen1 - grid connected")
        
        if self.state.gen2_running:
            if self._can_stop_generator('gen2', meas.timestamp):
                outputs.gen2_command.stop = True
                outputs.info.append("Stopping Gen2 - grid connected")
        
        # Battery dispatch for residential optimization
        outputs.battery_command = self._dispatch_battery_grid_connected(meas)
        
        return outputs
    
    def _dispatch_battery_grid_connected(self, meas: MicrogridMeasurements) -> BatteryCommand:
        """
        Battery dispatch for grid-connected mode
        
        Residential strategy:
        - Peak rate avoidance: Discharge during high-rate periods
        - Off-peak charging: Charge during low-rate periods (night)
        - Solar self-consumption: Store excess PV
        - Maintain backup readiness
        - Support EV charging without increasing peak demand
        """
        net_load = meas.total_load_demand_kw - meas.pv_actual_power_kw
        
        # Default: no battery action
        battery_cmd = BatteryCommand(power_setpoint_kw=0.0, enable=True)
        
        # Priority 1: Store excess PV (maximize self-consumption)
        if net_load < 0 and meas.battery_soc_percent < 85:
            charge_power = min(abs(net_load), self.config.battery.max_charge_power_kw)
            battery_cmd.power_setpoint_kw = -charge_power  # Negative = charge
            return battery_cmd
        
        # Priority 2: Peak rate avoidance (discharge during expensive periods)
        if self.state.peak_rate_period:
            if net_load > 80 and meas.battery_soc_percent > 40:
                # Reduce grid import during peak rates
                discharge_power = min(
                    net_load * 0.5,  # Target 50% reduction in grid import
                    self.config.battery.max_discharge_power_kw
                )
                battery_cmd.power_setpoint_kw = discharge_power
        
        # Priority 3: Off-peak charging (low electricity rates)
        elif self.state.time_period == 'night':
            if meas.battery_soc_percent < 85:
                # Charge from grid during off-peak hours
                charge_power = min(
                    80.0,  # Moderate charging rate (avoid high demand charges)
                    self.config.battery.max_charge_power_kw * 0.4
                )
                battery_cmd.power_setpoint_kw = -charge_power
        
        return battery_cmd
    
    # =========================================================================
    # CONTROL: TRANSITION TO ISLAND
    # =========================================================================
    
    def _control_transition_to_island(self, meas: MicrogridMeasurements,
                                     outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Control during transition to islanded mode
        
        Critical actions:
        1. Ensure grid breaker is open
        2. Activate battery for immediate power support
        3. Prepare generator for startup if needed
        4. Shed non-essential loads immediately (EV charging, AC)
        5. Protect critical community services (lifts, water, security)
        """
        outputs.info.append("Transitioning to island - grid breaker open")
        
        # Battery provides immediate support
        outputs.battery_command = BatteryCommand(
            power_setpoint_kw=self.config.battery.max_discharge_power_kw,
            enable=True
        )
        
        # If battery SoC low, start generator immediately
        if meas.battery_soc_percent < self.gen_auto_start_soc:
            outputs.gen1_command.start = True
            outputs.gen1_command.power_setpoint_kw = self.critical_load_kw
            outputs.info.append("Starting Gen1 - low battery during islanding")
        
        # Immediately shed non-essential loads (EV charging, AC)
        if meas.battery_soc_percent < 30:
            outputs = self._shed_comfort_loads(meas, outputs, shed_percent=100)
            outputs.warnings.append("Low battery - shedding non-essential loads")
        
        return outputs
    
    # =========================================================================
    # CONTROL: ISLANDED MODE
    # =========================================================================
    
    def _control_islanded(self, meas: MicrogridMeasurements,
                         outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Control logic for islanded operation
        
        Residential priority hierarchy:
        1. CRITICAL SERVICES (lifts, water pumps, security) - ALWAYS protected
        2. Battery SoC management
        3. Generator dispatch based on battery SoC
        4. Comfort load shedding (AC, EV charging, washing machines)
        5. PV utilization
        """
        # Generator dispatch based on battery SoC
        outputs = self._dispatch_generators_islanded(meas, outputs)
        
        # Battery dispatch for island support
        outputs.battery_command = self._dispatch_battery_islanded(meas, outputs)
        
        # Load shedding/restoration
        outputs = self._manage_load_shedding(meas, outputs)
        
        return outputs
    
    def _dispatch_generators_islanded(self, meas: MicrogridMeasurements,
                                     outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Generator dispatch for islanded mode
        
        Gen1: Supports critical community services
        Gen2: Not used in residential (single generator configuration)
        """
        # GEN1 LOGIC (Critical services support)
        if not meas.gen1_running:
            # Start Gen1 if battery low
            if meas.battery_soc_percent <= self.gen_auto_start_soc:
                if self._can_start_generator('gen1', meas.timestamp):
                    outputs.gen1_command.start = True
                    outputs.gen1_command.enable = True
                    # Set power to meet current demand (after shedding)
                    target_power = min(
                        meas.total_load_demand_kw - sum(self.state.active_load_sheds.values()),
                        self.config.generator.gen1_rated_power_kw
                    )
                    outputs.gen1_command.power_setpoint_kw = target_power
                    outputs.info.append(f"Starting Gen1 - SoC {meas.battery_soc_percent:.1f}%")
        else:
            # Stop Gen1 if battery recovered
            if meas.battery_soc_percent >= self.gen_auto_stop_soc:
                if self._can_stop_generator('gen1', meas.timestamp):
                    outputs.gen1_command.stop = True
                    outputs.info.append(f"Stopping Gen1 - SoC {meas.battery_soc_percent:.1f}%")
            else:
                # Gen1 running - adjust power to meet demand
                outputs.gen1_command.enable = True
                target_power = min(
                    meas.total_load_demand_kw - sum(self.state.active_load_sheds.values()),
                    self.config.generator.gen1_rated_power_kw
                )
                outputs.gen1_command.power_setpoint_kw = target_power
        
        return outputs
    
    def _dispatch_battery_islanded(self, meas: MicrogridMeasurements,
                                  outputs: EMSControlOutputs) -> BatteryCommand:
        """
        Battery dispatch for islanded mode
        
        Strategy:
        - Discharge to support community loads when PV + Gen insufficient
        - Charge from excess PV when available
        - Preserve SoC for extended outages
        - Prioritize critical community services
        """
        # Calculate net demand (load - PV - generator)
        gen_power = 0
        if meas.gen1_running:
            gen_power += outputs.gen1_command.power_setpoint_kw
        
        net_demand = meas.total_load_demand_kw - meas.pv_actual_power_kw - gen_power
        
        # Adjust for shed loads
        total_shed = sum(self.state.active_load_sheds.values())
        net_demand -= total_shed
        
        if net_demand > 0:
            # Discharge to meet community demand
            discharge_power = min(
                net_demand,
                self.config.battery.max_discharge_power_kw
            )
            return BatteryCommand(power_setpoint_kw=discharge_power, enable=True)
        
        elif net_demand < -10:  # Excess power available (PV surplus)
            # Charge from excess
            charge_power = min(
                abs(net_demand),
                self.config.battery.max_charge_power_kw
            )
            return BatteryCommand(power_setpoint_kw=-charge_power, enable=True)
        
        else:
            # Balanced
            return BatteryCommand(power_setpoint_kw=0.0, enable=True)
    
    # =========================================================================
    # LOAD SHEDDING AND RESTORATION
    # =========================================================================
    
    def _manage_load_shedding(self, meas: MicrogridMeasurements,
                             outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Manage load shedding and restoration for residential community
        
        Shed when:
        - Battery SoC critically low
        - Available power < demand
        
        Restore when:
        - Battery SoC recovered
        - Sufficient generation available
        
        Residential shedding priorities (lowest to highest):
        1. EV charging (highest impact, lowest inconvenience)
        2. Air conditioning (comfort load)
        3. Washing machines (deferrable)
        4. Common area lighting (reduced, not eliminated)
        5. Critical services (lifts, water, security) - NEVER shed
        """
        # Calculate power balance
        available_power = meas.pv_actual_power_kw
        if meas.gen1_running:
            available_power += outputs.gen1_command.power_setpoint_kw
        
        # Add battery discharge capacity
        available_power += self.config.battery.max_discharge_power_kw
        
        current_demand = meas.total_load_demand_kw
        total_shed = sum(self.state.active_load_sheds.values())
        
        # SHEDDING LOGIC
        if meas.battery_soc_percent < 20 or available_power < current_demand:
            # Need to shed more loads
            required_shed = max(
                current_demand - available_power,
                (20 - meas.battery_soc_percent) * 10  # Aggressive shedding at low SoC
            )
            
            if required_shed > total_shed:
                shed_percent = min(100, (required_shed / meas.non_critical_load_kw) * 100)
                outputs = self._shed_comfort_loads(meas, outputs, shed_percent)
                outputs.warnings.append(f"Shedding comfort loads - SoC {meas.battery_soc_percent:.1f}%")
        
        # RESTORATION LOGIC
        elif total_shed > 0:
            # Check if we can restore loads
            if self._can_restore_loads(meas, outputs):
                outputs = self._restore_comfort_loads(meas, outputs)
                outputs.info.append("Restoring comfort loads")
        
        return outputs
    
    def _shed_comfort_loads(self, meas: MicrogridMeasurements,
                           outputs: EMSControlOutputs,
                           shed_percent: float) -> EMSControlOutputs:
        """
        Shed comfort loads by priority
        
        Residential priority (lowest first):
        1. ev_charging (most sheddable, least disruption)
        2. air_conditioning (comfort, but significant power)
        3. washing_machines (deferrable)
        4. common_lighting (can reduce by 80%)
        5. critical (NEVER shed - lifts, water, security)
        """
        # Get load categories sorted by priority (highest priority = lowest number)
        sorted_categories = sorted(
            [(cat, info) for cat, info in self.load_categories.items() 
             if cat != 'critical'],
            key=lambda x: x[1]['priority'],
            reverse=True  # Shed lowest priority first
        )
        
        for category, info in sorted_categories:
            max_shed_percent = info['max_shed_percent']
            category_power = info['power_kw']
            
            # Calculate shed amount
            target_shed_percent = min(shed_percent, max_shed_percent)
            shed_kw = category_power * (target_shed_percent / 100.0)
            
            # Update active sheds
            self.state.active_load_sheds[category] = shed_kw
            
            # Add command
            outputs.load_shedding_commands.append(
                LoadSheddingCommand(
                    category=category,
                    shed_amount_kw=shed_kw,
                    shed_percent=target_shed_percent,
                    restore=False
                )
            )
        
        self.state.last_shed_time = meas.timestamp
        return outputs
    
    def _can_restore_loads(self, meas: MicrogridMeasurements,
                          outputs: EMSControlOutputs) -> bool:
        """
        Check if conditions allow load restoration
        """
        # Time-based: minimum time since last shed/restore
        if self.state.last_shed_time:
            time_since_shed = (meas.timestamp - self.state.last_shed_time).total_seconds() / 60
            if time_since_shed < self.restore_time_min:
                return False
        
        if self.state.last_restore_time:
            time_since_restore = (meas.timestamp - self.state.last_restore_time).total_seconds() / 60
            if time_since_restore < self.restore_time_min:
                return False
        
        # Power-based: ensure sufficient margin
        available_power = meas.pv_actual_power_kw
        if meas.gen1_running:
            available_power += outputs.gen1_command.power_setpoint_kw
        available_power += self.config.battery.max_discharge_power_kw * 0.5  # Conservative
        
        total_shed = sum(self.state.active_load_sheds.values())
        demand_with_restore = meas.total_load_demand_kw - total_shed + total_shed
        
        required_margin = demand_with_restore * self.restore_margin_ratio
        
        # SoC-based: battery must be healthy
        if meas.battery_soc_percent < 35:
            return False
        
        return available_power >= (demand_with_restore + required_margin)
    
    def _restore_comfort_loads(self, meas: MicrogridMeasurements,
                              outputs: EMSControlOutputs) -> EMSControlOutputs:
        """Restore previously shed comfort loads"""
        for category in list(self.state.active_load_sheds.keys()):
            outputs.load_shedding_commands.append(
                LoadSheddingCommand(
                    category=category,
                    shed_amount_kw=0.0,
                    shed_percent=0.0,
                    restore=True
                )
            )
            del self.state.active_load_sheds[category]
        
        self.state.last_restore_time = meas.timestamp
        return outputs
    
    def _restore_all_loads(self, meas: MicrogridMeasurements,
                          outputs: EMSControlOutputs) -> EMSControlOutputs:
        """Restore all loads (used when returning to grid-connected)"""
        for category in list(self.state.active_load_sheds.keys()):
            outputs.load_shedding_commands.append(
                LoadSheddingCommand(
                    category=category,
                    shed_amount_kw=0.0,
                    shed_percent=0.0,
                    restore=True
                )
            )
            del self.state.active_load_sheds[category]
        
        outputs.info.append("Restoring all loads - grid connected")
        return outputs
    
    # =========================================================================
    # CONTROL: TRANSITION TO GRID
    # =========================================================================
    
    def _control_transition_to_grid(self, meas: MicrogridMeasurements,
                                   outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Control during transition back to grid
        
        Actions:
        1. Prepare for synchronization
        2. Reduce generator output gradually
        3. Ensure battery ready for grid-connected mode
        4. Restore all community loads before reconnection
        """
        outputs.info.append("Preparing for grid reconnection")
        
        # Restore all loads before reconnection
        if self.state.active_load_sheds:
            outputs = self._restore_all_loads(meas, outputs)
        
        # Reduce generator output gradually
        if meas.gen1_running:
            outputs.gen1_command.enable = True
            current_demand = meas.total_load_demand_kw - meas.pv_actual_power_kw
            outputs.gen1_command.power_setpoint_kw = max(
                current_demand * 0.5,
                self.critical_load_kw
            )
        
        # Battery provides support during transition
        outputs.battery_command = BatteryCommand(
            power_setpoint_kw=30.0,  # Mild discharge to support
            enable=True
        )
        
        return outputs
    
    # =========================================================================
    # CONTROL: FAULT MODE
    # =========================================================================
    
    def _control_fault(self, meas: MicrogridMeasurements,
                      outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Control during fault condition
        
        Emergency actions:
        1. Ensure grid isolation
        2. Activate all backup resources
        3. Shed all non-essential loads (100% EV, AC, washing)
        4. Protect critical community services only
        """
        outputs.warnings.append("FAULT MODE - Emergency community control active")
        outputs.grid_breaker_open = True
        
        # Full battery discharge
        outputs.battery_command = BatteryCommand(
            power_setpoint_kw=self.config.battery.max_discharge_power_kw,
            enable=True
        )
        
        # Start generator
        if not meas.gen1_running:
            outputs.gen1_command.start = True
            outputs.gen1_command.power_setpoint_kw = self.critical_load_kw
        
        # Shed 100% of comfort loads (protect critical services only)
        outputs = self._shed_comfort_loads(meas, outputs, shed_percent=100)
        
        return outputs
    
    # =========================================================================
    # GENERATOR CONSTRAINTS
    # =========================================================================
    
    def _can_start_generator(self, gen_name: str, timestamp: datetime) -> bool:
        """Check if generator can be started (minimum off time)"""
        if gen_name == 'gen1':
            if self.state.gen1_stop_time is None:
                return True
            time_off = timestamp - self.state.gen1_stop_time
            return time_off >= self.gen_min_off_time
        else:
            if self.state.gen2_stop_time is None:
                return True
            time_off = timestamp - self.state.gen2_stop_time
            return time_off >= self.gen_min_off_time
    
    def _can_stop_generator(self, gen_name: str, timestamp: datetime) -> bool:
        """Check if generator can be stopped (minimum on time)"""
        if gen_name == 'gen1':
            if self.state.gen1_start_time is None:
                return True
            time_on = timestamp - self.state.gen1_start_time
            return time_on >= self.gen_min_on_time
        else:
            if self.state.gen2_start_time is None:
                return True
            time_on = timestamp - self.state.gen2_start_time
            return time_on >= self.gen_min_on_time
    
    # =========================================================================
    # STATE UPDATE FROM MEASUREMENTS
    # =========================================================================
    
    def _update_state_from_measurements(self, meas: MicrogridMeasurements):
        """Update internal EMS state from component measurements"""
        self.state.battery_soc_percent = meas.battery_soc_percent
        self.state.battery_power_kw = meas.battery_power_kw
        self.state.grid_voltage_pu = meas.grid_voltage_pu
        self.state.grid_frequency_hz = meas.grid_frequency_hz
        self.state.total_load_kw = meas.total_load_demand_kw
        
        # Track generator state changes
        if meas.gen1_running and not self.state.gen1_running:
            self.state.gen1_start_time = meas.timestamp
        elif not meas.gen1_running and self.state.gen1_running:
            self.state.gen1_stop_time = meas.timestamp
        self.state.gen1_running = meas.gen1_running
        
        if meas.gen2_running and not self.state.gen2_running:
            self.state.gen2_start_time = meas.timestamp
        elif not meas.gen2_running and self.state.gen2_running:
            self.state.gen2_stop_time = meas.timestamp
        self.state.gen2_running = meas.gen2_running