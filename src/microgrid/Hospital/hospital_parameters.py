import json
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Tuple

@dataclass
class LoadProfile:
    """24-hour hospital load profile with critical/non-critical breakdown"""
    hour_blocks: List[tuple] = None
    critical_loads: Dict[str, float] = None
    
    # Load categories with shedding limits (HOSPITAL-SPECIFIC)
    load_categories: Dict[str, Dict] = None
    
    # Hospital-specific non-critical loads
    hvac_partial_kw: float = 120  # Partial HVAC for non-critical areas
    wards_lighting_kw: float = 60
    admin_misc_kw: float = 100
    
    def __post_init__(self):
        if self.hour_blocks is None:
            # Hospital operates 24/7 with minor variations
            # (start_hour, end_hour, power_kw)
            self.hour_blocks = [
                (0, 6, 550),      # Night: reduced activity
                (6, 9, 580),      # Morning shift change
                (9, 17, 600),     # Day peak: OTs, diagnostics
                (17, 20, 590),    # Evening: high occupancy
                (20, 24, 560)     # Night operations
            ]
        
        if self.critical_loads is None:
            # NON-NEGOTIABLE HOSPITAL CRITICAL LOADS (320 kW)
            self.critical_loads = {
                'icu_life_support': 120,      # ICU ventilators, monitors, infusion pumps
                'operation_theatres': 80,      # OT lights, anesthesia, surgical equipment
                'emergency_labs': 70,          # ER, pathology, radiology (critical)
                'essential_lighting': 30,      # Emergency corridors, exits, ICU/OT
                'it_monitoring': 20            # EHR, patient monitoring systems, communication
            }
        
        if self.load_categories is None:
            self.load_categories = {
                'critical': {
                    'power_kw': self.total_critical_load,
                    'max_shed_percent': 0,      # NEVER shed critical loads
                    'priority': 1
                },
                'hvac_partial': {
                    'power_kw': 120,
                    'max_shed_percent': 70,     # Can reduce ward HVAC significantly
                    'priority': 4
                },
                'wards_lighting': {
                    'power_kw': 60,
                    'max_shed_percent': 60,     # Can dim non-critical ward lighting
                    'priority': 5
                },
                'admin_misc': {
                    'power_kw': 100,
                    'max_shed_percent': 90,     # Admin areas fully sheddable
                    'priority': 6
                }
            }
    
    @property
    def total_critical_load(self) -> float:
        """320 kW - NON-NEGOTIABLE"""
        return sum(self.critical_loads.values())
    
    @property
    def total_non_critical_load(self) -> float:
        """280 kW - Sheddable"""
        return self.hvac_partial_kw + self.wards_lighting_kw + self.admin_misc_kw
    
    @property
    def peak_load(self) -> float:
        """600 kW peak"""
        return max(block[2] for block in self.hour_blocks)
    
    @property
    def average_load(self) -> float:
        total_energy = sum((block[1] - block[0]) * block[2] for block in self.hour_blocks)
        return total_energy / 24
    
    def get_category_shedding_potential(self, category: str) -> float:
        """Get max kW that can be shed from a category"""
        if category not in self.load_categories:
            return 0
        cat = self.load_categories[category]
        return cat['power_kw'] * (cat['max_shed_percent'] / 100)


@dataclass
class BatteryConfig:
    """Battery Energy Storage System - Hospital-grade reliability"""
    # Start higher for hospital
    
    nominal_capacity_kwh: float = 2600
    usable_capacity_kwh: float = 2400.0
    max_discharge_power_kw: float = 500.0
    max_charge_power_kw: float = 500.0
    round_trip_efficiency: float = 0.90
    min_soc_percent: float = 10
    max_soc_percent: float = 90
    initial_soc_percent: float = 90

    
    @property
    def discharge_efficiency(self) -> float:
        return self.round_trip_efficiency ** 0.5
    
    @property
    def charge_efficiency(self) -> float:
        return self.round_trip_efficiency ** 0.5
    
    @property
    def critical_backup_hours(self) -> float:
        """Backup time for 320 kW critical load"""
        return self.usable_capacity_kwh / 320


@dataclass
class PVConfig:
    """Solar PV system - Hospital rooftop + carport"""
    installed_capacity_kwp: float = 400     # Realistic hospital rooftop
    panel_efficiency: float = 0.22
    inverter_efficiency: float = 0.97
    temperature_coefficient: float = -0.004
    nominal_operating_temp: float = 45
    design_insolation_kwh_m2_day: float = 5.5  # Varies by location
    
    @property
    def estimated_daily_generation_kwh(self) -> float:
        return self.installed_capacity_kwp * self.design_insolation_kwh_m2_day * self.inverter_efficiency
    
    @property
    def peak_generation_kw(self) -> float:
        """Peak output around noon"""
        return self.installed_capacity_kwp * self.inverter_efficiency


@dataclass
class GeneratorConfig:
    """Diesel Generator - N+1 redundancy for critical loads"""
    # Generator 1: Dedicated to critical loads
    gen1_rated_power_kw: float = 450        # Covers 320 kW critical + margin
    gen1_rated_power_kva: float = 562.3    # At 0.8 PF
    
    # Generator 2: Serves non-critical + backup
    gen2_rated_power_kw: float = 450
    gen2_rated_power_kva: float = 562.3
    
    fuel_consumption_l_per_kwh: float = 0.26  # Hospital-grade diesel
    min_load_ratio: float = 0.3
    startup_time_seconds: float = 10        # Faster for hospital
    cooldown_time_seconds: float = 180
    
    # Control thresholds
    auto_start_soc_threshold: float = 25    # More conservative for hospital
    auto_stop_soc_threshold: float = 80
    
    min_on_time_minutes: int = 30
    min_off_time_minutes: int = 15          # Shorter for hospital flexibility
    
    # Fuel storage
    fuel_tank_capacity_liters: float = 10000  # 72+ hours at full load
    
    @property
    def rated_power_kw(self) -> float:
        """Total generator capacity"""
        return self.gen1_rated_power_kw
    
    @property
    def min_operating_power_kw(self) -> float:
        return self.rated_power_kw * self.min_load_ratio
    
    @property
    def total_capacity_kw(self) -> float:
        """Both generators combined"""
        return self.gen1_rated_power_kw + self.gen2_rated_power_kw


@dataclass
class ControlConfig:
    """Energy Management System - Hospital-specific control"""
    backup_duration_hours: float = 6.0      # Critical load backup time
    time_resolution_minutes: float = 5
    islanding_detection_time_ms: float = 50  # Faster detection for hospital
    reconnection_delay_seconds: float = 300
    
    # Load shedding priority (hospital-specific)
    load_shedding_priority: List[str] = None
    
    frequency_droop_coefficient: float = 0.04
    voltage_droop_coefficient: float = 0.02
    
    # Shedding policy
    max_total_shed_kw: float = 280          # All non-critical can be shed
    max_shed_percent_per_step: float = 30   # Gradual curtailment
    enforce_critical_load_priority: bool = True  # ALWAYS True for hospital
    
    # Restoration policy
    restore_time_min: float = 10            # Wait 10 min before restoring
    restore_margin_ratio: float = 0.25      # 25% margin before restore
    
    def __post_init__(self):
        if self.load_shedding_priority is None:
            # Priority order (highest number = shed first)
            self.load_shedding_priority = [
                'critical',          # Priority 1: NEVER shed
                'hvac_partial',      # Priority 4: Shed if needed
                'wards_lighting',    # Priority 5: Shed after HVAC
                'admin_misc'         # Priority 6: Shed first
            ]


@dataclass
class ProtectionConfig:
    """Protection system - Hospital-grade requirements"""
    over_frequency_hz: float = 50.5
    under_frequency_hz: float = 49.5
    over_voltage_pu: float = 1.1
    under_voltage_pu: float = 0.9
    over_current_ratio: float = 1.5
    reconnection_voltage_window_pu: tuple = (0.95, 1.05)
    reconnection_frequency_window_hz: tuple = (49.8, 50.2)
    
    # Hospital-specific
    critical_load_isolation_time_ms: float = 100  # Fast isolation
    backup_power_transfer_time_ms: float = 50     # Very fast transfer


@dataclass
class MicrogridConfig:
    """Complete hospital microgrid configuration"""
    facility_name: str = "Regional Hospital"
    facility_type: str = "150-250 Bed Hospital"
    location: str = "Bangalore, Karnataka, India"
    latitude: float = 12.9716
    longitude: float = 77.5946
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
        return asdict(self)
    
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
        """Validate configuration for hospital requirements"""
        warnings = []
        
        # CRITICAL: Battery must support critical loads
        required_energy = self.load_profile.total_critical_load * self.control.backup_duration_hours
        if self.battery.usable_capacity_kwh < required_energy:
            warnings.append(
                f"⚠️ CRITICAL: Battery capacity ({self.battery.usable_capacity_kwh} kWh) "
                f"insufficient for {self.control.backup_duration_hours}h critical backup. "
                f"Required: {required_energy:.0f} kWh"
            )
        
        # Battery power must handle critical load
        if self.battery.max_discharge_power_kw < self.load_profile.total_critical_load * 1.1:
            warnings.append(
                f"⚠️ CRITICAL: Battery power ({self.battery.max_discharge_power_kw} kW) "
                f"insufficient for critical loads ({self.load_profile.total_critical_load} kW)"
            )
        
        # Generator must cover critical loads
        if self.generator.gen1_rated_power_kw < self.load_profile.total_critical_load:
            warnings.append(
                f"⚠️ CRITICAL: Generator 1 ({self.generator.gen1_rated_power_kw} kW) "
                f"cannot cover critical loads ({self.load_profile.total_critical_load} kW)"
            )
        
        # Verify 320 kW critical load
        if abs(self.load_profile.total_critical_load - 320) > 1:
            warnings.append(
                f"⚠️ WARNING: Critical load is {self.load_profile.total_critical_load} kW, "
                f"expected 320 kW per specification"
            )
        
        # Verify 280 kW non-critical load
        if abs(self.load_profile.total_non_critical_load - 280) > 1:
            warnings.append(
                f"⚠️ WARNING: Non-critical load is {self.load_profile.total_non_critical_load} kW, "
                f"expected 280 kW per specification"
            )
        
        return warnings


def create_default_config() -> MicrogridConfig:
    """Create and return default hospital microgrid configuration"""
    config = MicrogridConfig()
    
    # Validate and print warnings
    warnings = config.validate()
    if warnings:
        try:
            print("\n[HOSPITAL] Microgrid Configuration Warnings:")
            for w in warnings:
                print(f"  {w}")
        except UnicodeEncodeError:
            # Handle encoding issues on Windows
            print("\n[HOSPITAL] Microgrid Configuration: Some warnings could not be displayed due to encoding.")
    else:
        print("\n[OK] Hospital microgrid configuration validated successfully!")
    
    return config


if __name__ == "__main__":
    # Create configuration
    config = create_default_config()
    
    # Export to JSON
    config.to_json('hospital_parameters.json')
    print("\nConfiguration exported to: hospital_parameters.json")
    
    # Print summary
    print("\n" + "="*70)
    print("🏥 HOSPITAL MICROGRID CONFIGURATION SUMMARY")
    print("="*70)
    print(f"Facility: {config.facility_name} ({config.facility_type})")
    print(f"Location: {config.location}")
    
    print(f"\n📊 LOAD PROFILE (NON-NEGOTIABLE):")
    print(f"  Peak Load:          {config.load_profile.peak_load} kW")
    print(f"  Average Load:       {config.load_profile.average_load:.1f} kW")
    print(f"  ┃")
    print(f"  ┣━━ 🔴 CRITICAL:    {config.load_profile.total_critical_load} kW (53%)")
    print(f"  ┃   ├─ ICU/Life Support:  {config.load_profile.critical_loads['icu_life_support']} kW")
    print(f"  ┃   ├─ Operation Theatres: {config.load_profile.critical_loads['operation_theatres']} kW")
    print(f"  ┃   ├─ Emergency/Labs:     {config.load_profile.critical_loads['emergency_labs']} kW")
    print(f"  ┃   ├─ Essential Lighting: {config.load_profile.critical_loads['essential_lighting']} kW")
    print(f"  ┃   └─ IT/Monitoring:      {config.load_profile.critical_loads['it_monitoring']} kW")
    print(f"  ┃")
    print(f"  ┗━━ NON-CRITICAL:  {config.load_profile.total_non_critical_load} kW (47%)")
    print(f"      ├─ HVAC (Partial):    {config.load_profile.hvac_partial_kw} kW")
    print(f"      ├─ Wards Lighting:    {config.load_profile.wards_lighting_kw} kW")
    print(f"      └─ Admin/Misc:        {config.load_profile.admin_misc_kw} kW")
    
    print(f"\n🔋 BATTERY STORAGE:")
    print(f"  Capacity:           {config.battery.usable_capacity_kwh} kWh usable")
    print(f"  Power:              {config.battery.max_discharge_power_kw} kW discharge")
    print(f"  Critical Backup:    {config.battery.critical_backup_hours:.1f} hours @ 320 kW")
    print(f"  Efficiency:         {config.battery.round_trip_efficiency*100:.1f}% round-trip")
    
    print(f"\n☀️ SOLAR PV:")
    print(f"  Capacity:           {config.pv.installed_capacity_kwp} kWp")
    print(f"  Peak Generation:    {config.pv.peak_generation_kw:.0f} kW")
    print(f"  Daily Generation:   {config.pv.estimated_daily_generation_kwh:.0f} kWh (estimated)")
    
    print(f"\n⚡ GENERATORS (N+1 Redundancy):")
    print(f"  Generator 1:        {config.generator.gen1_rated_power_kw} kW (Critical loads)")
    print(f"  Generator 2:        {config.generator.gen2_rated_power_kw} kW (Non-critical + Backup)")
    print(f"  Total Capacity:     {config.generator.total_capacity_kw} kW")
    print(f"  Fuel Tank:          {config.generator.fuel_tank_capacity_liters} liters")
    print(f"  Startup Time:       {config.generator.startup_time_seconds} seconds")
    
    print(f"\n🎛️ CONTROL SYSTEM:")
    print(f"  Time Resolution:    {config.control.time_resolution_minutes} minutes")
    print(f"  Islanding Detection: {config.control.islanding_detection_time_ms} ms")
    print(f"  Critical Priority:  {config.control.enforce_critical_load_priority}")
    print(f"  Max Shedding:       {config.control.max_total_shed_kw} kW (non-critical only)")
    
    print("\n" + "="*70)
