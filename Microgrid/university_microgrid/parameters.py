import json
from dataclasses import dataclass, asdict
from typing import Dict, List

@dataclass
class LoadProfile:
    """24-hour load profile configuration"""
    hour_blocks: List[tuple] = None
    critical_loads: Dict[str, float] = None

    hvac_load_kw: float = 180
    labs_load_kw: float = 140
    lighting_load_kw: float = 100

    
    def __post_init__(self):
        if self.hour_blocks is None:
            # (start_hour, end_hour, power_kw)
            self.hour_blocks = [
                (0, 5, 160),
                (5, 8, 260),
                (8, 13, 520),
                (13, 17, 600),
                (17, 22, 420),
                (22, 24, 240)
            ]
        
        if self.critical_loads is None:
            self.critical_loads = {
                'data_center': 60,
                'emergency_lighting': 20,
                'research_labs': 80,
                'communication': 25,
                'essential_hvac': 40,
                'medical_clinic': 15
            }
    
    @property
    def total_critical_load(self) -> float:
        return sum(self.critical_loads.values())
    
    @property
    def peak_load(self) -> float:
        return max(block[2] for block in self.hour_blocks)
    
    @property
    def average_load(self) -> float:
        total_energy = sum((block[1] - block[0]) * block[2] for block in self.hour_blocks)
        return total_energy / 24

@dataclass
class BatteryConfig:
    """Battery Energy Storage System configuration"""
    nominal_capacity_kwh: float = 2000
    usable_capacity_kwh: float = 1800
    max_discharge_power_kw: float = 400
    max_charge_power_kw: float = 400
    round_trip_efficiency: float = 0.91
    min_soc_percent: float = 20
    max_soc_percent: float = 95
    initial_soc_percent: float = 80
    
    @property
    def discharge_efficiency(self) -> float:
        return self.round_trip_efficiency ** 0.5
    
    @property
    def charge_efficiency(self) -> float:
        return self.round_trip_efficiency ** 0.5

@dataclass
class PVConfig:
    """Solar PV system configuration"""
    installed_capacity_kwp: float = 600
    panel_efficiency: float = 0.20
    inverter_efficiency: float = 0.97
    temperature_coefficient: float = -0.004  # per °C
    nominal_operating_temp: float = 45  # °C
    design_insolation_kwh_m2_day: float = 5.0
    
    @property
    def estimated_daily_generation_kwh(self) -> float:
        return self.installed_capacity_kwp * self.design_insolation_kwh_m2_day * self.inverter_efficiency

@dataclass
class GeneratorConfig:
    rated_power_kw: float = 600
    rated_power_kva: float = 750
    fuel_consumption_l_per_kwh: float = 0.25
    min_load_ratio: float = 0.3
    startup_time_seconds: float = 15
    cooldown_time_seconds: float = 300

    auto_start_soc_threshold: float = 35
    auto_stop_soc_threshold: float = 60

    min_on_time_minutes: int = 30
    min_off_time_minutes: int = 30

    
    @property
    def min_operating_power_kw(self) -> float:
        return self.rated_power_kw * self.min_load_ratio

@dataclass
class ControlConfig:
    """Energy Management System control parameters"""
    backup_duration_hours: float = 6
    time_resolution_minutes: float = 5
    islanding_detection_time_ms: float = 100
    reconnection_delay_seconds: float = 300
    load_shedding_priority: List[str] = None
    frequency_droop_coefficient: float = 0.04
    voltage_droop_coefficient: float = 0.02
    
    def __post_init__(self):
        if self.load_shedding_priority is None:
            # Priority order (1 = shed first, 5 = shed last)
            self.load_shedding_priority = [
                'hvac_non_critical',
                'general_lighting',
                'office_equipment',
                'lab_non_essential',
                'hostel_loads'
            ]

@dataclass
class ProtectionConfig:
    """Protection system parameters"""
    over_frequency_hz: float = 50.5
    under_frequency_hz: float = 49.5
    over_voltage_pu: float = 1.1
    under_voltage_pu: float = 0.9
    over_current_ratio: float = 1.5
    reconnection_voltage_window_pu: tuple = (0.95, 1.05)
    reconnection_frequency_window_hz: tuple = (49.8, 50.2)

@dataclass
class MicrogridConfig:
    """Complete microgrid system configuration"""
    campus_name: str = "Amrita University Coimbatore"
    location: str = "Coimbatore, Tamil Nadu, India"
    latitude: float = 11.0183
    longitude: float = 76.9725
    timezone: str = "Asia/Kolkata"
    nominal_voltage_kv: float = 11
    nominal_frequency_hz: float = 50
    
    load_profile: LoadProfile = None
    battery: BatteryConfig = None
    pv: PVConfig = None
    generator: GeneratorConfig = None
    control: ControlConfig = None
    protection: ProtectionConfig = None
    
    def __post_init__(self):
        if self.load_profile is None:
            self.load_profile = LoadProfile()
        if self.battery is None:
            self.battery = BatteryConfig()
        if self.pv is None:
            self.pv = PVConfig()
        if self.generator is None:
            self.generator = GeneratorConfig()
        if self.control is None:
            self.control = ControlConfig()
        if self.protection is None:
            self.protection = ProtectionConfig()
    
    def to_dict(self) -> dict:
        """Convert configuration to dictionary"""
        result = {}
        for key, value in asdict(self).items():
            if isinstance(value, dict):
                result[key] = value
            else:
                result[key] = value
        return result
    
    def to_json(self, filepath: str = None) -> str:
        """Export configuration to JSON"""
        config_dict = self.to_dict()
        json_str = json.dumps(config_dict, indent=2)
        
        if filepath:
            with open(filepath, 'w') as f:
                f.write(json_str)
        
        return json_str
    
    @classmethod
    def from_json(cls, filepath: str):
        """Load configuration from JSON file"""
        with open(filepath, 'r') as f:
            data = json.load(f)
        
        # Reconstruct nested dataclasses
        load_profile = LoadProfile(**data.pop('load_profile', {}))
        battery = BatteryConfig(**data.pop('battery', {}))
        pv = PVConfig(**data.pop('pv', {}))
        generator = GeneratorConfig(**data.pop('generator', {}))
        control = ControlConfig(**data.pop('control', {}))
        protection = ProtectionConfig(**data.pop('protection', {}))
        
        return cls(
            load_profile=load_profile,
            battery=battery,
            pv=pv,
            generator=generator,
            control=control,
            protection=protection,
            **data
        )
    
    def validate(self) -> List[str]:
        """Validate configuration parameters"""
        warnings = []
        
        # Battery sizing check
        required_energy = self.load_profile.total_critical_load * self.control.backup_duration_hours
        if self.battery.usable_capacity_kwh < required_energy * 1.2:
            warnings.append(
                f"Battery capacity ({self.battery.usable_capacity_kwh} kWh) may be insufficient "
                f"for {self.control.backup_duration_hours}h backup. Recommended: {required_energy * 1.2:.0f} kWh"
            )
        
        # Power rating check
        if self.battery.max_discharge_power_kw < self.load_profile.total_critical_load * 1.1:
            warnings.append(
                f"Battery power rating ({self.battery.max_discharge_power_kw} kW) may be insufficient "
                f"for critical loads ({self.load_profile.total_critical_load} kW)"
            )
        
        # Generator sizing
        if self.generator.rated_power_kw < self.load_profile.total_critical_load:
            warnings.append(
                f"Generator capacity ({self.generator.rated_power_kw} kW) less than critical load "
                f"({self.load_profile.total_critical_load} kW)"
            )
        
        return warnings


# Create default configuration instance
def create_default_config() -> MicrogridConfig:
    """Create and return default microgrid configuration"""
    config = MicrogridConfig()
    
    # Validate and print warnings
    warnings = config.validate()
    if warnings:
        print("Configuration Warnings:")
        for w in warnings:
            print(f"  - {w}")
    
    return config


if __name__ == "__main__":
    # Create configuration
    config = create_default_config()
    
    # Export to JSON
    config.to_json('parameters.json')
    print("Configuration exported to: parameters.json")
    
    # Print summary
    print("\n=== Amrita Microgrid Configuration Summary ===")
    print(f"Campus: {config.campus_name}")
    print(f"Location: {config.location}")
    print(f"\nLoad Profile:")
    print(f"  Peak Load: {config.load_profile.peak_load} kW")
    print(f"  Average Load: {config.load_profile.average_load:.1f} kW")
    print(f"  Critical Load: {config.load_profile.total_critical_load} kW")
    print(f"\nBattery Storage:")
    print(f"  Capacity: {config.battery.usable_capacity_kwh} kWh usable")
    print(f"  Power: {config.battery.max_discharge_power_kw} kW")
    print(f"  Backup Duration: {config.control.backup_duration_hours} hours")
    print(f"\nSolar PV:")
    print(f"  Capacity: {config.pv.installed_capacity_kwp} kWp")
    print(f"  Est. Daily Gen: {config.pv.estimated_daily_generation_kwh:.0f} kWh")
    print(f"\nGenerator:")
    print(f"  Capacity: {config.generator.rated_power_kw} kW")
    print(f"  Auto-start SoC: {config.generator.auto_start_soc_threshold}%")