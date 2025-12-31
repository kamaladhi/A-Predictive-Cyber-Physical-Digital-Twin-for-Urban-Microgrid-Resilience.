import numpy as np
from dataclasses import dataclass
from typing import Tuple, Optional
from enum import Enum


class ComponentState(Enum):
    """Component operational states"""
    OFF = 0
    STARTING = 1
    RUNNING = 2
    STOPPING = 3
    FAULT = 4


@dataclass
class ComponentStatus:
    """Status information for any component"""
    state: ComponentState
    power_kw: float
    energy_kwh: float = 0
    efficiency: float = 1.0
    fault_code: Optional[str] = None


class Battery:
    """Battery Energy Storage System model"""
    
    def __init__(self, config):
        self.config = config
        self.soc_percent = config.initial_soc_percent
        
        #self.energy_kwh = self.soc_percent / 100 * config.nominal_capacity_kwh
        self.energy_kwh = self.soc_percent / 100 * self.config.usable_capacity_kwh

        self.power_kw = 0
        self.cumulative_throughput_kwh = 0
        self.cycle_count = 0
        self.temperature_c = 25
        self.state = ComponentState.RUNNING
        
    def get_available_discharge_power(self) -> float:
        """Calculate maximum available discharge power"""
        if self.soc_percent <= self.config.min_soc_percent:
            return 0
        
        # Power limited by SoC and C-rate
        energy_available = (self.soc_percent - self.config.min_soc_percent) / 100 * self.config.usable_capacity_kwh
        time_limited_power = energy_available / 0.25  # Can discharge to min in 15 min
        
        return min(self.config.max_discharge_power_kw, time_limited_power)
    
    def get_available_charge_power(self) -> float:
        """Calculate maximum available charge power"""
        if self.soc_percent >= self.config.max_soc_percent:
            return 0
        
        # Power limited by SoC and C-rate
        energy_capacity = (self.config.max_soc_percent - self.soc_percent) / 100 * self.config.usable_capacity_kwh
        time_limited_power = energy_capacity / 0.25  # Can charge to max in 15 min
        
        return min(self.config.max_charge_power_kw, time_limited_power)
    
    def discharge(self, power_kw: float, dt_hours: float) -> float:
        """
        Discharge battery for given power and duration
        Returns actual power delivered (may be less than requested)
        """
        max_power = self.get_available_discharge_power()
        actual_power = min(power_kw, max_power)
        
        if actual_power <= 0:
            self.power_kw = 0
            return 0
        
        # Energy calculation with efficiency
        energy_from_battery = actual_power * dt_hours / self.config.discharge_efficiency
        
        # Update state
        self.energy_kwh -= energy_from_battery
        #self.soc_percent = (self.energy_kwh / self.config.nominal_capacity_kwh) * 100
        self.soc_percent = (self.energy_kwh / self.config.usable_capacity_kwh) * 100
        self.soc_percent = np.clip(self.soc_percent,self.config.min_soc_percent,self.config.max_soc_percent)


        self.power_kw = -actual_power  # Negative for discharge
        self.cumulative_throughput_kwh += energy_from_battery
        
        # Simple thermal model
        self.temperature_c += actual_power * 0.001  # Simplified heating
        
        return actual_power
    
    def charge(self, power_kw: float, dt_hours: float) -> float:
        """
        Charge battery for given power and duration
        Returns actual power consumed (may be less than available)
        """
        max_power = self.get_available_charge_power()
        actual_power = min(power_kw, max_power)
        
        if actual_power <= 0:
            self.power_kw = 0
            return 0
        
        # Energy calculation with efficiency
        energy_to_battery = actual_power * dt_hours * self.config.charge_efficiency
        
        # Update state
        self.energy_kwh += energy_to_battery
        #self.soc_percent = (self.energy_kwh / self.config.nominal_capacity_kwh) * 100
        self.soc_percent = (self.energy_kwh / self.config.usable_capacity_kwh) * 100
        self.soc_percent = np.clip(self.soc_percent,self.config.min_soc_percent,self.config.max_soc_percent)


        self.power_kw = actual_power  # Positive for charge
        
        # Thermal model
        self.temperature_c += actual_power * 0.0008
        
        return actual_power
    
    def get_status(self) -> ComponentStatus:
        """Get current battery status"""
        return ComponentStatus(
            state=self.state,
            power_kw=self.power_kw,
            energy_kwh=self.energy_kwh,
            efficiency=self.config.round_trip_efficiency
        )


class PVArray:
    """Solar PV array model"""
    
    def __init__(self, config):
        self.config = config
        self.power_kw = 0
        self.daily_energy_kwh = 0
        self.cumulative_energy_kwh = 0
        self.curtailment_kw = 0
        self.state = ComponentState.RUNNING
        
    def calculate_generation(self, timestamp, irradiance_w_m2: float = None, 
                           ambient_temp_c: float = 25) -> float:
        """
        Calculate PV generation based on time and conditions
        If irradiance not provided, uses simplified model based on time
        """
        hour = timestamp.hour + timestamp.minute / 60
        
        if irradiance_w_m2 is None:
            # Simplified sinusoidal model for irradiance
            if 6 <= hour <= 18:
                # Peak at solar noon (12:00)
                hour_angle = (hour - 12) / 6 * np.pi / 2
                irradiance_w_m2 = 1000 * np.cos(hour_angle) ** 2
            else:
                irradiance_w_m2 = 0
        
        # Standard test conditions: 1000 W/m²
        stc_irradiance = 1000
        
        # Temperature derating
        cell_temp = ambient_temp_c + (self.config.nominal_operating_temp - 20) * (irradiance_w_m2 / stc_irradiance)
        temp_factor = 1 + self.config.temperature_coefficient * (cell_temp - 25)
        
        # Power calculation
        self.power_kw = (
            self.config.installed_capacity_kwp * 
            (irradiance_w_m2 / stc_irradiance) * 
            temp_factor * 
            self.config.inverter_efficiency
        )
        
        self.power_kw = max(0, self.power_kw)  # No negative generation
        
        return self.power_kw
    
    def curtail(self, curtail_kw: float):
        """Apply curtailment to PV output"""
        self.curtailment_kw = min(curtail_kw, self.power_kw)
        self.power_kw -= self.curtailment_kw
    
    def update_energy(self, dt_hours: float):
        """Update energy counters"""
        energy_increment = self.power_kw * dt_hours
        self.daily_energy_kwh += energy_increment
        self.cumulative_energy_kwh += energy_increment
    
    def reset_daily_energy(self):
        """Reset daily energy counter (call at midnight)"""
        self.daily_energy_kwh = 0
    
    def get_status(self) -> ComponentStatus:
        """Get current PV status"""
        return ComponentStatus(
            state=self.state,
            power_kw=self.power_kw,
            energy_kwh=self.cumulative_energy_kwh,
            efficiency=self.config.inverter_efficiency
        )


class Generator:
    """Diesel generator model"""
    
    def __init__(self, config):
        self.config = config
        self.state = ComponentState.OFF
        self.power_kw = 0
        self.fuel_consumed_liters = 0
        self.runtime_hours = 0
        self.startup_timer = 0
        self.cooldown_timer = 0
        self.start_count = 0
        self.last_start_time = None
        self.last_stop_time = None
        
    def can_start(self, now, config):
        if self.state == ComponentState.RUNNING:
            return False
        if self.last_stop_time is None:
            return True
        elapsed = (now - self.last_stop_time).total_seconds() / 60
        return elapsed >= config.min_off_time_minutes

    def can_stop(self, now, config):
        if self.state != ComponentState.RUNNING:
            return False
        if self.last_start_time is None:
            return True
        elapsed = (now - self.last_start_time).total_seconds() / 60
        return elapsed >= config.min_on_time_minutes

        
    def start(self):
        """Initiate generator startup"""
        if self.state == ComponentState.OFF:
            self.state = ComponentState.STARTING
            self.startup_timer = self.config.startup_time_seconds
            self.start_count += 1
    
    def stop(self):
        """Initiate generator shutdown"""
        if self.state == ComponentState.RUNNING:
            self.state = ComponentState.STOPPING
            self.cooldown_timer = self.config.cooldown_time_seconds
    
    def update(self, dt_seconds: float, load_demand_kw: float = None) -> float:
        """
        Update generator state
        Returns actual power output
        """
        if self.state == ComponentState.STARTING:
            self.startup_timer -= dt_seconds
            if self.startup_timer <= 0:
                self.state = ComponentState.RUNNING
                self.power_kw = self.config.min_operating_power_kw
            return 0
        
        elif self.state == ComponentState.STOPPING:
            self.cooldown_timer -= dt_seconds
            # Ramp down power
            ramp_rate = self.power_kw / self.config.cooldown_time_seconds
            self.power_kw = max(0, self.power_kw - ramp_rate * dt_seconds)
            
            if self.cooldown_timer <= 0:
                self.state = ComponentState.OFF
                self.power_kw = 0
            return self.power_kw
        
        elif self.state == ComponentState.RUNNING:
            if load_demand_kw is not None:
                # Set power based on demand, respecting limits
                target_power = np.clip(
                    load_demand_kw,
                    self.config.min_operating_power_kw,
                    self.config.rated_power_kw
                )
                
                # Ramp rate limit: 10% per second
                max_ramp = self.config.rated_power_kw * 0.1 * dt_seconds
                power_change = target_power - self.power_kw
                
                if abs(power_change) > max_ramp:
                    power_change = max_ramp * np.sign(power_change)
                
                self.power_kw += power_change
            
            # Update fuel consumption (simplified model)
            dt_hours = dt_seconds / 3600
            fuel_rate = self.config.fuel_consumption_l_per_kwh
            # Fuel consumption increases at low load
            load_factor = self.power_kw / self.config.rated_power_kw
            efficiency_factor = 0.7 + 0.3 * load_factor  # Worse efficiency at low load
            
            self.fuel_consumed_liters += (self.power_kw * dt_hours * fuel_rate / efficiency_factor)
            self.runtime_hours += dt_hours
            
            return self.power_kw
        
        return 0
    
    @property
    def is_running(self) -> bool:
        """Convenience boolean indicating generator running state"""
        return self.state == ComponentState.RUNNING

    def get_status(self) -> ComponentStatus:
        """Get current generator status"""
        return ComponentStatus(
            state=self.state,
            power_kw=self.power_kw,
            energy_kwh=self.runtime_hours * self.power_kw
        )


class Load:
    """Campus load model"""
    
    def __init__(self, config):
        self.config = config

        # Aggregate loads
        self.total_load_kw = 0
        self.critical_load_kw = config.load_profile.total_critical_load
        self.non_critical_load_kw = 0
        self.shed_load_kw = 0

        # Tiered non-critical loads (INITIAL VALUES)
        self.tier_loads = {
            "HVAC": config.load_profile.hvac_load_kw,
            "LABS": config.load_profile.labs_load_kw,
            "LIGHTING": config.load_profile.lighting_load_kw
        }

        # Keep originals for restoration
        self.original_tier_loads = self.tier_loads.copy()

        self.cumulative_energy_kwh = 0
        self.load_factor = 1.0
        
    def update_load(self, timestamp) -> Tuple[float, float]:
        """
        Update load based on time-of-day profile
        Returns (total_load, critical_load)
        """
        hour = timestamp.hour + timestamp.minute / 60
        
        # Find current load block
        base_load = 350  # Default
        for start_h, end_h, load_kw in self.config.load_profile.hour_blocks:
            if start_h <= hour < end_h:
                base_load = load_kw
                break
        
        # Add some realistic variation (±5%)
        #np.random.seed(int(timestamp.timestamp()))
        variation = np.random.uniform(0.95, 1.05)
        
        self.total_load_kw = base_load * variation * self.load_factor
        self.non_critical_load_kw = max(0, self.total_load_kw - self.critical_load_kw)
        total_tier_capacity = sum(self.original_tier_loads.values())

        if total_tier_capacity > 0:
            scale = self.non_critical_load_kw / total_tier_capacity
            for tier in self.tier_loads:
                self.tier_loads[tier] = self.original_tier_loads[tier] * scale
        
        return self.total_load_kw, self.critical_load_kw
    
    def shed_non_critical(self, amount_kw: float) -> float:
        """
        Shed non-critical load
        Returns actual amount shed
        """
        actual_shed = min(amount_kw, self.non_critical_load_kw)
        self.shed_load_kw = actual_shed
        self.total_load_kw -= actual_shed
        self.non_critical_load_kw -= actual_shed
        # Proportionally reduce tier loads
        total_tiers = sum(self.tier_loads.values())
        if total_tiers > 0:
            scale = self.non_critical_load_kw / total_tiers
            for tier in self.tier_loads:
                self.tier_loads[tier] *= scale

        return actual_shed
    
    def restore_load(self, amount_kw: float = None) -> float:
        """
        Restore shed load
        If amount not specified, restore all
        Returns actual amount restored
        """
        if amount_kw is None:
            amount_kw = self.shed_load_kw
        
        actual_restore = min(amount_kw, self.shed_load_kw)
        self.shed_load_kw -= actual_restore
        self.total_load_kw += actual_restore
        self.non_critical_load_kw += actual_restore
        total_tiers = sum(self.original_tier_loads.values())
        if total_tiers > 0:
            scale = self.non_critical_load_kw / total_tiers
            for tier in self.tier_loads:
                self.tier_loads[tier] = self.original_tier_loads[tier] * scale
        return actual_restore
    
    def update_energy(self, dt_hours: float):
        """Update cumulative energy consumption"""
        self.cumulative_energy_kwh += self.total_load_kw * dt_hours
    
    def get_status(self) -> dict:
        """Get current load status"""
        return {
            'total_load_kw': self.total_load_kw,
            'critical_load_kw': self.critical_load_kw,
            'non_critical_load_kw': self.non_critical_load_kw,
            'shed_load_kw': self.shed_load_kw,
            'cumulative_energy_kwh': self.cumulative_energy_kwh
        }
    def shed_tier(self, tier: str, amount_kw: float) -> float:
        """Shed load from a specific tier"""
        if tier not in self.tier_loads:
            return 0.0

        available = self.tier_loads[tier]
        shed = min(amount_kw, available)

        if shed <= 0:
            return 0.0

        self.tier_loads[tier] -= shed
        self.total_load_kw -= shed
        self.non_critical_load_kw -= shed
        self.shed_load_kw += shed

        return shed


    def restore_tier(self, tier: str, amount_kw: float) -> float:
        """Restore load to a specific tier"""
        if tier not in self.tier_loads:
            return 0.0

        original = self.original_tier_loads[tier]
        current = self.tier_loads[tier]

        can_restore = original - current
        restored = min(amount_kw, can_restore)

        if restored <= 0:
            return 0.0

        self.tier_loads[tier] += restored
        self.total_load_kw += restored
        self.non_critical_load_kw += restored
        self.shed_load_kw -= restored

        return restored
