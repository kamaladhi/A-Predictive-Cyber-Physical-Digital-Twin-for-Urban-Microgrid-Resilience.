"""
Hospital Microgrid Energy Management System (EMS)

This module implements a LOCAL EMS for a SINGLE hospital microgrid.
The EMS is responsible for control decisions ONLY - not physics simulation.

Key Responsibilities:
- Operation mode management (grid-connected, islanded, transitions)
- Grid fault detection and islanding logic
- Battery dispatch decisions
- Generator dispatch decisions
- Load shedding and restoration
- Critical load protection (ALWAYS)
- Forecast-aware proactive control (when forecast data available)

Architecture:
- EMS receives measurements from components
- EMS outputs control commands (setpoints, start/stop, shed amounts)
- EMS does NOT modify component internals
- EMS does NOT simulate energy flows
- Forecast data is OPTIONAL — EMS degrades gracefully to rule-based
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
# COST / PENALTY CONFIG (Optimization Preparation)
# =============================================================================

@dataclass
class EMSCostConfig:
    """
    Cost and penalty parameters for future optimization (LP/RL).
    
    These are placeholders that structure the decision space.
    Current rule-based EMS does not optimize against these costs,
    but a future LP/RL-based EMS can minimize total cost directly.
    """
    fuel_cost_per_kwh: float = 0.30           # Generator fuel cost ($/kWh)
    load_shedding_penalty_per_kwh: float = 5.0 # Penalty for unserved energy
    battery_degradation_cost_per_kwh: float = 0.05  # Battery wear cost
    grid_import_cost_per_kwh: float = 0.12    # Grid electricity cost
    grid_export_revenue_per_kwh: float = 0.06 # Feed-in tariff
    critical_load_penalty_multiplier: float = 10.0  # Extra penalty for critical load loss


@dataclass
class EMSDispatchObjective:
    """
    Quantified cost breakdown for a single dispatch decision.

    Used by _score_dispatch_option() to compare candidate dispatch
    strategies.  Separating cost terms enables research analysis of
    trade-offs (e.g. fuel vs. degradation vs. shedding).
    """
    fuel_cost: float = 0.0
    load_shedding_penalty: float = 0.0
    battery_degradation_cost: float = 0.0
    grid_import_cost: float = 0.0
    total_cost: float = 0.0

    def compute_total(self) -> float:
        self.total_cost = (
            self.fuel_cost
            + self.load_shedding_penalty
            + self.battery_degradation_cost
            + self.grid_import_cost
        )
        return self.total_cost


class DispatchMode(Enum):
    """Risk-aware dispatch modes selected from forecast uncertainty."""
    CONSERVATIVE = "conservative"  # high reserve, early gen start
    BALANCED = "balanced"          # default behaviour
    AGGRESSIVE = "aggressive"      # max self-consumption, low reserves


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
    battery_soc_percent: float = 90.0
    battery_power_kw: float = 0.0
    
    # Load tracking
    total_load_kw: float = 0.0
    critical_load_kw: float = 320.0
    
    # Forecast-aware state tracking
    forecast_pre_charge_active: bool = False
    forecast_gen_standby: bool = False
    forecast_reserve_margin: float = 0.10  # Default 10% reserve
    dispatch_mode: str = "balanced"  # conservative / balanced / aggressive
    
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
    
    # PV forecast (optional — EMS works without these)
    pv_forecast_1h: Optional[float] = None    # kW, predicted PV 1h ahead
    pv_forecast_6h: Optional[float] = None    # kW, predicted PV 6h ahead
    pv_forecast_24h: Optional[float] = None   # kW, predicted PV 24h ahead
    forecast_uncertainty: Optional[float] = None  # 0.0–1.0, higher = less certain


# =============================================================================
# HOSPITAL MICROGRID EMS
# =============================================================================

class HospitalEMS:
    """
    Local Energy Management System for Hospital Microgrid
    
    This EMS makes control decisions based on measurements.
    It does NOT simulate physics or manage component internals.
    """
    
    def __init__(self, config):
        """
        Initialize Hospital EMS
        
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
        
        # Cost config (optimization preparation)
        self.cost_config = EMSCostConfig()
        
        # Decision logger (None until attached)
        self.decision_logger = None
        
        logger.info(f"Hospital EMS initialized for {config.facility_name}")
        logger.info(f"  Critical load protection: {self.critical_load_kw} kW")
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
        
        # Apply forecast-aware adjustments (no-op if no forecast data)
        if measurements.pv_forecast_1h is not None:
            outputs = self._apply_forecast_adjustments(measurements, outputs)
        
        # Log decision for research experiments
        self._log_decision(measurements, outputs)
        
        return outputs
    
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
        
        Priority:
        1. Maximize PV self-consumption
        2. Use battery for peak shaving / valley filling
        3. Maintain battery ready for backup (keep SoC high)
        4. Minimize grid import during peak hours
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
        
        # Battery dispatch for self-consumption and grid support
        outputs.battery_command = self._dispatch_battery_grid_connected(meas)
        
        return outputs
    
    def _dispatch_battery_grid_connected(self, meas: MicrogridMeasurements) -> BatteryCommand:
        """
        Battery dispatch for grid-connected mode
        
        Strategy:
        - During day: Store excess PV
        - Keep SoC high for backup readiness
        - Discharge during peak load if beneficial
        """
        net_load = meas.total_load_demand_kw - meas.pv_actual_power_kw
        
        # Default: no battery action
        battery_cmd = BatteryCommand(power_setpoint_kw=0.0, enable=True)
        
        # If excess PV and battery not full, charge
        if net_load < 0 and meas.battery_soc_percent < 85:
            charge_power = min(abs(net_load), self.config.battery.max_charge_power_kw)
            battery_cmd.power_setpoint_kw = -charge_power  # Negative = charge
        
        # If grid import and battery has margin, discharge to reduce import
        elif net_load > 50 and meas.battery_soc_percent > 60:
            discharge_power = min(net_load * 0.5, self.config.battery.max_discharge_power_kw)
            battery_cmd.power_setpoint_kw = discharge_power  # Positive = discharge
        
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
        3. Prepare generators for startup if needed
        4. Shed non-critical loads preemptively if battery low
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
        
        # Preemptive load shedding if battery very low
        if meas.battery_soc_percent < 20:
            outputs = self._shed_non_critical_loads(meas, outputs, shed_percent=50)
            outputs.warnings.append("Low battery - shedding 50% non-critical loads")
        
        return outputs
    
    # =========================================================================
    # CONTROL: ISLANDED MODE
    # =========================================================================
    
    def _control_islanded(self, meas: MicrogridMeasurements,
                         outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Control logic for islanded operation
        
        Priority hierarchy:
        1. CRITICAL LOADS (320 kW) - ALWAYS protected
        2. Battery SoC management
        3. Generator dispatch based on battery SoC
        4. Load shedding/restoration of non-critical loads
        5. PV utilization
        """
        # Calculate available power
        available_power = (
            meas.pv_actual_power_kw +
            meas.battery_power_kw +  # Current battery contribution
            (self.config.generator.gen1_rated_power_kw if meas.gen1_running else 0) +
            (self.config.generator.gen2_rated_power_kw if meas.gen2_running else 0)
        )
        
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
        
        Gen1: Supports critical loads (320 kW)
        Gen2: Supports non-critical loads
        """
        # GEN1 LOGIC (Critical load support)
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
        
        # GEN2 LOGIC (Non-critical load support)
        non_critical_demand = meas.non_critical_load_kw - sum(self.state.active_load_sheds.values())
        
        if not meas.gen2_running:
            # Start Gen2 if battery very low and non-critical loads exist
            if meas.battery_soc_percent <= self.gen_auto_start_soc - 10:
                if non_critical_demand > 50 and self._can_start_generator('gen2', meas.timestamp):
                    outputs.gen2_command.start = True
                    outputs.gen2_command.enable = True
                    outputs.gen2_command.power_setpoint_kw = min(
                        non_critical_demand,
                        self.config.generator.gen2_rated_power_kw
                    )
                    outputs.info.append(f"Starting Gen2 - supporting non-critical loads")
        else:
            # Stop Gen2 if battery recovered or loads shed
            if meas.battery_soc_percent >= self.gen_auto_stop_soc or non_critical_demand < 30:
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
        - Discharge to support loads when PV + Gen insufficient
        - Charge from excess PV when available
        - Preserve SoC for extended outages
        """
        # Calculate net demand (load - PV - generators)
        gen_power = 0
        if meas.gen1_running:
            gen_power += outputs.gen1_command.power_setpoint_kw
        if meas.gen2_running:
            gen_power += outputs.gen2_command.power_setpoint_kw
        
        net_demand = meas.total_load_demand_kw - meas.pv_actual_power_kw - gen_power
        
        # Adjust for already-shed loads
        total_shed = sum(self.state.active_load_sheds.values())
        net_demand -= total_shed
        
        if net_demand > 0:
            # Discharge to meet demand
            discharge_power = min(
                net_demand,
                self.config.battery.max_discharge_power_kw
            )
            return BatteryCommand(power_setpoint_kw=discharge_power, enable=True)
        
        elif net_demand < -10:  # Excess power available
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
        Manage load shedding and restoration
        
        Shed when:
        - Battery SoC critically low
        - Available power < demand
        
        Restore when:
        - Battery SoC recovered
        - Sufficient generation available
        - Minimum time since last shed/restore
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
        total_shed = sum(self.state.active_load_sheds.values())
        
        # SHEDDING LOGIC
        if meas.battery_soc_percent < 15 or available_power < current_demand:
            # Need to shed more loads
            required_shed = max(
                current_demand - available_power,
                (15 - meas.battery_soc_percent) * 10  # Aggressive shedding at low SoC
            )
            
            if required_shed > total_shed:
                shed_percent = min(80, (required_shed / meas.non_critical_load_kw) * 100)
                outputs = self._shed_non_critical_loads(meas, outputs, shed_percent)
                outputs.warnings.append(f"Shedding loads - SoC {meas.battery_soc_percent:.1f}%")
        
        # RESTORATION LOGIC
        elif total_shed > 0:
            # Check if we can restore loads
            if self._can_restore_loads(meas, outputs):
                outputs = self._restore_non_critical_loads(meas, outputs)
                outputs.info.append("Restoring shed loads")
        
        return outputs
    
    def _shed_non_critical_loads(self, meas: MicrogridMeasurements,
                                outputs: EMSControlOutputs,
                                shed_percent: float) -> EMSControlOutputs:
        """
        Shed non-critical loads by priority
        
        Priority (lowest first):
        1. admin_misc
        2. wards_lighting  
        3. hvac_partial
        4. critical (NEVER shed)
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
        if meas.gen2_running:
            available_power += outputs.gen2_command.power_setpoint_kw
        available_power += self.config.battery.max_discharge_power_kw * 0.5  # Conservative
        
        total_shed = sum(self.state.active_load_sheds.values())
        demand_with_restore = meas.total_load_demand_kw - total_shed + total_shed
        
        required_margin = demand_with_restore * self.restore_margin_ratio
        
        # SoC-based: battery must be healthy
        if meas.battery_soc_percent < 40:
            return False
        
        return available_power >= (demand_with_restore + required_margin)
    
    def _restore_non_critical_loads(self, meas: MicrogridMeasurements,
                                   outputs: EMSControlOutputs) -> EMSControlOutputs:
        """Restore previously shed non-critical loads"""
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
        4. Restore all loads before reconnection
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
        3. Shed non-critical loads aggressively
        """
        outputs.warnings.append("FAULT MODE - Emergency control active")
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
        
        # Shed 80% of non-critical loads
        outputs = self._shed_non_critical_loads(meas, outputs, shed_percent=80)
        
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
    
    # =========================================================================
    # FORECAST-AWARE CONTROL
    # =========================================================================
    
    def _apply_forecast_adjustments(self, meas: MicrogridMeasurements,
                                    outputs: EMSControlOutputs) -> EMSControlOutputs:
        """
        Apply forecast-aware adjustments to control outputs.
        
        Called AFTER mode-specific control, allowing forecast data to
        override or augment rule-based decisions. Only runs when
        pv_forecast_1h is not None.
        
        Adjustments:
        1. Pre-charge battery if PV drop forecast (confidence-weighted)
        2. Prepare generator if cloudy forecast + low battery (confidence-scaled threshold)
        3. Increase reserve margin if uncertainty high
        4. Auto-select dispatch mode (conservative/balanced/aggressive)
        5. RELIABILITY GUARDS: min SOC reserve + early gen start buffer
        """
        forecast_decisions = []

        # Update dynamic reserve margin based on uncertainty
        self.state.forecast_reserve_margin = self._compute_reserve_margin(meas)

        # Select dispatch mode from uncertainty + SoC
        self._select_dispatch_mode(meas)

        # Forecast confidence (1.0 = perfect, 0.0 = useless)
        confidence = 1.0 - min(meas.forecast_uncertainty or 0.0, 1.0)

        is_conservative = (self.state.dispatch_mode
                           == DispatchMode.CONSERVATIVE.value)

        # --- RELIABILITY GUARD: minimum SOC reserve ---
        min_soc_reserve = 20.0
        if is_conservative:
            min_soc_reserve = 35.0
            if meas.battery_soc_percent <= min_soc_reserve:
                if outputs.battery_command.power_setpoint_kw > 0:
                    outputs.battery_command.power_setpoint_kw = 0.0
                    forecast_decisions.append(
                        f"RELIABILITY-GUARD: SOC {meas.battery_soc_percent:.0f}%"
                        f" ≤ reserve floor {min_soc_reserve:.0f}% → discharge blocked"
                    )

        # --- RELIABILITY GUARD: early generator start buffer ---
        gen_start_buffer = 0.0
        if is_conservative:
            gen_start_buffer = 15.0
        elif (meas.forecast_uncertainty or 0.0) > 0.4:
            gen_start_buffer = 8.0

        if gen_start_buffer > 0 and not meas.gen1_running:
            buffered_threshold = self.gen_auto_start_soc + gen_start_buffer
            if (self.state.operation_mode == OperationMode.ISLANDED
                    and meas.battery_soc_percent <= buffered_threshold):
                if self._can_start_generator('gen1', meas.timestamp):
                    outputs.gen1_command.start = True
                    outputs.gen1_command.enable = True
                    outputs.gen1_command.power_setpoint_kw = self.critical_load_kw
                    forecast_decisions.append(
                        f"RELIABILITY-GEN-START: SOC {meas.battery_soc_percent:.0f}%"
                        f" ≤ buffered threshold {buffered_threshold:.0f}%"
                        f" (mode={self.state.dispatch_mode})"
                    )

        # --- PRE-CHARGE BATTERY (confidence-weighted) ---
        if self._should_pre_charge(meas):
            if not self.state.forecast_pre_charge_active:
                self.state.forecast_pre_charge_active = True
                forecast_decisions.append("PRE-CHARGE: PV drop forecast, increasing battery charge")

            # Override battery command to charge more aggressively
            if self.state.operation_mode == OperationMode.GRID_CONNECTED:
                target_soc = 95  # Higher target when PV drop expected
                if meas.battery_soc_percent < target_soc:
                    base_power = self.config.battery.max_charge_power_kw * 0.8
                    charge_power = base_power * max(0.3, confidence)
                    outputs.battery_command.power_setpoint_kw = -charge_power
                    outputs.info.append(
                        f"Forecast pre-charge: PV 1h={meas.pv_forecast_1h:.0f}kW, "
                        f"6h={meas.pv_forecast_6h:.0f}kW → charging at {charge_power:.0f}kW "
                        f"(conf={confidence:.0%})"
                    )
        else:
            self.state.forecast_pre_charge_active = False

        # --- PREPARE GENERATOR (confidence-scaled threshold) ---
        if self._should_prepare_generator(meas):
            if not self.state.forecast_gen_standby:
                self.state.forecast_gen_standby = True
                forecast_decisions.append("GEN-STANDBY: cloudy forecast + low battery")
                outputs.info.append(
                    f"Forecast gen standby: PV 6h={meas.pv_forecast_6h:.0f}kW, "
                    f"SOC={meas.battery_soc_percent:.0f}%"
                )

            # In islanded mode, start generator earlier
            if (self.state.operation_mode == OperationMode.ISLANDED
                    and not meas.gen1_running):
                adjusted_threshold = self.gen_auto_start_soc + 10 * confidence
                if meas.battery_soc_percent <= adjusted_threshold:
                    if self._can_start_generator('gen1', meas.timestamp):
                        outputs.gen1_command.start = True
                        outputs.gen1_command.enable = True
                        outputs.gen1_command.power_setpoint_kw = self.critical_load_kw
                        forecast_decisions.append(
                            f"GEN-EARLY-START: SOC {meas.battery_soc_percent:.0f}% "
                            f"< adjusted threshold {adjusted_threshold:.0f}%"
                        )
        else:
            self.state.forecast_gen_standby = False

        # --- INCREASE RESERVE MARGIN ---
        if meas.forecast_uncertainty is not None and meas.forecast_uncertainty > 0.4:
            forecast_decisions.append(
                f"HIGH-UNCERTAINTY: u={meas.forecast_uncertainty:.2f}, "
                f"mode={self.state.dispatch_mode}, "
                f"reserve margin → {self.state.forecast_reserve_margin:.0%}"
            )

        # Store decisions for logging
        if forecast_decisions:
            outputs.info.extend(forecast_decisions)

        return outputs

    def _should_pre_charge(self, meas: MicrogridMeasurements) -> bool:
        """
        Determine if battery should pre-charge based on PV forecast.

        Triggers when:
        - 6h PV forecast is significantly lower than current PV
        - Battery SOC has room to charge
        - Grid is available (pre-charge from grid makes sense)
        """
        if meas.pv_forecast_6h is None:
            return False

        current_pv = meas.pv_actual_power_kw
        forecast_6h = meas.pv_forecast_6h

        # Trigger if 6h forecast is <50% of current PV and current PV is significant
        pv_drop = (current_pv > 50 and forecast_6h < current_pv * 0.5)

        # Also trigger if 1h forecast shows imminent drop
        if meas.pv_forecast_1h is not None:
            imminent_drop = (current_pv > 50 and meas.pv_forecast_1h < current_pv * 0.3)
            pv_drop = pv_drop or imminent_drop

        soc_has_room = meas.battery_soc_percent < 90

        return pv_drop and soc_has_room

    def _should_prepare_generator(self, meas: MicrogridMeasurements) -> bool:
        """
        Determine if generators should be prepared based on forecast.

        Triggers when:
        - 6h PV forecast is very low (cloudy period expected)
        - Battery SOC is below comfortable threshold
        - Extended low-PV period expected (24h forecast also low)
        """
        if meas.pv_forecast_6h is None:
            return False

        cloudy_6h = meas.pv_forecast_6h < 50  # Very low PV expected
        low_battery = meas.battery_soc_percent < 50

        # Extended low PV: check 24h horizon too
        extended_low = False
        if meas.pv_forecast_24h is not None:
            extended_low = meas.pv_forecast_24h < 100 and meas.pv_forecast_6h < 100

        return (cloudy_6h and low_battery) or (extended_low and low_battery)

    def _compute_reserve_margin(self, meas: MicrogridMeasurements) -> float:
        """
        Dynamic reserve margin incorporating:
          - Base margin (10%)
          - Forecast uncertainty (up to +40%)  ← strengthened
          - Evening peak period (up to +5%)
          - Low SoC penalty (up to +10%)
          - Conservative mode bonus (+5%)

        Returns
        -------
        float
            Reserve margin as fraction (e.g., 0.15 = 15%).
        """
        base_margin = 0.10
        uncertainty_margin = 0.0
        if meas.forecast_uncertainty is not None:
            uncertainty_margin = meas.forecast_uncertainty * 0.40

        # Evening peak = higher margin (18:00-22:00)
        peak_margin = 0.0
        if hasattr(meas, 'timestamp') and meas.timestamp is not None:
            hour = meas.timestamp.hour
            if 18 <= hour <= 22:
                peak_margin = 0.05

        # Low SoC penalty (strengthened)
        soc_margin = 0.0
        if meas.battery_soc_percent < 30:
            soc_margin = 0.10
        elif meas.battery_soc_percent < 50:
            soc_margin = 0.05

        # Conservative dispatch mode adds flat 5%
        mode_margin = 0.0
        if self.state.dispatch_mode == DispatchMode.CONSERVATIVE.value:
            mode_margin = 0.05

        return base_margin + uncertainty_margin + peak_margin + soc_margin + mode_margin

    def _select_dispatch_mode(self, meas: MicrogridMeasurements) -> None:
        """
        Auto-select dispatch mode from forecast uncertainty and SoC.

        Thresholds tuned so conservative mode triggers EARLY enough
        to prevent supply deficits (the root cause of LOLP > baseline).

        - CONSERVATIVE: uncertainty > 0.5 or SoC < 40%
        - AGGRESSIVE:   uncertainty < 0.15 and SoC > 80%
        - BALANCED:     otherwise
        """
        u = meas.forecast_uncertainty or 0.0
        soc = meas.battery_soc_percent

        if u > 0.5 or soc < 40:
            self.state.dispatch_mode = DispatchMode.CONSERVATIVE.value
        elif u < 0.15 and soc > 80:
            self.state.dispatch_mode = DispatchMode.AGGRESSIVE.value
        else:
            self.state.dispatch_mode = DispatchMode.BALANCED.value

    def _score_dispatch_option(
        self,
        gen_power_kw: float,
        battery_power_kw: float,
        grid_import_kw: float,
        shed_kw: float,
        duration_h: float = 1.0,
    ) -> EMSDispatchObjective:
        """
        Evaluate the approximate cost of a candidate dispatch.

        Parameters
        ----------
        gen_power_kw : float    Generator output (kW)
        battery_power_kw : float   Positive = discharge, Negative = charge
        grid_import_kw : float  Grid import (kW)
        shed_kw : float         Unserved load (kW)
        duration_h : float      Dispatch duration (default 1 h)

        Returns
        -------
        EMSDispatchObjective
        """
        obj = EMSDispatchObjective(
            fuel_cost=gen_power_kw * duration_h * self.cost_config.fuel_cost_per_kwh,
            battery_degradation_cost=(
                abs(battery_power_kw) * duration_h
                * self.cost_config.battery_degradation_cost_per_kwh
            ),
            grid_import_cost=(
                grid_import_kw * duration_h
                * self.cost_config.grid_import_cost_per_kwh
            ),
            load_shedding_penalty=(
                shed_kw * duration_h
                * self.cost_config.load_shedding_penalty_per_kwh
            ),
        )
        obj.compute_total()
        return obj
    
    # =========================================================================
    # DECISION LOGGING (Research Experiments)
    # =========================================================================
    
    def _log_decision(self, meas: MicrogridMeasurements,
                      outputs: EMSControlOutputs) -> None:
        """
        Log decision to attached EMSDecisionLogger, if any.
        
        Safe to call always — no-op if logger is None.
        """
        if self.decision_logger is None:
            return
        
        forecast_info = None
        if meas.pv_forecast_1h is not None:
            forecast_info = {
                'pv_1h': meas.pv_forecast_1h,
                'pv_6h': meas.pv_forecast_6h,
                'pv_24h': meas.pv_forecast_24h,
                'uncertainty': meas.forecast_uncertainty,
                'pre_charge_active': self.state.forecast_pre_charge_active,
                'gen_standby': self.state.forecast_gen_standby,
                'reserve_margin': self.state.forecast_reserve_margin,
            }
        
        # Extract forecast-related decisions from info
        forecast_reasons = [
            msg for msg in outputs.info
            if any(kw in msg for kw in ['Forecast', 'PRE-CHARGE', 'GEN-STANDBY',
                                        'GEN-EARLY-START', 'HIGH-UNCERTAINTY'])
        ]
        
        try:
            self.decision_logger.log_decision(
                timestamp=meas.timestamp,
                microgrid_type='hospital',
                operation_mode=outputs.operation_mode.value,
                battery_soc=meas.battery_soc_percent,
                battery_command_kw=outputs.battery_command.power_setpoint_kw,
                gen1_running=meas.gen1_running,
                gen2_running=meas.gen2_running,
                gen1_command_kw=outputs.gen1_command.power_setpoint_kw,
                gen2_command_kw=outputs.gen2_command.power_setpoint_kw,
                pv_power_kw=meas.pv_actual_power_kw,
                total_load_kw=meas.total_load_demand_kw,
                load_sheds=self.state.active_load_sheds,
                forecast_info=forecast_info,
                decision_reasons=forecast_reasons if forecast_reasons else None,
            )
        except Exception as e:
            # Logging must NEVER break the EMS
            logger.debug(f"Decision logging failed: {e}")
    
    def attach_decision_logger(self, decision_logger) -> None:
        """Attach an EMSDecisionLogger for research experiment logging."""
        self.decision_logger = decision_logger
        logger.info("Decision logger attached to Hospital EMS")
