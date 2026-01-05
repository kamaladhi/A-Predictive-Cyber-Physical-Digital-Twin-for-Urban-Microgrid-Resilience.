
import numpy as np
from enum import Enum
from typing import Dict, List, Tuple
from dataclasses import dataclass
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OperationMode(Enum):
    """Microgrid operation modes"""
    GRID_CONNECTED = 1
    ISLANDED = 2
    TRANSITION_TO_ISLAND = 3
    TRANSITION_TO_GRID = 4


@dataclass
class EMSState:
    """EMS state information"""
    
    mode: OperationMode
    grid_available: bool
    frequency_hz: float
    voltage_pu: float
    total_generation_kw: float
    total_load_kw: float
    power_balance_kw: float
    islanding_event: bool = False
    reconnection_event: bool = False


class EnergyManagementSystem:
    """
    Main EMS controller implementing hierarchical control:
    - Primary control (voltage/frequency)
    - Secondary control (restoration)
    - Tertiary control (optimization)
    """
    
    def __init__(self, config, battery, pv, generator, load):
        self.config = config
        self.battery = battery
        self.pv = pv
        self.generator = generator
        self.load = load
                # Generator runtime tracking (EMS responsibility)
        self.generator_on_minutes = 0
        self.generator_off_minutes = 9999  # assume generator has been OFF for long
        self.restore_stable_minutes = 0

        # State variables
        self.mode = OperationMode.GRID_CONNECTED
        self.grid_available = True
        self.grid_power_kw = 0
        
        # Grid parameters
        self.frequency_hz = config.nominal_frequency_hz
        self.voltage_pu = 1.0
        
        # Control state
        self.last_mode = self.mode
        #self.islanding_timer = 0
        self.reconnection_timer = 0
        self.reconnection_delay = config.control.reconnection_delay_seconds
        
    
        #self.restore_stable_minutes = 0
        self.restore_time_min = 10
        self.restore_margin_ratio = 0.25

        # Event tracking
        self.events = []
        
    def detect_grid_fault(self) -> bool:
        """
        Detect grid fault conditions
        Returns True if grid is faulted
        """
        # Frequency out of bounds
        if (self.frequency_hz > self.config.protection.over_frequency_hz or 
            self.frequency_hz < self.config.protection.under_frequency_hz):
            return True
        
        # Voltage out of bounds
        if (self.voltage_pu > self.config.protection.over_voltage_pu or 
            self.voltage_pu < self.config.protection.under_voltage_pu):
            return True
        
        return False
    
    def check_reconnection_conditions(self) -> bool:
        """
        Check if conditions are suitable for reconnection
        """
        if not self.grid_available:
            return False
        
        # Voltage within window
        v_min, v_max = self.config.protection.reconnection_voltage_window_pu
        if not (v_min <= self.voltage_pu <= v_max):
            return False
        
        # Frequency within window
        f_min, f_max = self.config.protection.reconnection_frequency_window_hz
        if not (f_min <= self.frequency_hz <= f_max):
            return False
        
        # Battery has sufficient charge
        if self.battery.soc_percent < 30:
            return False
        
        return True
    
    def primary_control_grid_forming(self, dt_seconds: float) -> Tuple[float, float]:
        """
        Primary control in islanded mode (grid-forming)
        Maintains voltage and frequency using droop control
        Returns (frequency, voltage) setpoints
        """
        # Power balance
        total_gen = self.pv.power_kw + self.generator.power_kw
        total_load = self.load.total_load_kw
        power_imbalance = total_gen - total_load
        
        # Frequency droop: Δf = -K_f × ΔP
        freq_droop = self.config.control.frequency_droop_coefficient
        delta_f = -freq_droop * power_imbalance / self.config.load_profile.peak_load
        
        self.frequency_hz = self.config.nominal_frequency_hz + delta_f
        
        # Voltage droop: ΔV = -K_v × ΔQ (simplified, assume unity power factor)
        voltage_droop = self.config.control.voltage_droop_coefficient
        delta_v = -voltage_droop * power_imbalance / self.config.load_profile.peak_load
        
        self.voltage_pu = 1.0 + delta_v
        
        # Clamp to reasonable limits
        self.frequency_hz = np.clip(self.frequency_hz, 49.5, 50.5)
        self.voltage_pu = np.clip(self.voltage_pu, 0.95, 1.05)
        
        return self.frequency_hz, self.voltage_pu
    
    def secondary_control_restoration(self, dt_seconds: float):
        """
        Secondary control: restore frequency and voltage to nominal
        """
        if self.mode != OperationMode.ISLANDED:
            return
        
        # Slow restoration to nominal values
        restoration_rate = 0.01  # per second
        
        freq_error = self.config.nominal_frequency_hz - self.frequency_hz
        self.frequency_hz += freq_error * restoration_rate * dt_seconds
        
        voltage_error = 1.0 - self.voltage_pu
        self.voltage_pu += voltage_error * restoration_rate * dt_seconds
    
    def load_shedding_strategy(self, power_deficit_kw: float) -> float:
        """
        Tiered load shedding strategy
        Priority: HVAC → LABS → LIGHTING
        """
        if power_deficit_kw <= 0:
            return 0

        total_shed = 0

        # Tier order: lowest priority first
        load_tiers = ["HVAC", "LABS", "LIGHTING"]

        for tier in load_tiers:
            if power_deficit_kw <= 0:
                break

            shed = self.load.shed_tier(tier, power_deficit_kw)
            if shed > 0:
                self.log_event(
                    "load_shed",
                    f"Shed {shed:.1f} kW from {tier}"
                )

            power_deficit_kw -= shed
            total_shed += shed

        return total_shed

    
    def load_restoration_strategy(self, power_surplus_kw: float) -> float:
        if power_surplus_kw <= 100:
            return 0

        total_restored = 0
        restore_tiers = ["LIGHTING", "LABS", "HVAC"]  # reverse order

        for tier in restore_tiers:
            if power_surplus_kw <= 100:
                break

            restored = self.load.restore_tier(tier, power_surplus_kw - 100)
            if restored > 0:
                self.log_event(
                    "load_restore",
                    f"Restored {restored:.1f} kW to {tier}"
                )

            power_surplus_kw -= restored
            total_restored += restored

        return total_restored


    
    def battery_dispatch_strategy(self, power_balance_kw: float, dt_hours: float) -> float:
        """
        Determine battery charge/discharge based on power balance
        Returns actual battery power (positive = charge, negative = discharge)
        """
        if power_balance_kw < -10:  # Deficit, discharge battery
            required_discharge = abs(power_balance_kw)
            actual_discharge = self.battery.discharge(required_discharge, dt_hours)
            return -actual_discharge
        
        elif power_balance_kw > 50:  # Surplus, charge battery
            available_charge = power_balance_kw - 20  # Keep margin
            actual_charge = self.battery.charge(available_charge, dt_hours)
            return actual_charge
        
        return 0
    
    def generator_dispatch_strategy(self):
        from Microgrid.university_microgrid.microgrid_components import ComponentState

        soc = self.battery.soc_percent

        # START generator (ONLY based on SoC + min OFF time)
        if self.generator.state == ComponentState.OFF:
            if (self.mode == OperationMode.ISLANDED and soc <= self.generator.config.auto_start_soc_threshold and
                self.generator_off_minutes >= self.generator.config.min_off_time_minutes
            ):
                self.generator.start()
                self.log_event('generator_start',f'Started due to low SoC {soc:.1f}%')

        # STOP generator (ONLY after min ON time)
        elif self.generator.state == ComponentState.RUNNING:
            if (
                soc >= self.generator.config.auto_stop_soc_threshold and
                self.generator_on_minutes >= self.generator.config.min_on_time_minutes and
                (
                    self.grid_available or
                    (
                        self.mode == OperationMode.ISLANDED and
                        self.pv.power_kw > self.load.total_load_kw and
                        soc >= 70
                    )
                )
            ):

                self.generator.stop()
                self.log_event(
                    'generator_stop',
                    f'Stopped after min runtime, SoC {soc:.1f}%'
                )


    def calculate_power_balance(self) -> float:
        """
        Calculate current power balance
        Positive = surplus, Negative = deficit
        """
        generation = self.pv.power_kw + self.generator.power_kw
        
        if self.mode == OperationMode.GRID_CONNECTED:
            generation += self.grid_power_kw
        
        # Don't count battery in generation (it's balanced separately)
        return generation - self.load.total_load_kw
    
    def transition_to_island(self):
        """Execute transition from grid-connected to islanded"""
        self.mode = OperationMode.TRANSITION_TO_ISLAND
        self.grid_available = False
        self.grid_power_kw = 0
        
        self.log_event('islanding', 'Transitioning to islanded mode')
        
        # Immediate actions
        # 1. Battery takes over as grid-forming
        # 2. Check power balance and shed if needed
        power_balance = self.calculate_power_balance()
        
        if power_balance < 0:
            self.log_event('power_deficit', f'Power deficit: {abs(power_balance):.1f} kW')
            self.load_shedding_strategy(abs(power_balance))
        
        # Complete transition
        self.mode = OperationMode.ISLANDED
        self.log_event('islanded', 'Islanded mode active')
    
    def transition_to_grid(self):
        """Execute transition from islanded to grid-connected"""
        self.mode = OperationMode.TRANSITION_TO_GRID
        self.reconnection_timer = self.reconnection_delay
        
        self.log_event('reconnection_initiated', f'Waiting {self.reconnection_delay}s before reconnection')
    
    def complete_reconnection(self):
        """Complete reconnection to grid"""
        self.mode = OperationMode.GRID_CONNECTED
        self.grid_available = True
        
        # Restore any shed loads
        if self.load.shed_load_kw > 0:
            self.load.restore_load()
            self.log_event('load_restore', 'All shed loads restored after reconnection')
        
        self.log_event('grid_connected', 'Grid-connected mode active')
    
    def update(self, timestamp, dt_seconds: float, simulated_grid_available: bool = True,
               grid_frequency_hz: float = None,grid_voltage_pu: float = None) -> EMSState:
        """
        Main EMS update loop
        """
        dt_hours = dt_seconds / 3600
        dt_minutes = dt_seconds / 60

        
        # Update grid parameters (from external simulation or defaults)
        if grid_frequency_hz is not None:
            self.frequency_hz = grid_frequency_hz
        if grid_voltage_pu is not None:
            self.voltage_pu = grid_voltage_pu
        
        self.grid_available = simulated_grid_available
        
        # Mode transitions
        islanding_event = False
        reconnection_event = False
        
        if self.mode == OperationMode.GRID_CONNECTED:
            # Check for grid fault or simulated outage
            if not self.grid_available or self.detect_grid_fault():
                self.transition_to_island()
                islanding_event = True
        
        elif self.mode == OperationMode.ISLANDED:
            # Check for reconnection conditions
            if self.check_reconnection_conditions():
                self.transition_to_grid()
        
        elif self.mode == OperationMode.TRANSITION_TO_GRID:
            self.reconnection_timer -= dt_seconds
            if self.reconnection_timer <= 0:
                self.complete_reconnection()
                reconnection_event = True
        
        # Control based on mode
        if self.mode == OperationMode.ISLANDED:
            # Primary control (grid-forming)
            self.primary_control_grid_forming(dt_seconds)
            
            # Secondary control (restoration)
            self.secondary_control_restoration(dt_seconds)
        
        # Generator dispatch
        if self.generator.is_running:
            self.generator_on_minutes += dt_minutes
            self.generator_off_minutes = 0
        else:
            self.generator_off_minutes += dt_minutes
            self.generator_on_minutes = 0

        # Decide generator start/stop AFTER updating timers
        self.generator_dispatch_strategy()

        
        # Calculate power balance
        power_balance = self.calculate_power_balance()
        
        # Battery dispatch
        battery_power = self.battery_dispatch_strategy(power_balance, dt_hours)
        
        # Recalculate after battery action
        power_balance += battery_power

        
        restore_margin_kw = self.restore_margin_ratio * self.load.critical_load_kw


        if (
            power_balance > restore_margin_kw and
            self.battery.soc_percent > 60
        ):
            self.restore_stable_minutes += dt_minutes
        else:
            self.restore_stable_minutes = 0

        if (self.restore_stable_minutes >= self.restore_time_min and self.load.shed_load_kw > 0 ):
            self.load_restoration_strategy(power_balance)

                
        # Grid power (in grid-connected mode)
        if self.mode == OperationMode.GRID_CONNECTED:
            # Grid absorbs/provides the balance
            self.grid_power_kw = self.load.total_load_kw - self.pv.power_kw - self.generator.power_kw + self.battery.power_kw
        else:
            self.grid_power_kw = 0
        
        # Build state
        state = EMSState(
            mode=self.mode,
            grid_available=self.grid_available,
            frequency_hz=self.frequency_hz,
            voltage_pu=self.voltage_pu,
            total_generation_kw=self.pv.power_kw + self.generator.power_kw + self.grid_power_kw,
            total_load_kw=self.load.total_load_kw,
            power_balance_kw=self.calculate_power_balance(),
            islanding_event=islanding_event,
            reconnection_event=reconnection_event
        )
        
        return state
    
    def log_event(self, event_type: str, message: str):
        """Log an event"""
        event = {
            'type': event_type,
            'message': message
        }
        self.events.append(event)
        logger.info(f"[{event_type}] {message}")
    
    def get_events(self) -> List[dict]:
        """Get and clear event log"""
        events = self.events.copy()
        self.events.clear()
        return events