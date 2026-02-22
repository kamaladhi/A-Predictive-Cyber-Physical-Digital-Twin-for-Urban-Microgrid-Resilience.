import json
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Tuple

@dataclass
class LoadProfile:
    """24-hour residential gated community load profile
    
    Reality: 400 apartments, 8 buildings, urban Bangalore
    Peak evening load driven by AC + cooking + EV charging
    """
    hour_blocks: List[tuple] = None
    critical_loads: Dict[str, float] = None
    
    # Load categories with shedding priority (RESIDENTIAL-SPECIFIC)
    load_categories: Dict[str, Dict] = None
    
    # Residential non-critical loads (comfort/lifestyle)
    air_conditioning_kw: float = 280        # 70% of apartments running AC
    washing_machines_kw: float = 45         # Evening laundry peak
    ev_charging_kw: float = 120             # 30 EVs charging (4 kW each)
    common_area_lighting_kw: float = 35     # Corridors, lobbies, parking
    
    def __post_init__(self):
        if self.hour_blocks is None:
            # Residential has sharp peaks - NOT flat like hospital
            # (start_hour, end_hour, power_kW)
            self.hour_blocks = [
                (0, 5, 180),       # Night: minimal (only fridges, security)
                (5, 9, 520),       # Morning peak: geysers, breakfast, lifts
                (9, 17, 280),      # Day low: working hours, minimal occupancy
                (17, 23, 650),     # EVENING PEAK: AC + cooking + EV + entertainment
                (23, 24, 250)      # Late night: AC continues, lights off
            ]
        
        if self.critical_loads is None:
            # SAFETY-CRITICAL ONLY (100 kW) - NOT comfort
            self.critical_loads = {
                'lifts_emergency': 30,          # 4 lifts at reduced capacity
                'water_pumps': 25,              # Borewell + sump pumps
                'emergency_lighting': 20,       # Stairwells, exits, parking
                'security_systems': 15,         # CCTV, access control, intercoms
                'common_services': 10           # Fire alarms, PA system
            }
        
        if self.load_categories is None:
            self.load_categories = {
                'critical': {
                    'power_kw': self.total_critical_load,
                    'max_shed_percent': 0,      # NEVER shed safety loads
                    'priority': 1
                },
                'air_conditioning': {
                    'power_kw': 280,
                    'max_shed_percent': 100,    # Can fully disable AC
                    'priority': 5
                },
                'ev_charging': {
                    'power_kw': 120,
                    'max_shed_percent': 100,    # Shed FIRST - luxury
                    'priority': 6
                },
                'washing_machines': {
                    'power_kw': 45,
                    'max_shed_percent': 100,    # Fully sheddable
                    'priority': 4
                },
                'common_lighting': {
                    'power_kw': 35,
                    'max_shed_percent': 80,     # Keep minimal pathway lighting
                    'priority': 3
                }
            }
    
    @property
    def total_critical_load(self) -> float:
        """100 kW - Safety only, NOT comfort"""
        return sum(self.critical_loads.values())
    
    @property
    def total_non_critical_load(self) -> float:
        """480 kW - ALL sheddable for survival"""
        return (self.air_conditioning_kw + self.washing_machines_kw + 
                self.ev_charging_kw + self.common_area_lighting_kw)
    
    @property
    def peak_load(self) -> float:
        """650 kW - Evening peak (AC + cooking + EV)"""
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
    """Battery Energy Storage System - Residential scale
    
    Reality: Cannot sustain full outage, sized for peak shaving
    Critical loads only: ~3-4 hours backup
    """
    nominal_capacity_kwh: float = 500       # Modest residential BESS
    usable_capacity_kwh: float = 450        # 90% usable
    max_discharge_power_kw: float = 200     # Cannot handle full load
    max_charge_power_kw: float = 200
    round_trip_efficiency: float = 0.88     # Residential grade
    min_soc_percent: float = 15             # Deep discharge allowed
    max_soc_percent: float = 90
    initial_soc_percent: float = 85         # Start less prepared than hospital
    
    @property
    def discharge_efficiency(self) -> float:
        return self.round_trip_efficiency ** 0.5
    
    @property
    def charge_efficiency(self) -> float:
        return self.round_trip_efficiency ** 0.5
    
    @property
    def critical_backup_hours(self) -> float:
        """Backup time for 100 kW critical load only"""
        return self.usable_capacity_kwh / 100  # ~4.5 hours for critical only


@dataclass
class PVConfig:
    """Solar PV system - Rooftop across 8 buildings
    
    Reality: Limited roof space, partial shading, urban constraints
    NOT net-zero capable
    """
    installed_capacity_kwp: float = 250     # ~30-35% of peak load
    panel_efficiency: float = 0.20          # Standard residential panels
    inverter_efficiency: float = 0.96       # Residential inverters
    temperature_coefficient: float = -0.0045
    nominal_operating_temp: float = 47      # Indian summer heat
    design_insolation_kwh_m2_day: float = 5.0  # Urban Bangalore average
    
    @property
    def estimated_daily_generation_kwh(self) -> float:
        return self.installed_capacity_kwp * self.design_insolation_kwh_m2_day * self.inverter_efficiency
    
    @property
    def peak_generation_kw(self) -> float:
        """Peak output around noon"""
        return self.installed_capacity_kwp * self.inverter_efficiency


@dataclass
class GeneratorConfig:
    """Diesel Generator - Single unit for cost efficiency
    
    Reality: Expensive to run, limited fuel, noise complaints
    Used ONLY as last resort
    """
    gen1_rated_power_kw: float = 300        # Can cover critical + some comfort
    gen1_rated_power_kva: float = 375       # At 0.8 PF
    
    # No Gen2 for residential (cost prohibitive)
    gen2_rated_power_kw: float = 0
    gen2_rated_power_kva: float = 0
    
    fuel_consumption_l_per_kwh: float = 0.28    # Less efficient than hospital
    min_load_ratio: float = 0.25
    startup_time_seconds: float = 15            # Slower than hospital
    cooldown_time_seconds: float = 240
    
    # Control thresholds - MORE CONSERVATIVE (fuel cost concern)
    auto_start_soc_threshold: float = 20        # Start later than hospital
    auto_stop_soc_threshold: float = 70         # Stop earlier
    
    min_on_time_minutes: int = 20
    min_off_time_minutes: int = 20
    
    # Fuel storage - LIMITED
    fuel_tank_capacity_liters: float = 2000     # ~8-10 hours at full load
    
    @property
    def rated_power_kw(self) -> float:
        return self.gen1_rated_power_kw
    
    @property
    def min_operating_power_kw(self) -> float:
        return self.rated_power_kw * self.min_load_ratio
    
    @property
    def total_capacity_kw(self) -> float:
        """Single generator only"""
        return self.gen1_rated_power_kw


@dataclass
class ControlConfig:
    """Energy Management System - Residential survival mode
    
    Priority: Safety first, comfort sacrificed
    """
    backup_duration_hours: float = 4.5      # Critical load backup only
    time_resolution_minutes: float = 5
    islanding_detection_time_ms: float = 100
    reconnection_delay_seconds: float = 300
    
    # Load shedding priority (AGGRESSIVE for residential)
    load_shedding_priority: List[str] = None
    
    frequency_droop_coefficient: float = 0.04
    voltage_droop_coefficient: float = 0.02
    
    # Shedding policy - HARSH reality
    max_total_shed_kw: float = 480          # Can shed ALL non-critical
    max_shed_percent_per_step: float = 50   # Aggressive curtailment
    enforce_critical_load_priority: bool = True
    
    # Restoration policy - CAUTIOUS (limited resources)
    restore_time_min: float = 15            # Wait longer before restoring
    restore_margin_ratio: float = 0.50      # Need 50% margin (vs 25% hospital)
    
    def __post_init__(self):
        if self.load_shedding_priority is None:
            # Priority order (highest number = shed first)
            # Residential MUST sacrifice comfort for survival
            self.load_shedding_priority = [
                'critical',          # Priority 1: NEVER shed
                'common_lighting',   # Priority 3: Keep minimal
                'washing_machines',  # Priority 4: Defer laundry
                'air_conditioning',  # Priority 5: Shed AC (discomfort accepted)
                'ev_charging'        # Priority 6: Shed FIRST (luxury)
            ]


@dataclass
class ProtectionConfig:
    """Protection system - Residential grade"""
    over_frequency_hz: float = 50.5
    under_frequency_hz: float = 49.5
    over_voltage_pu: float = 1.1
    under_voltage_pu: float = 0.9
    over_current_ratio: float = 1.5
    reconnection_voltage_window_pu: tuple = (0.95, 1.05)
    reconnection_frequency_window_hz: tuple = (49.8, 50.2)
    
    # Residential-specific
    critical_load_isolation_time_ms: float = 200    # Slower than hospital
    backup_power_transfer_time_ms: float = 100


@dataclass
class MicrogridConfig:
    """Complete residential gated community microgrid configuration
    
    REALITY CHECK:
    - 400 apartments, 8 buildings
    - Peak load 650 kW, cannot be fully backed up
    - Battery: 450 kWh (NOT enough for full outage)
    - Solar: 250 kWp (helps but insufficient alone)
    - Generator: 300 kW (expensive, limited fuel)
    - Residents WILL experience discomfort during outages
    - Survival > Comfort
    """
    facility_name: str = "Green Valley Residences"
    facility_type: str = "400-Apartment Gated Community"
    location: str = "Whitefield, Bangalore, Karnataka, India"
    latitude: float = 12.9698
    longitude: float = 77.7500
    timezone: str = "Asia/Kolkata"
    nominal_voltage_kv: float = 0.415       # LT supply (not 11 kV)
    nominal_frequency_hz: float = 50
    
    # Priority level for city coordination
    city_priority_level: int = 3            # Lower than hospital(1), university(2)
    
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
        """Validate configuration for residential requirements"""
        warnings = []
        
        # Battery CANNOT handle full load (this is expected)
        if self.battery.max_discharge_power_kw < self.load_profile.peak_load:
            warnings.append(
                f"EXPECTED: Battery power ({self.battery.max_discharge_power_kw} kW) "
                f"cannot handle peak load ({self.load_profile.peak_load} kW). "
                f"Load shedding REQUIRED during outages."
            )
        
        # Battery energy insufficient for full outage
        required_full_backup = self.load_profile.peak_load * self.control.backup_duration_hours
        if self.battery.usable_capacity_kwh < required_full_backup:
            warnings.append(
                f"EXPECTED: Battery capacity ({self.battery.usable_capacity_kwh} kWh) "
                f"cannot sustain peak load. Critical loads only: "
                f"{self.battery.critical_backup_hours:.1f}h backup available."
            )
        
        # Generator cannot handle peak load alone
        if self.generator.rated_power_kw < self.load_profile.peak_load:
            warnings.append(
                f"EXPECTED: Generator ({self.generator.rated_power_kw} kW) "
                f"undersized for peak load. Comfort loads WILL be shed."
            )
        
        # Verify 100 kW critical load
        if abs(self.load_profile.total_critical_load - 100) > 1:
            warnings.append(
                f"WARNING: Critical load is {self.load_profile.total_critical_load} kW, "
                f"expected 100 kW per specification"
            )
        
        # Verify 480 kW non-critical load range
        if not (450 <= self.load_profile.total_non_critical_load <= 500):
            warnings.append(
                f"WARNING: Non-critical load is {self.load_profile.total_non_critical_load} kW, "
                f"expected ~480 kW per specification"
            )
        
        # Solar cannot achieve net-zero
        daily_consumption = self.load_profile.average_load * 24
        if self.pv.estimated_daily_generation_kwh >= daily_consumption * 0.9:
            warnings.append(
                f"UNREALISTIC: PV generation too high. "
                f"Residential should NOT be near net-zero."
            )
        
        # Fuel storage check
        max_gen_hours = (self.generator.fuel_tank_capacity_liters / 
                        (self.generator.rated_power_kw * self.generator.fuel_consumption_l_per_kwh))
        if max_gen_hours > 12:
            warnings.append(
                f"Fuel tank allows {max_gen_hours:.1f}h generator runtime at full load"
            )
        
        return warnings


def create_default_config() -> MicrogridConfig:
    """Create and return default residential microgrid configuration"""
    config = MicrogridConfig()
    
    # Validate and print warnings
    warnings = config.validate()
    if warnings:
        try:
            print("\n[RESIDENTIAL] Microgrid Configuration Notes:")
            for w in warnings:
                print(f"  {w}")
        except UnicodeEncodeError:
            # Handle encoding issues on Windows
            print("\n[RESIDENTIAL] Microgrid Configuration: Some warnings could not be displayed due to encoding.")
    else:
        print("\n[OK] Residential microgrid configuration validated!")
    
    return config


if __name__ == "__main__":
    # Create configuration
    config = create_default_config()
    
    # Export to JSON
    config.to_json('residential_parameters.json')
    print("\nConfiguration exported to: residential_parameters.json")
    
    # Print summary
    print("\n" + "="*70)
    print("🏘️ RESIDENTIAL MICROGRID CONFIGURATION SUMMARY")
    print("="*70)
    print(f"Facility: {config.facility_name} ({config.facility_type})")
    print(f"Location: {config.location}")
    print(f"City Priority: Level {config.city_priority_level} (Hospital=1, University=2, Residential=3, Industrial=4)")
    
    print(f"\n📊 LOAD PROFILE (REALISTIC RESIDENTIAL):")
    print(f"  Peak Load:          {config.load_profile.peak_load} kW (Evening peak)")
    print(f"  Average Load:       {config.load_profile.average_load:.1f} kW")
    print(f"  │")
    print(f"  ┣━━ 🔴 CRITICAL:    {config.load_profile.total_critical_load} kW (17%)")
    print(f"  │   ├─ Lifts (Emergency):  {config.load_profile.critical_loads['lifts_emergency']} kW")
    print(f"  │   ├─ Water Pumps:        {config.load_profile.critical_loads['water_pumps']} kW")
    print(f"  │   ├─ Emergency Lighting: {config.load_profile.critical_loads['emergency_lighting']} kW")
    print(f"  │   ├─ Security Systems:   {config.load_profile.critical_loads['security_systems']} kW")
    print(f"  │   └─ Common Services:    {config.load_profile.critical_loads['common_services']} kW")
    print(f"  │")
    print(f"  ┗━━ NON-CRITICAL:  {config.load_profile.total_non_critical_load} kW (83%) - SHEDDABLE")
    print(f"      ├─ Air Conditioning:   {config.load_profile.air_conditioning_kw} kW (Priority 5)")
    print(f"      ├─ EV Charging:        {config.load_profile.ev_charging_kw} kW (Priority 6 - SHED FIRST)")
    print(f"      ├─ Washing Machines:   {config.load_profile.washing_machines_kw} kW (Priority 4)")
    print(f"      └─ Common Lighting:    {config.load_profile.common_area_lighting_kw} kW (Priority 3)")
    
    print(f"\n🔋 BATTERY STORAGE (LIMITED):")
    print(f"  Capacity:           {config.battery.usable_capacity_kwh} kWh usable")
    print(f"  Power:              {config.battery.max_discharge_power_kw} kW discharge")
    print(f"  Critical Backup:    {config.battery.critical_backup_hours:.1f} hours @ 100 kW")
    print(f"  ⚠️  CANNOT sustain peak load ({config.load_profile.peak_load} kW)")
    print(f"  Efficiency:         {config.battery.round_trip_efficiency*100:.1f}% round-trip")
    
    print(f"\n☀️ SOLAR PV (PARTIAL COVERAGE):")
    print(f"  Capacity:           {config.pv.installed_capacity_kwp} kWp")
    print(f"  Peak Generation:    {config.pv.peak_generation_kw:.0f} kW")
    print(f"  Daily Generation:   {config.pv.estimated_daily_generation_kwh:.0f} kWh (estimated)")
    print(f"  Daily Consumption:  {config.load_profile.average_load * 24:.0f} kWh")
    print(f"  Solar Coverage:     {config.pv.estimated_daily_generation_kwh / (config.load_profile.average_load * 24) * 100:.1f}%")
    print(f"  ⚠️  NOT net-zero capable")
    
    print(f"\n⚡ GENERATOR (LAST RESORT):")
    print(f"  Generator 1:        {config.generator.gen1_rated_power_kw} kW")
    print(f"  ⚠️  Cannot handle peak load ({config.load_profile.peak_load} kW)")
    print(f"  Fuel Tank:          {config.generator.fuel_tank_capacity_liters} liters")
    print(f"  Fuel Consumption:   {config.generator.fuel_consumption_l_per_kwh} L/kWh")
    print(f"  Max Runtime:        ~{config.generator.fuel_tank_capacity_liters / (config.generator.rated_power_kw * config.generator.fuel_consumption_l_per_kwh):.1f}h at full load")
    print(f"  Startup Time:       {config.generator.startup_time_seconds} seconds")
    print(f"  Auto-start SoC:     {config.generator.auto_start_soc_threshold}%")
    
    print(f"\n🎛️ CONTROL SYSTEM (SURVIVAL MODE):")
    print(f"  Time Resolution:    {config.control.time_resolution_minutes} minutes")
    print(f"  Islanding Detection: {config.control.islanding_detection_time_ms} ms")
    print(f"  Critical Priority:  {config.control.enforce_critical_load_priority}")
    print(f"  Max Shedding:       {config.control.max_total_shed_kw} kW (ALL non-critical)")
    print(f"  Load Shed Priority: EV → AC → Washing → Lighting")
    print(f"  Restore Margin:     {config.control.restore_margin_ratio*100:.0f}% (cautious)")
    
    print("\n⚠️ RESIDENTIAL REALITY:")
    print("  • Residents WILL experience discomfort during extended outages")
    print("  • AC will be shed - expect hot apartments")
    print("  • EV charging disabled immediately")
    print("  • Only safety-critical systems protected")
    print("  • Generator fuel is finite - cannot sustain indefinitely")
    print("  • Battery cannot handle peak evening load")
    
    print("\n🏙️ CITY-LEVEL PRIORITY:")
    print("  Priority Level: 3 (Medium)")
    print("  • Lower priority than Hospital (1) and University (2)")
    print("  • Higher priority than Industrial (4)")
    print("  • Justification: Safety-critical services protected,")
    print("                   but comfort sacrificed for city resilience")
    
    print("\n" + "="*70)
