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
    """Battery Energy Storage System - Residential scale"""
    
    def __init__(self, config):
        self.config = config
        self.soc_percent = config.initial_soc_percent
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
        time_limited_power = energy_available / 0.25  # 15-minute discharge limit
        
        return min(self.config.max_discharge_power_kw, time_limited_power)
    
    def get_available_charge_power(self) -> float:
        """Calculate maximum available charge power"""
        if self.soc_percent >= self.config.max_soc_percent:
            return 0
        
        energy_capacity = (self.config.max_soc_percent - self.soc_percent) / 100 * self.config.usable_capacity_kwh
        time_limited_power = energy_capacity / 0.25
        
        return min(self.config.max_charge_power_kw, time_limited_power)
    
    def discharge(self, power_kw: float, dt_hours: float) -> float:
        """Discharge battery - returns actual power delivered"""
        max_power = self.get_available_discharge_power()
        actual_power = min(power_kw, max_power)
        
        if actual_power <= 0:
            self.power_kw = 0
            return 0
        
        # Energy with efficiency
        energy_from_battery = actual_power * dt_hours / self.config.discharge_efficiency
        
        # Update state
        self.energy_kwh -= energy_from_battery
        self.soc_percent = (self.energy_kwh / self.config.usable_capacity_kwh) * 100
        self.soc_percent = np.clip(self.soc_percent, self.config.min_soc_percent, self.config.max_soc_percent)
        
        self.power_kw = -actual_power
        self.cumulative_throughput_kwh += energy_from_battery
        self.temperature_c += actual_power * 0.001
        
        return actual_power
    
    def charge(self, power_kw: float, dt_hours: float) -> float:
        """Charge battery - returns actual power consumed"""
        max_power = self.get_available_charge_power()
        actual_power = min(power_kw, max_power)
        
        if actual_power <= 0:
            self.power_kw = 0
            return 0
        
        energy_to_battery = actual_power * dt_hours * self.config.charge_efficiency
        
        self.energy_kwh += energy_to_battery
        self.soc_percent = (self.energy_kwh / self.config.usable_capacity_kwh) * 100
        self.soc_percent = np.clip(self.soc_percent, self.config.min_soc_percent, self.config.max_soc_percent)
        
        self.power_kw = actual_power
        self.temperature_c += actual_power * 0.0008
        
        return actual_power
    
    def get_status(self) -> ComponentStatus:
        return ComponentStatus(
            state=self.state,
            power_kw=self.power_kw,
            energy_kwh=self.energy_kwh,
            efficiency=self.config.round_trip_efficiency
        )


class PVArray:
    """Solar PV array - Rooftop residential system"""
    
    def __init__(self, config):
        self.config = config
        self.power_kw = 0
        self.daily_energy_kwh = 0
        self.cumulative_energy_kwh = 0
        self.curtailment_kw = 0
        self.state = ComponentState.RUNNING
        
    def calculate_generation(self, timestamp, irradiance_w_m2: float = None, 
                           ambient_temp_c: float = 30) -> float:
        """Calculate PV generation - Bangalore urban conditions"""
        hour = timestamp.hour + timestamp.minute / 60
        
        if irradiance_w_m2 is None:
            # Simplified model with urban shading effects
            if 6 <= hour <= 18:
                hour_angle = (hour - 12) / 6 * np.pi / 2
                # Reduced by 15% for urban shading, pollution
                irradiance_w_m2 = 850 * np.cos(hour_angle) ** 2
            else:
                irradiance_w_m2 = 0
        
        stc_irradiance = 1000
        
        # Temperature derating (more significant in hot climate)
        cell_temp = ambient_temp_c + (self.config.nominal_operating_temp - 20) * (irradiance_w_m2 / stc_irradiance)
        temp_factor = 1 + self.config.temperature_coefficient * (cell_temp - 25)
        
        # Power calculation
        self.power_kw = (
            self.config.installed_capacity_kwp * 
            (irradiance_w_m2 / stc_irradiance) * 
            temp_factor * 
            self.config.inverter_efficiency
        )
        
        self.power_kw = max(0, self.power_kw)
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
        """Reset daily counter at midnight"""
        self.daily_energy_kwh = 0
    
    def get_status(self) -> ComponentStatus:
        return ComponentStatus(
            state=self.state,
            power_kw=self.power_kw,
            energy_kwh=self.cumulative_energy_kwh,
            efficiency=self.config.inverter_efficiency
        )


class Generator:
    """Residential diesel generator - Single unit"""
    
    def __init__(self, config, gen_id: int = 1):
        self.config = config
        self.gen_id = gen_id
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
        """Update generator state"""
        if self.state == ComponentState.STARTING:
            self.startup_timer -= dt_seconds
            if self.startup_timer <= 0:
                self.state = ComponentState.RUNNING
                self.power_kw = self.config.min_operating_power_kw
            return 0
        
        elif self.state == ComponentState.STOPPING:
            self.cooldown_timer -= dt_seconds
            ramp_rate = self.power_kw / self.config.cooldown_time_seconds
            self.power_kw = max(0, self.power_kw - ramp_rate * dt_seconds)
            
            if self.cooldown_timer <= 0:
                self.state = ComponentState.OFF
                self.power_kw = 0
            return self.power_kw
        
        elif self.state == ComponentState.RUNNING:
            if load_demand_kw is not None:
                rated_power = self.config.rated_power_kw
                
                target_power = np.clip(
                    load_demand_kw,
                    self.config.min_operating_power_kw,
                    rated_power
                )
                
                # Ramp rate limit
                max_ramp = rated_power * 0.08 * dt_seconds  # Slower than hospital
                power_change = target_power - self.power_kw
                
                if abs(power_change) > max_ramp:
                    power_change = max_ramp * np.sign(power_change)
                
                self.power_kw += power_change
            
            # Fuel consumption
            dt_hours = dt_seconds / 3600
            fuel_rate = self.config.fuel_consumption_l_per_kwh
            rated_power = self.config.rated_power_kw
            load_factor = self.power_kw / rated_power
            efficiency_factor = 0.65 + 0.35 * load_factor  # Less efficient
            
            self.fuel_consumed_liters += (self.power_kw * dt_hours * fuel_rate / efficiency_factor)
            self.runtime_hours += dt_hours
            
            return self.power_kw
        
        return 0
    
    @property
    def is_running(self) -> bool:
        return self.state == ComponentState.RUNNING
    
    def get_status(self) -> ComponentStatus:
        return ComponentStatus(
            state=self.state,
            power_kw=self.power_kw,
            energy_kwh=self.runtime_hours * self.power_kw
        )


class Load:
    """Residential load model with aggressive shedding capability"""
    
    def __init__(self, config):
        self.config = config
        
        # Core loads
        self.total_load_kw = 0
        self.critical_load_kw = config.load_profile.total_critical_load  # 100 kW
        self.non_critical_load_kw = 0
        self.shed_load_kw = 0
        
        # Non-critical tier loads (residential-specific)
        self.tier_loads = {
            "EV_CHARGING": config.load_profile.ev_charging_kw,           # 120 kW
            "AIR_CONDITIONING": config.load_profile.air_conditioning_kw,  # 280 kW
            "WASHING_MACHINES": config.load_profile.washing_machines_kw,  # 45 kW
            "COMMON_LIGHTING": config.load_profile.common_area_lighting_kw # 35 kW
        }
        
        self.original_tier_loads = self.tier_loads.copy()
        self.cumulative_energy_kwh = 0
        self.load_factor = 1.0
        
        # Resident discomfort tracking
        self.ac_shed_minutes = 0
        self.ev_shed_minutes = 0
        
    def update_load(self, timestamp) -> Tuple[float, float]:
        """Update load based on time-of-day - realistic residential pattern"""
        hour = timestamp.hour + timestamp.minute / 60
        
        # Find current load block
        base_load = 650  # Default peak
        for start_h, end_h, load_kw in self.config.load_profile.hour_blocks:
            if start_h <= hour < end_h:
                base_load = load_kw
                break
        
        # Higher variation for residential (±5%)
        variation = np.random.uniform(0.95, 1.05)
        
        self.total_load_kw = base_load * variation * self.load_factor
        self.non_critical_load_kw = max(0, self.total_load_kw - self.critical_load_kw)
        
        # Scale tier loads proportionally
        total_tier_capacity = sum(self.original_tier_loads.values())
        if total_tier_capacity > 0:
            scale = self.non_critical_load_kw / total_tier_capacity
            for tier in self.tier_loads:
                self.tier_loads[tier] = self.original_tier_loads[tier] * scale
        
        return self.total_load_kw, self.critical_load_kw
    
    def shed_non_critical(self, amount_kw: float) -> float:
        """Shed non-critical load - returns actual amount shed"""
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
        """Restore shed load"""
        if amount_kw is None:
            amount_kw = self.shed_load_kw
        
        actual_restore = min(amount_kw, self.shed_load_kw)
        self.shed_load_kw -= actual_restore
        self.total_load_kw += actual_restore
        self.non_critical_load_kw += actual_restore
        
        # Restore tier loads proportionally
        total_tiers = sum(self.original_tier_loads.values())
        if total_tiers > 0:
            scale = self.non_critical_load_kw / total_tiers
            for tier in self.tier_loads:
                self.tier_loads[tier] = self.original_tier_loads[tier] * scale
        
        return actual_restore
    
    def shed_tier(self, tier: str, amount_kw: float) -> float:
        """Shed load from specific tier - residential priority"""
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
        """Restore load to specific tier"""
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
    
    def update_discomfort_metrics(self, dt_minutes: float):
        """Track resident discomfort from load shedding"""
        # Track AC shedding
        ac_shed = self.original_tier_loads["AIR_CONDITIONING"] - self.tier_loads["AIR_CONDITIONING"]
        if ac_shed > 10:  # Significant AC shedding
            self.ac_shed_minutes += dt_minutes
        
        # Track EV charging disruption
        ev_shed = self.original_tier_loads["EV_CHARGING"] - self.tier_loads["EV_CHARGING"]
        if ev_shed > 10:
            self.ev_shed_minutes += dt_minutes
    
    def update_energy(self, dt_hours: float):
        """Update cumulative energy"""
        self.cumulative_energy_kwh += self.total_load_kw * dt_hours
    
    def get_status(self) -> dict:
        return {
            'total_load_kw': self.total_load_kw,
            'critical_load_kw': self.critical_load_kw,
            'non_critical_load_kw': self.non_critical_load_kw,
            'shed_load_kw': self.shed_load_kw,
            'cumulative_energy_kwh': self.cumulative_energy_kwh,
            'tier_loads': self.tier_loads.copy(),
            'ac_shed_minutes': self.ac_shed_minutes,
            'ev_shed_minutes': self.ev_shed_minutes
        }