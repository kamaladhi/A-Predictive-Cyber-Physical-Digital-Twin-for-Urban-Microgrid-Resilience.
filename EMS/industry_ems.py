"""
Industrial Microgrid Energy Management System (EMS)

This module implements a LOCAL EMS for a SINGLE industrial microgrid.
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

Industrial-specific features:
- Production shift awareness (different control strategies per shift)
- Power quality management for sensitive manufacturing equipment
- Controlled load curtailment with advance notice
- Peak demand charge management
- Process equipment protection (CNC, coating, air systems)
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
    curtailment_notice: bool = False  # Industrial-specific: advance notice for controlled shutdown


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
    curtailment_notice_issued: Dict[str, datetime] = field(default_factory=dict)  # category -> notice_time
    
    # Battery state tracking
    battery_soc_percent: float = 80.0
    battery_power_kw: float = 0.0
    
    # Load tracking
    total_load_kw: float = 0.0
    critical_load_kw: float = 220.0
    
    # Industrial-specific tracking
    current_shift: str = "day"  # day, evening, night
    peak_demand_window: bool = False
    production_active: bool = True
    
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
# INDUSTRIAL MICROGRID EMS
# =============================================================================

class IndustryEMS:
    """
    Local Energy Management System for Industrial Microgrid
    
    This EMS makes control decisions based on measurements.
    It does NOT simulate physics or manage component internals.
    
    Industrial-specific features:
    - Production shift-aware control (day/evening/night shifts)
    - Peak demand charge management
    - Controlled load curtailment with advance notice
    - Process equipment protection (CNC machines, coating ovens, air systems)
    - Power quality management for sensitive equipment
    - Energy cost optimization during grid-connected operation
    """
    
    def __init__(self, config):
        """
        Initialize Industrial EMS
        
        Args:
            config: MicrogridConfig object with all parameters
        """
        self.config = config
        self.state = EMSState()
        
        # Extract key parameters for quick access
        self.critical_load_kw = config.load_profile.total_critical_load
        self.load_categories = config.load_profile.load_categories
        
        # Protection thresholds
        self.over_freq_hz = config.protection.over_frequency_threshold_hz
        self.under_freq_hz = config.protection.under_frequency_threshold_hz
        self.over_voltage_pu = config.protection.over_voltage_threshold_pu
        self.under_voltage_pu = config.protection.under_voltage_threshold_pu
        
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
        
        # Industrial-specific parameters
        self.curtailment_notice_minutes = getattr(config, 'curtailment_notice_minutes', 15)
        self.restart_time_minutes = getattr(config, 'restart_time_minutes', 30)
        self.max_curtailment_percent = getattr(config, 'max_curtailment_percent', 70)
        
        # Production shift times (24-hour format)
        self.shift_times = {
            'day': (6, 16),      # 6 AM - 4 PM (primary production)
            'evening': (16, 22), # 4 PM - 10 PM (secondary production)
            'night': (22, 6)     # 10 PM - 6 AM (maintenance/reduced production)
        }
        
        # Peak demand hours (typically when utility rates are highest)
        self.peak_demand_hours = (9, 18)  # 9 AM - 6 PM
        
        logger.info(f"Industrial EMS initialized for {config.facility_name}")
        logger.info(f"  Critical production load: {self.critical_load_kw} kW")
        logger.info(f"  Max curtailment: {self.max_curtailment_percent}%")
        logger.info(f"  Curtailment notice period: {self.curtailment_notice_minutes} min")
        
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
        
        # Update shift and peak demand status
        self._update_industrial_context(measurements.timestamp)
        
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
    # INDUSTRIAL CONTEXT MANAGEMENT
    # =========================================================================
    
    def _update_industrial_context(self, timestamp: datetime):
        """
        Update industrial-specific context (shift, peak demand window)
        """
        hour = timestamp.hour
        
        # Determine current shift
        if self.shift_times['day'][0] <= hour < self.shift_times['day'][1]:
            self.state.current_shift = 'day'
            self.state.production_active = True
        elif self.shift_times['evening'][0] <= hour < self.shift_times['evening'][1]:
            self.state.current_shift = 'evening'
            self.state.production_active = True
        else:
            self.state.current_shift = 'night'
            self.state.production_active = False  # Reduced production/maintenance
        
        # Check if in peak demand window
        self.state.peak_demand_window = (
            self.peak_demand_hours[0] <= hour < self.peak_demand_hours[1]
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
        
        Industrial priorities:
        1. Peak demand charge management (reduce grid import during peak hours)
        2. Maximize PV self-consumption
        3. Energy cost optimization (charge battery during off-peak)
        4. Maintain battery ready for backup
        5. Power quality for sensitive manufacturing equipment
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
        
        # Battery dispatch for industrial optimization
        outputs.battery_command = self._dispatch_battery_grid_connected(meas)
        
        return outputs
    
    def _dispatch_battery_grid_connected(self, meas: MicrogridMeasurements) -> BatteryCommand:
        """
        Battery dispatch for grid-connected mode
        
        Industrial strategy:
        - Peak demand reduction: Discharge during peak hours to reduce demand charges
        - Off-peak charging: Charge during low-cost periods
        - PV self-consumption: Store excess solar
        - Shift-aware: More aggressive during production shifts
        - Maintain backup readiness
        """
        net_load = meas.total_load_demand_kw - meas.pv_actual_power_kw
        
        # Default: no battery action
        battery_cmd = BatteryCommand(power_setpoint_kw=0.0, enable=True)
        
        # If excess PV, charge battery
        if net_load < 0 and meas.battery_soc_percent < 85:
            charge_power = min(abs(net_load), self.config.battery.max_charge_power_kw)
            battery_cmd.power_setpoint_kw = -charge_power  # Negative = charge
            return battery_cmd
        
        # Peak demand management during production hours
        if self.state.peak_demand_window and self.state.production_active:
            # Discharge battery to reduce grid import during peak demand periods
            if net_load > 150 and meas.battery_soc_percent > 50:
                # Target 30-40% reduction in grid import
                discharge_power = min(
                    net_load * 0.35,
                    self.config.battery.max_discharge_power_kw
                )
                battery_cmd.power_setpoint_kw = discharge_power
        
        # Off-peak charging during night shift (low utility rates)
        elif self.state.current_shift == 'night':
            if meas.battery_soc_percent < 80 and net_load < 200:
                # Charge from grid during off-peak
                charge_power = min(
                    100.0,  # Conservative charging rate
                    self.config.battery.max_charge_power_kw * 0.5
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
        3. Issue curtailment notices to non-critical processes
        4. Prepare generators for startup if needed
        5. Controlled load shedding with advance notice
        """
        outputs.info.append("Transitioning to island - grid breaker open")
        
        # Battery provides immediate support
        outputs.battery_command = BatteryCommand(
            power_setpoint_kw=self.config.battery.max_discharge_power_kw,
            enable=True
        )
        
        # If battery SoC low, start generators immediately
        if meas.battery_soc_percent < self.gen_auto_start_soc:
            outputs.gen1_command.start = True
            outputs.gen1_command.power_setpoint_kw = self.critical_load_kw
            outputs.info.append("Starting Gen1 - low battery during islanding")
        
        # Issue curtailment notices if needed
        if meas.battery_soc_percent < 25:
            outputs = self._issue_curtailment_notices(meas, outputs)
            outputs.warnings.append("Low battery - curtailment notices issued")
        
        return outputs
    
    # =========================================================================
    # CONTROL: ISLANDED MODE
    # =========================================================================
    
    def _control_islanded(self, meas: MicrogridMeasurements,
                         outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Control logic for islanded operation
        
        Industrial priority hierarchy:
        1. CRITICAL PRODUCTION (CNC, coating, air systems, cooling) - ALWAYS protected
        2. Battery SoC management
        3. Generator dispatch based on battery SoC
        4. Controlled load curtailment (with advance notice)
        5. Non-essential load shedding (HVAC, material handling, assembly)
        6. PV utilization
        """
        # Generator dispatch based on battery SoC
        outputs = self._dispatch_generators_islanded(meas, outputs)
        
        # Battery dispatch for island support
        outputs.battery_command = self._dispatch_battery_islanded(meas, outputs)
        
        # Load curtailment/restoration
        outputs = self._manage_load_curtailment(meas, outputs)
        
        return outputs
    
    def _dispatch_generators_islanded(self, meas: MicrogridMeasurements,
                                     outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Generator dispatch for islanded mode
        
        Gen1: Supports critical production loads (CNC, coating, air systems)
        Gen2: Supports non-critical production loads (assembly, material handling)
        """
        # GEN1 LOGIC (Critical production support)
        if not meas.gen1_running:
            # Start Gen1 if battery low
            if meas.battery_soc_percent <= self.gen_auto_start_soc:
                if self._can_start_generator('gen1', meas.timestamp):
                    outputs.gen1_command.start = True
                    outputs.gen1_command.enable = True
                    outputs.gen1_command.power_setpoint_kw = self.critical_load_kw
                    outputs.info.append(f"Starting Gen1 - SoC {meas.battery_soc_percent:.1f}%")
        else:
            # Stop Gen1 if battery recovered
            if meas.battery_soc_percent >= self.gen_auto_stop_soc:
                if self._can_stop_generator('gen1', meas.timestamp):
                    outputs.gen1_command.stop = True
                    outputs.info.append(f"Stopping Gen1 - SoC {meas.battery_soc_percent:.1f}%")
            else:
                # Gen1 running - set power to critical load
                outputs.gen1_command.enable = True
                outputs.gen1_command.power_setpoint_kw = self.critical_load_kw
        
        # GEN2 LOGIC (Non-critical production support)
        non_critical_demand = meas.non_critical_load_kw - sum(self.state.active_load_sheds.values())
        
        if not meas.gen2_running:
            # Start Gen2 if battery low and production active
            if meas.battery_soc_percent <= self.gen_auto_start_soc - 5:
                if non_critical_demand > 150 and self._can_start_generator('gen2', meas.timestamp):
                    outputs.gen2_command.start = True
                    outputs.gen2_command.enable = True
                    outputs.gen2_command.power_setpoint_kw = min(
                        non_critical_demand,
                        self.config.generator.gen2_rated_power_kw
                    )
                    outputs.info.append(f"Starting Gen2 - supporting production loads")
        else:
            # Stop Gen2 if battery recovered or loads curtailed
            if meas.battery_soc_percent >= self.gen_auto_stop_soc or non_critical_demand < 100:
                if self._can_stop_generator('gen2', meas.timestamp):
                    outputs.gen2_command.stop = True
                    outputs.info.append("Stopping Gen2")
            else:
                # Gen2 running - set power to non-critical demand
                outputs.gen2_command.enable = True
                outputs.gen2_command.power_setpoint_kw = min(
                    non_critical_demand,
                    self.config.generator.gen2_rated_power_kw
                )
        
        return outputs
    
    def _dispatch_battery_islanded(self, meas: MicrogridMeasurements,
                                  outputs: EMSControlOutputs) -> BatteryCommand:
        """
        Battery dispatch for islanded mode
        
        Strategy:
        - Discharge to support production loads when PV + Gen insufficient
        - Charge from excess PV when available
        - Preserve SoC for extended outages
        - Prioritize critical manufacturing equipment
        """
        # Calculate net demand (load - PV - generators)
        gen_power = 0
        if meas.gen1_running:
            gen_power += outputs.gen1_command.power_setpoint_kw
        if meas.gen2_running:
            gen_power += outputs.gen2_command.power_setpoint_kw
        
        net_demand = meas.total_load_demand_kw - meas.pv_actual_power_kw - gen_power
        
        # Adjust for curtailed loads
        total_shed = sum(self.state.active_load_sheds.values())
        net_demand -= total_shed
        
        if net_demand > 0:
            # Discharge to meet production demand
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
    # LOAD CURTAILMENT AND RESTORATION
    # =========================================================================
    
    def _manage_load_curtailment(self, meas: MicrogridMeasurements,
                                outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Manage load curtailment and restoration for industrial facility
        
        Curtail when:
        - Battery SoC critically low
        - Available power < demand
        
        Restore when:
        - Battery SoC recovered
        - Sufficient generation available
        - Minimum restart time elapsed
        
        Industrial curtailment priorities (lowest to highest):
        1. Canteen/welfare facilities
        2. Office HVAC
        3. Assembly lines (can be restarted)
        4. Material handling (automated systems)
        5. HVAC in production areas
        6. Metal stamping (energy-intensive, sheddable)
        7. Critical production (CNC, coating, air, cooling) - NEVER curtail
        """
        # Calculate power balance
        available_power = meas.pv_actual_power_kw
        if meas.gen1_running:
            available_power += outputs.gen1_command.power_setpoint_kw
        if meas.gen2_running:
            available_power += outputs.gen2_command.power_setpoint_kw
        
        # Add battery discharge capacity
        available_power += self.config.battery.max_discharge_power_kw
        
        current_demand = meas.total_load_demand_kw
        total_curtailed = sum(self.state.active_load_sheds.values())
        
        # CURTAILMENT LOGIC
        if meas.battery_soc_percent < 18 or available_power < current_demand:
            # Need to curtail more loads
            required_curtailment = max(
                current_demand - available_power,
                (18 - meas.battery_soc_percent) * 15  # Aggressive curtailment at low SoC
            )
            
            if required_curtailment > total_curtailed:
                # Calculate curtailment percentage
                curtail_percent = min(
                    self.max_curtailment_percent,
                    (required_curtailment / meas.non_critical_load_kw) * 100
                )
                outputs = self._curtail_production_loads(meas, outputs, curtail_percent)
                outputs.warnings.append(f"Curtailing production loads - SoC {meas.battery_soc_percent:.1f}%")
        
        # RESTORATION LOGIC
        elif total_curtailed > 0:
            # Check if we can restore loads
            if self._can_restore_production(meas, outputs):
                outputs = self._restore_production_loads(meas, outputs)
                outputs.info.append("Restoring production loads")
        
        return outputs
    
    def _issue_curtailment_notices(self, meas: MicrogridMeasurements,
                                  outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Issue advance curtailment notices to production equipment
        
        This gives process controllers time to safely shut down equipment
        """
        for category, info in self.load_categories.items():
            if category == 'critical_production':
                continue  # Never curtail critical production
            
            if category not in self.state.curtailment_notice_issued:
                outputs.load_shedding_commands.append(
                    LoadSheddingCommand(
                        category=category,
                        shed_amount_kw=0.0,
                        shed_percent=0.0,
                        restore=False,
                        curtailment_notice=True
                    )
                )
                self.state.curtailment_notice_issued[category] = meas.timestamp
                outputs.info.append(f"Curtailment notice issued: {category}")
        
        return outputs
    
    def _curtail_production_loads(self, meas: MicrogridMeasurements,
                                 outputs: EMSControlOutputs,
                                 curtail_percent: float) -> EMSControlOutputs:
        """
        Curtail production loads by priority
        
        Industrial priority (lowest first):
        1. canteen_welfare
        2. office_hvac
        3. assembly_lines
        4. material_handling
        5. hvac_production
        6. metal_stamping
        7. critical_production (NEVER curtail)
        """
        # Get load categories sorted by priority
        sorted_categories = sorted(
            [(cat, info) for cat, info in self.load_categories.items() 
             if cat != 'critical_production'],
            key=lambda x: x[1]['priority'],
            reverse=True  # Curtail lowest priority first
        )
        
        for category, info in sorted_categories:
            max_shed_percent = info['max_shed_percent']
            category_power = info['power_kw']
            
            # Check if curtailment notice period has elapsed
            notice_elapsed = True
            if category in self.state.curtailment_notice_issued:
                time_since_notice = (meas.timestamp - self.state.curtailment_notice_issued[category]).total_seconds() / 60
                notice_elapsed = time_since_notice >= self.curtailment_notice_minutes
            
            # Only curtail if notice period elapsed or immediate curtailment needed
            if notice_elapsed or meas.battery_soc_percent < 15:
                # Calculate curtailment amount
                target_curtail_percent = min(curtail_percent, max_shed_percent)
                curtail_kw = category_power * (target_curtail_percent / 100.0)
                
                # Update active sheds
                self.state.active_load_sheds[category] = curtail_kw
                
                # Add command
                outputs.load_shedding_commands.append(
                    LoadSheddingCommand(
                        category=category,
                        shed_amount_kw=curtail_kw,
                        shed_percent=target_curtail_percent,
                        restore=False,
                        curtailment_notice=False
                    )
                )
        
        self.state.last_shed_time = meas.timestamp
        return outputs
    
    def _can_restore_production(self, meas: MicrogridMeasurements,
                               outputs: EMSControlOutputs) -> bool:
        """
        Check if conditions allow production restoration
        """
        # Time-based: minimum restart time
        if self.state.last_shed_time:
            time_since_curtail = (meas.timestamp - self.state.last_shed_time).total_seconds() / 60
            if time_since_curtail < self.restart_time_minutes:
                return False
        
        if self.state.last_restore_time:
            time_since_restore = (meas.timestamp - self.state.last_restore_time).total_seconds() / 60
            if time_since_restore < self.restore_time_min:
                return False
        
        # Power-based: ensure sufficient margin for production restart
        available_power = meas.pv_actual_power_kw
        if meas.gen1_running:
            available_power += outputs.gen1_command.power_setpoint_kw
        if meas.gen2_running:
            available_power += outputs.gen2_command.power_setpoint_kw
        available_power += self.config.battery.max_discharge_power_kw * 0.6  # Conservative
        
        total_curtailed = sum(self.state.active_load_sheds.values())
        demand_with_restore = meas.total_load_demand_kw - total_curtailed + total_curtailed
        
        required_margin = demand_with_restore * self.restore_margin_ratio
        
        # SoC-based: battery must be healthy for production restoration
        if meas.battery_soc_percent < 50:
            return False
        
        return available_power >= (demand_with_restore + required_margin)
    
    def _restore_production_loads(self, meas: MicrogridMeasurements,
                                 outputs: EMSControlOutputs) -> EMSControlOutputs:
        """Restore previously curtailed production loads"""
        for category in list(self.state.active_load_sheds.keys()):
            outputs.load_shedding_commands.append(
                LoadSheddingCommand(
                    category=category,
                    shed_amount_kw=0.0,
                    shed_percent=0.0,
                    restore=True,
                    curtailment_notice=False
                )
            )
            del self.state.active_load_sheds[category]
            
            # Clear curtailment notice
            if category in self.state.curtailment_notice_issued:
                del self.state.curtailment_notice_issued[category]
        
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
                    restore=True,
                    curtailment_notice=False
                )
            )
            del self.state.active_load_sheds[category]
            
            # Clear curtailment notice
            if category in self.state.curtailment_notice_issued:
                del self.state.curtailment_notice_issued[category]
        
        outputs.info.append("Restoring all production loads - grid connected")
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
        4. Restore all production loads before reconnection
        """
        outputs.info.append("Preparing for grid reconnection")
        
        # Restore all loads before reconnection
        if self.state.active_load_sheds:
            outputs = self._restore_all_loads(meas, outputs)
        
        # Reduce generator output gradually
        if meas.gen1_running:
            outputs.gen1_command.enable = True
            outputs.gen1_command.power_setpoint_kw = self.critical_load_kw * 0.5
        
        if meas.gen2_running:
            outputs.gen2_command.enable = True
            outputs.gen2_command.power_setpoint_kw = meas.non_critical_load_kw * 0.3
        
        # Battery provides support during transition
        outputs.battery_command = BatteryCommand(
            power_setpoint_kw=50.0,  # Mild discharge to support
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
        3. Curtail non-critical production aggressively
        4. Protect critical manufacturing equipment
        """
        outputs.warnings.append("FAULT MODE - Emergency production control active")
        outputs.grid_breaker_open = True
        
        # Full battery discharge
        outputs.battery_command = BatteryCommand(
            power_setpoint_kw=self.config.battery.max_discharge_power_kw,
            enable=True
        )
        
        # Start all generators
        if not meas.gen1_running:
            outputs.gen1_command.start = True
            outputs.gen1_command.power_setpoint_kw = self.critical_load_kw
        
        # Curtail maximum allowed percentage (protect critical production only)
        outputs = self._curtail_production_loads(meas, outputs, curtail_percent=self.max_curtailment_percent)
        
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