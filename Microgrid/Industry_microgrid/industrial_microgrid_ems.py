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
    Hospital Energy Management System
    Priority: ALWAYS protect 320 kW critical loads
    """
    
    def __init__(self, config, battery, pv, generator1, generator2, load):
        self.config = config
        self.battery = battery
        self.pv = pv
        self.generator1 = generator1  # Dedicated to critical loads
        self.generator2 = generator2  # Serves non-critical + backup
        self.load = load
        
        # Generator runtime tracking
        self.gen1_on_minutes = 0
        self.gen1_off_minutes = 9999
        self.gen2_on_minutes = 0
        self.gen2_off_minutes = 9999
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
        self.reconnection_timer = 0
        self.reconnection_delay = config.control.reconnection_delay_seconds
        
        # Restoration policy
        self.restore_time_min = config.control.restore_time_min
        self.restore_margin_ratio = config.control.restore_margin_ratio
        
        # Event tracking
        self.events = []
        
    def detect_grid_fault(self) -> bool:
        """Detect grid fault conditions"""
        if (self.frequency_hz > self.config.protection.over_frequency_hz or 
            self.frequency_hz < self.config.protection.under_frequency_hz):
            return True
        
        if (self.voltage_pu > self.config.protection.over_voltage_pu or 
            self.voltage_pu < self.config.protection.under_voltage_pu):
            return True
        
        return False
    
    def check_reconnection_conditions(self) -> bool:
        """Check if conditions suitable for reconnection"""
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
        
        # Battery has sufficient charge (hospital: 40% minimum)
        if self.battery.soc_percent < 40:
            return False
        
        return True
    
    def primary_control_grid_forming(self, dt_seconds: float) -> Tuple[float, float]:
        """Primary control in islanded mode (grid-forming)"""
        total_gen = self.pv.power_kw + self.generator1.power_kw + self.generator2.power_kw
        total_load = self.load.total_load_kw
        power_imbalance = total_gen - total_load
        
        # Frequency droop
        freq_droop = self.config.control.frequency_droop_coefficient
        delta_f = -freq_droop * power_imbalance / self.config.load_profile.peak_load
        self.frequency_hz = self.config.nominal_frequency_hz + delta_f
        
        # Voltage droop
        voltage_droop = self.config.control.voltage_droop_coefficient
        delta_v = -voltage_droop * power_imbalance / self.config.load_profile.peak_load
        self.voltage_pu = 1.0 + delta_v
        
        # Clamp to limits
        self.frequency_hz = np.clip(self.frequency_hz, 49.5, 50.5)
        self.voltage_pu = np.clip(self.voltage_pu, 0.95, 1.05)
        
        return self.frequency_hz, self.voltage_pu
    
    def secondary_control_restoration(self, dt_seconds: float):
        """Secondary control: restore frequency and voltage to nominal"""
        if self.mode != OperationMode.ISLANDED:
            return
        
        restoration_rate = 0.01
        
        freq_error = self.config.nominal_frequency_hz - self.frequency_hz
        self.frequency_hz += freq_error * restoration_rate * dt_seconds
        
        voltage_error = 1.0 - self.voltage_pu
        self.voltage_pu += voltage_error * restoration_rate * dt_seconds
    
    def load_shedding_strategy(self, power_deficit_kw: float) -> float:
        """
        Hospital load shedding: NEVER shed critical loads
        Priority: ADMIN → WARDS_LIGHTING → HVAC
        """
        if power_deficit_kw <= 0:
            return 0
        
        total_shed = 0
        
        # Shed order: lowest priority first (protect critical at all costs)
        load_tiers = ["ADMIN", "WARDS_LIGHTING", "HVAC"]
        
        for tier in load_tiers:
            if power_deficit_kw <= 0:
                break
            
            shed = self.load.shed_tier(tier, power_deficit_kw)
            if shed > 0:
                self.log_event(
                    "load_shed",
                    f"🏥 HOSPITAL SHED: {shed:.1f} kW from {tier} (Critical loads protected)"
                )
            
            power_deficit_kw -= shed
            total_shed += shed
        
        # If still deficit, log critical warning
        if power_deficit_kw > 0:
            self.log_event(
                "critical_warning",
                f"⚠️ WARNING: Cannot shed more. Deficit {power_deficit_kw:.1f} kW remains. Critical loads at risk!"
            )
        
        return total_shed
    
    def load_restoration_strategy(self, power_surplus_kw: float) -> float:
        """
        Restore loads gradually (reverse priority)
        Hospital: Restore HVAC → WARDS_LIGHTING → ADMIN
        """
        if power_surplus_kw <= 100:
            return 0
        
        total_restored = 0
        restore_tiers = ["HVAC", "WARDS_LIGHTING", "ADMIN"]
        
        for tier in restore_tiers:
            if power_surplus_kw <= 100:
                break
            
            restored = self.load.restore_tier(tier, power_surplus_kw - 100)
            if restored > 0:
                self.log_event(
                    "load_restore",
                    f"🏥 Restored {restored:.1f} kW to {tier}"
                )
            
            power_surplus_kw -= restored
            total_restored += restored
        
        return total_restored
    
    def battery_dispatch_strategy(self, power_balance_kw: float, dt_hours: float) -> float:
        """Battery charge/discharge control"""
        if power_balance_kw < -10:  # Deficit
            required_discharge = abs(power_balance_kw)
            actual_discharge = self.battery.discharge(required_discharge, dt_hours)
            return -actual_discharge
        
        elif power_balance_kw > 50:  # Surplus
            available_charge = power_balance_kw - 20
            actual_charge = self.battery.charge(available_charge, dt_hours)
            return actual_charge
        
        return 0
    
    def generator_dispatch_strategy(self):
        """
        Dual generator control for hospital
        Generator 1: Critical loads (auto-start at 25% SoC)
        Generator 2: Non-critical loads (start at 20% SoC or high load)
        """
        from Microgrid.Industry_microgrid.industrial_microgrid_component import ComponentState
        
        soc = self.battery.soc_percent
        
        # GENERATOR 1 (Critical loads - higher priority)
        if self.generator1.state == ComponentState.OFF:
            if (self.mode == OperationMode.ISLANDED and 
                soc <= self.generator1.config.auto_start_soc_threshold and
                self.gen1_off_minutes >= self.generator1.config.min_off_time_minutes):
                
                self.generator1.start()
                self.log_event('gen1_start', f'🏥 GEN1 (Critical) started - SoC {soc:.1f}%')
        
        elif self.generator1.state == ComponentState.RUNNING:
            if (soc >= self.generator1.config.auto_stop_soc_threshold and
                self.gen1_on_minutes >= self.generator1.config.min_on_time_minutes and
                (self.grid_available or (self.mode == OperationMode.ISLANDED and self.pv.power_kw > 200))):
                
                self.generator1.stop()
                self.log_event('gen1_stop', f'🏥 GEN1 stopped - SoC {soc:.1f}%')
        
        # GENERATOR 2 (Non-critical loads + backup)
        if self.generator2.state == ComponentState.OFF:
            # Start if battery very low OR high non-critical load during outage
            if (self.mode == OperationMode.ISLANDED and 
                ((soc <= 20) or (self.load.non_critical_load_kw > 200 and soc < 50)) and
                self.gen2_off_minutes >= self.generator2.config.min_off_time_minutes):
                
                self.generator2.start()
                self.log_event('gen2_start', f'🏥 GEN2 (Non-critical) started - SoC {soc:.1f}%')
        
        elif self.generator2.state == ComponentState.RUNNING:
            if (soc >= 75 and
                self.gen2_on_minutes >= self.generator2.config.min_on_time_minutes and
                self.load.non_critical_load_kw < 150):
                
                self.generator2.stop()
                self.log_event('gen2_stop', f'🏥 GEN2 stopped')
    
    def calculate_power_balance(self) -> float:
        """Calculate current power balance"""
        generation = self.pv.power_kw + self.generator1.power_kw + self.generator2.power_kw
        
        if self.mode == OperationMode.GRID_CONNECTED:
            generation += self.grid_power_kw
        
        return generation - self.load.total_load_kw
    
    def transition_to_island(self):
        """Execute transition to islanded mode"""
        self.mode = OperationMode.TRANSITION_TO_ISLAND
        self.grid_available = False
        self.grid_power_kw = 0
        
        self.log_event('islanding', '🏥 HOSPITAL ISLANDING - Protecting critical loads')
        
        # Immediate power balance check
        power_balance = self.calculate_power_balance()
        
        if power_balance < 0:
            self.log_event('power_deficit', f'Power deficit: {abs(power_balance):.1f} kW')
            self.load_shedding_strategy(abs(power_balance))
        
        self.mode = OperationMode.ISLANDED
        self.log_event('islanded', '🏥 Hospital microgrid islanded - Critical loads protected')
    
    def transition_to_grid(self):
        """Execute transition to grid-connected"""
        self.mode = OperationMode.TRANSITION_TO_GRID
        self.reconnection_timer = self.reconnection_delay
        self.log_event('reconnection_initiated', f'Waiting {self.reconnection_delay}s before reconnection')
    
    def complete_reconnection(self):
        """Complete reconnection to grid"""
        self.mode = OperationMode.GRID_CONNECTED
        self.grid_available = True
        
        # Restore all shed loads
        if self.load.shed_load_kw > 0:
            self.load.restore_load()
            self.log_event('load_restore', '🏥 All loads restored after grid reconnection')
        
        self.log_event('grid_connected', '🏥 Hospital reconnected to grid')
    
    def update(self, timestamp, dt_seconds: float, simulated_grid_available: bool = True,
               grid_frequency_hz: float = None, grid_voltage_pu: float = None) -> EMSState:
        """Main EMS update loop"""
        dt_hours = dt_seconds / 3600
        dt_minutes = dt_seconds / 60
        
        # Update grid parameters
        if grid_frequency_hz is not None:
            self.frequency_hz = grid_frequency_hz
        if grid_voltage_pu is not None:
            self.voltage_pu = grid_voltage_pu
        
        self.grid_available = simulated_grid_available
        
        # Mode transitions
        islanding_event = False
        reconnection_event = False
        
        if self.mode == OperationMode.GRID_CONNECTED:
            if not self.grid_available or self.detect_grid_fault():
                self.transition_to_island()
                islanding_event = True
        
        elif self.mode == OperationMode.ISLANDED:
            if self.check_reconnection_conditions():
                self.transition_to_grid()
        
        elif self.mode == OperationMode.TRANSITION_TO_GRID:
            self.reconnection_timer -= dt_seconds
            if self.reconnection_timer <= 0:
                self.complete_reconnection()
                reconnection_event = True
        
        # Control based on mode
        if self.mode == OperationMode.ISLANDED:
            self.primary_control_grid_forming(dt_seconds)
            self.secondary_control_restoration(dt_seconds)
        
        # Update generator runtime trackers
        if self.generator1.is_running:
            self.gen1_on_minutes += dt_minutes
            self.gen1_off_minutes = 0
        else:
            self.gen1_off_minutes += dt_minutes
            self.gen1_on_minutes = 0
        
        if self.generator2.is_running:
            self.gen2_on_minutes += dt_minutes
            self.gen2_off_minutes = 0
        else:
            self.gen2_off_minutes += dt_minutes
            self.gen2_on_minutes = 0
        
        # Generator dispatch
        self.generator_dispatch_strategy()
        
        # Power balance
        power_balance = self.calculate_power_balance()
        
        # Battery dispatch
        battery_power = self.battery_dispatch_strategy(power_balance, dt_hours)
        power_balance += battery_power
        
        # Restoration logic
        restore_margin_kw = self.restore_margin_ratio * self.load.critical_load_kw
        
        if (power_balance > restore_margin_kw and self.battery.soc_percent > 60):
            self.restore_stable_minutes += dt_minutes
        else:
            self.restore_stable_minutes = 0
        
        if (self.restore_stable_minutes >= self.restore_time_min and self.load.shed_load_kw > 0):
            self.load_restoration_strategy(power_balance)
        
        # Grid power (in grid-connected mode)
        if self.mode == OperationMode.GRID_CONNECTED:
            self.grid_power_kw = (self.load.total_load_kw - self.pv.power_kw - 
                                 self.generator1.power_kw - self.generator2.power_kw + 
                                 self.battery.power_kw)
        else:
            self.grid_power_kw = 0
        
        # Build state
        state = EMSState(
            mode=self.mode,
            grid_available=self.grid_available,
            frequency_hz=self.frequency_hz,
            voltage_pu=self.voltage_pu,
            total_generation_kw=self.pv.power_kw + self.generator1.power_kw + self.generator2.power_kw + self.grid_power_kw,
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