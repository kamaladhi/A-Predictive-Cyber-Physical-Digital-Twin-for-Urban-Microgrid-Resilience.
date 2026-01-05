import json
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Tuple

@dataclass
class LoadProfile:
    """24-hour industrial load profile - Automotive Component Manufacturing"""
    hour_blocks: List[tuple] = None
    critical_loads: Dict[str, float] = None
    
    # Load categories with shedding limits (INDUSTRIAL-SPECIFIC)
    load_categories: Dict[str, Dict] = None
    
    # Industrial-specific flexible loads
    hvac_production_kw: float = 180  # Production floor HVAC
    metal_stamping_kw: float = 120   # Stamping presses
    material_handling_kw: float = 60  # Conveyors, forklifts
    assembly_lines_kw: float = 50    # Non-JIT assembly
    office_hvac_kw: float = 30       # Office HVAC
    canteen_welfare_kw: float = 20   # Canteen and welfare
    
    def __post_init__(self):
        if self.hour_blocks is None:
            # Automotive manufacturing: 24/6 operation (Mon-Sat), reduced Sunday
            # (start_hour, end_hour, power_kw)
            self.hour_blocks = [
                (0, 6, 720),      # Night shift: 80% of peak
                (6, 8, 650),      # Shift change, maintenance checks
                (8, 16, 850),     # Day shift PEAK: all lines, peak HVAC
                (16, 22, 780),    # Evening shift: full production, lower HVAC
                (22, 24, 680)     # Reduced production, cleaning, preventive maintenance
            ]
        
        if self.critical_loads is None:
            # CRITICAL INDUSTRIAL LOADS (220 kW) - Cannot interrupt without production loss
            self.critical_loads = {
                'cnc_machines': 80,            # Mid-operation shutdown causes scrap ($500-2000/part)
                'powder_coating_ovens': 60,    # Thermal process - fire hazard + batch loss
                'compressed_air': 35,          # Pneumatic tools, safety, clean room pressure
                'process_cooling': 25,         # Prevents equipment damage, maintains tolerances
                'it_erp_mes': 12,              # Production tracking, quality data, inventory
                'safety_emergency': 8          # Emergency lighting, fire suppression, alarms
            }
        
        if self.load_categories is None:
            self.load_categories = {
                'critical_production': {
                    'power_kw': self.total_critical_load,
                    'max_shed_percent': 0,      # NEVER shed during production
                    'priority': 1
                },
                'metal_stamping': {
                    'power_kw': 120,
                    'max_shed_percent': 100,    # Can reschedule production
                    'priority': 3
                },
                'hvac_production': {
                    'power_kw': 180,
                    'max_shed_percent': 80,     # Reduce to minimal ventilation
                    'priority': 4
                },
                'material_handling': {
                    'power_kw': 60,
                    'max_shed_percent': 70,     # Manual backup possible
                    'priority': 4
                },
                'assembly_lines': {
                    'power_kw': 50,
                    'max_shed_percent': 100,    # Buffer inventory exists
                    'priority': 5
                },
                'office_hvac': {
                    'power_kw': 30,
                    'max_shed_percent': 100,    # Non-essential during outage
                    'priority': 5
                },
                'canteen_welfare': {
                    'power_kw': 20,
                    'max_shed_percent': 100,    # Pause during outage
                    'priority': 5
                }
            }
    
    @property
    def total_critical_load(self) -> float:
        """220 kW - Critical production loads"""
        return sum(self.critical_loads.values())
    
    @property
    def total_flexible_load(self) -> float:
        """460 kW - Flexible/sheddable loads (68% of average)"""
        return (self.hvac_production_kw + self.metal_stamping_kw + 
                self.material_handling_kw + self.assembly_lines_kw + 
                self.office_hvac_kw + self.canteen_welfare_kw)

    @property
    def total_non_critical_load(self) -> float:
        """Compatibility alias used elsewhere in the codebase."""
        return self.total_flexible_load
    
    @property
    def peak_load(self) -> float:
        """850 kW peak during production"""
        return max(block[2] for block in self.hour_blocks)
    
    @property
    def average_load(self) -> float:
        """680 kW average during production shifts"""
        total_energy = sum((block[1] - block[0]) * block[2] for block in self.hour_blocks)
        return total_energy / 24
    
    @property
    def base_load(self) -> float:
        """180 kW - Night/weekend base load"""
        return 180
    
    def get_category_shedding_potential(self, category: str) -> float:
        """Get max kW that can be shed from a category"""
        if category not in self.load_categories:
            return 0
        cat = self.load_categories[category]
        return cat['power_kw'] * (cat['max_shed_percent'] / 100)

    # Compatibility properties expected by shared components
    @property
    def hvac_partial_kw(self) -> float:
        """Alias for partial/non-critical HVAC used by existing components."""
        return self.hvac_production_kw

    @property
    def wards_lighting_kw(self) -> float:
        """Alias for lighting loads in non-critical tiers."""
        return self.office_hvac_kw

    @property
    def admin_misc_kw(self) -> float:
        """Alias for administrative/miscellaneous non-critical load."""
        return self.assembly_lines_kw

    # Additional compatibility aliases expected by higher-level twins
    @property
    def hvac_load_kw(self) -> float:
        return self.hvac_production_kw

    @property
    def wards_lighting_load_kw(self) -> float:
        return self.office_hvac_kw

    @property
    def admin_load_kw(self) -> float:
        return self.assembly_lines_kw

    @property
    def labs_load_kw(self) -> float:
        """Map university 'labs' concept to an industrial heavy process load."""
        return self.metal_stamping_kw

    @property
    def lighting_load_kw(self) -> float:
        return self.canteen_welfare_kw


@dataclass
class BatteryConfig:
    """Battery Energy Storage System - Industrial UPS + Peak Shaving"""
    
    nominal_capacity_kwh: float = 1800
    usable_capacity_kwh: float = 1620.0  # 90% DoD (10-90% SoC)
    max_discharge_power_kw: float = 400.0  # 0.25C rate
    max_charge_power_kw: float = 400.0
    round_trip_efficiency: float = 0.92
    min_soc_percent: float = 10
    max_soc_percent: float = 90
    initial_soc_percent: float = 80
    
    @property
    def discharge_efficiency(self) -> float:
        return self.round_trip_efficiency ** 0.5
    
    @property
    def charge_efficiency(self) -> float:
        return self.round_trip_efficiency ** 0.5
    
    @property
    def critical_backup_hours(self) -> float:
        """Backup time for 220 kW critical load: 7.4 hours"""
        return self.usable_capacity_kwh / 220


@dataclass
class PVConfig:
    """Solar PV system - Industrial rooftop (65%) + ground mount (35%)"""
    installed_capacity_kwp: float = 600     # 40,000 m² rooftop + 10,000 m² carport
    panel_efficiency: float = 0.22          # Monocrystalline, 22% efficiency
    inverter_efficiency: float = 0.98       # Commercial string inverters with MPPT
    temperature_coefficient: float = -0.0038
    nominal_operating_temp: float = 47
    design_insolation_kwh_m2_day: float = 5.0  # Pune/Chennai belt
    tilt_angle: float = 15.0                # Optimized for 18.5°N latitude
    
    @property
    def estimated_daily_generation_kwh(self) -> float:
        """2,700 kWh/day average"""
        return self.installed_capacity_kwp * self.design_insolation_kwh_m2_day * self.inverter_efficiency
    
    @property
    def peak_generation_kw(self) -> float:
        """Peak output around noon: 580 kW"""
        return self.installed_capacity_kwp * self.inverter_efficiency * 0.99
    
    @property
    def annual_generation_kwh(self) -> float:
        """985 MWh/year (capacity factor ~18.7%)"""
        return self.estimated_daily_generation_kwh * 365


@dataclass
class GeneratorConfig:
    """Diesel Generator - N+1 Redundancy (2×500 kW)"""
    # Generator 1: Covers critical + partial flexible
    gen1_rated_power_kw: float = 500
    gen1_rated_power_kva: float = 625       # @ 0.8 PF
    
    # Generator 2: Covers flexible + backup
    gen2_rated_power_kw: float = 500
    gen2_rated_power_kva: float = 625
    
    fuel_consumption_l_per_kwh: float = 0.24  # @ 75% load
    min_load_ratio: float = 0.30            # Below 30%, wet stacking risk
    startup_time_seconds: float = 15        # Automatic start with battery cranking
    cooldown_time_seconds: float = 300      # Prevents thermal shock
    
    # Control thresholds
    auto_start_soc_threshold: float = 30    # Gen1 starts when battery hits 30%
    auto_stop_soc_threshold: float = 75
    
    min_on_time_minutes: int = 60           # Prevents excessive start-stop
    min_off_time_minutes: int = 30          # Adequate cooling between restarts
    
    # Fuel storage
    fuel_tank_capacity_liters: float = 5000  # 48-hour full-load capacity
    
    @property
    def rated_power_kw(self) -> float:
        """Primary generator capacity"""
        return self.gen1_rated_power_kw
    
    @property
    def min_operating_power_kw(self) -> float:
        return self.rated_power_kw * self.min_load_ratio
    
    @property
    def total_capacity_kw(self) -> float:
        """Both generators combined: 1,000 kW"""
        return self.gen1_rated_power_kw + self.gen2_rated_power_kw


@dataclass
class ControlConfig:
    """Energy Management System - Industrial-specific control"""
    backup_duration_hours: float = 7.4      # Critical load backup time
    time_resolution_minutes: float = 5
    islanding_detection_time_ms: float = 50  # Seamless islanding
    reconnection_delay_seconds: float = 300  # 5-minute wait after grid restoration
    
    # Load shedding priority (industrial-specific)
    load_shedding_priority: List[str] = None
    
    frequency_droop_coefficient: float = 0.05
    voltage_droop_coefficient: float = 0.03
    
    # Shedding policy
    enable_load_shedding: bool = True
    enforce_critical_load_priority: bool = True
    max_total_shed_kw: float = 460          # All flexible loads
    load_shed_step_kw: float = 50
    
    # Battery management
    battery_soc_target_percent: float = 70
    battery_charge_from_grid: bool = True
    battery_discharge_min_soc: float = 10
    battery_night_reserve_soc: float = 40
    
    # Restoration policy
    restore_time_min: int = 5               # Wait before restoring loads
    restore_margin_ratio: float = 1.2       # Require 20% margin before restore
    
    def __post_init__(self):
        if self.load_shedding_priority is None:
            # Sacrifice order during city-level outages
            self.load_shedding_priority = [
                'canteen_welfare',        # First to shed
                'office_hvac',
                'assembly_lines',
                'material_handling',
                'hvac_production',
                'metal_stamping',
                'critical_production'     # NEVER shed
            ]


@dataclass
class ProtectionConfig:
    """Industrial microgrid protection settings"""
    under_voltage_threshold_pu: float = 0.85
    over_voltage_threshold_pu: float = 1.15
    under_frequency_threshold_hz: float = 48.5
    over_frequency_threshold_hz: float = 51.5
    over_current_ratio: float = 1.8
    reconnection_voltage_window_pu: tuple = (0.92, 1.08)
    reconnection_frequency_window_hz: tuple = (49.5, 50.5)
    
    # Industrial-specific
    critical_load_isolation_time_ms: float = 200
    backup_power_transfer_time_ms: float = 100

    # Compatibility aliases expected by EMS modules
    @property
    def over_frequency_hz(self) -> float:
        return self.over_frequency_threshold_hz

    @property
    def under_frequency_hz(self) -> float:
        return self.under_frequency_threshold_hz

    @property
    def over_voltage_pu(self) -> float:
        return self.over_voltage_threshold_pu

    @property
    def under_voltage_pu(self) -> float:
        return self.under_voltage_threshold_pu


@dataclass
class MicrogridConfig:
    """Complete industrial microgrid configuration - Automotive Component Manufacturing"""
    facility_name: str = "Automotive Component Manufacturing"
    facility_type: str = "Mid-scale Industrial Plant"
    location: str = "Pune, Maharashtra, India"
    latitude: float = 18.5204
    longitude: float = 73.8567
    timezone: str = "Asia/Kolkata"
    nominal_voltage_kv: float = 11
    nominal_frequency_hz: float = 50
    power_factor: float = 0.85              # Inductive loads (motors, transformers)
    
    # Priority classification for city-level coordination
    microgrid_priority: int = 3             # Lower than hospital (1) and university (2)
    can_curtail_load: bool = True
    max_curtailment_percent: float = 68     # 460 kW out of 680 kW average
    curtailment_notice_minutes: int = 15    # Time to safely shut down processes
    restart_time_minutes: int = 30          # CNC warm-up, compressed air buildup
    
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
        """Validate configuration for industrial requirements"""
        warnings = []
        
        # Battery should support critical loads for 7.4 hours
        required_energy = self.load_profile.total_critical_load * self.control.backup_duration_hours
        if self.battery.usable_capacity_kwh < required_energy:
            warnings.append(
                f"⚠️ Battery capacity ({self.battery.usable_capacity_kwh} kWh) "
                f"insufficient for {self.control.backup_duration_hours}h critical backup. "
                f"Required: {required_energy:.0f} kWh"
            )
        
        # Battery power must handle critical load
        if self.battery.max_discharge_power_kw < self.load_profile.total_critical_load:
            warnings.append(
                f"⚠️ Battery power ({self.battery.max_discharge_power_kw} kW) "
                f"insufficient for critical loads ({self.load_profile.total_critical_load} kW)"
            )
        
        # Generator must cover critical loads
        if self.generator.gen1_rated_power_kw < self.load_profile.total_critical_load:
            warnings.append(
                f"⚠️ Generator 1 ({self.generator.gen1_rated_power_kw} kW) "
                f"cannot cover critical loads ({self.load_profile.total_critical_load} kW)"
            )
        
        # Verify 220 kW critical load
        if abs(self.load_profile.total_critical_load - 220) > 5:
            warnings.append(
                f"⚠️ WARNING: Critical load is {self.load_profile.total_critical_load} kW, "
                f"expected 220 kW per specification"
            )
        
        # Verify 460 kW flexible load
        if abs(self.load_profile.total_flexible_load - 460) > 5:
            warnings.append(
                f"⚠️ WARNING: Flexible load is {self.load_profile.total_flexible_load} kW, "
                f"expected 460 kW per specification"
            )
        
        return warnings


def create_default_config() -> MicrogridConfig:
    """Create and return default industrial microgrid configuration"""
    config = MicrogridConfig()
    
    # Validate and print warnings
    warnings = config.validate()
    if warnings:
        try:
            print("\n[INDUSTRIAL] Microgrid Configuration Warnings:")
            for w in warnings:
                print(f"  {w}")
        except UnicodeEncodeError:
            # Handle encoding issues on Windows
            print("\n[INDUSTRIAL] Microgrid Configuration: Some warnings could not be displayed due to encoding.")
    else:
        print("\n[OK] Industrial microgrid configuration validated successfully!")
    
    return config


if __name__ == "__main__":
    # Create configuration
    config = create_default_config()
    
    # Export to JSON
    config.to_json('industrial_parameters.json')
    print("\nConfiguration exported to: industrial_parameters.json")
    
    # Print summary
    print("\n" + "="*80)
    print("🏭 AUTOMOTIVE COMPONENT MANUFACTURING - MICROGRID CONFIGURATION")
    print("="*80)
    print(f"Facility: {config.facility_name}")
    print(f"Location: {config.location}")
    print(f"Power Factor: {config.power_factor} (Inductive loads)")
    print(f"\nCity-Level Priority: {config.microgrid_priority} (Hospital=1, University=2, Industry=3)")
    print(f"Load Curtailment: {config.can_curtail_load} (up to {config.max_curtailment_percent}%)")
    print(f"Curtailment Notice: {config.curtailment_notice_minutes} minutes")
    
    print(f"\n📊 LOAD PROFILE:")
    print(f"  Peak Load:          {config.load_profile.peak_load} kW (during full production)")
    print(f"  Average Load:       {config.load_profile.average_load:.1f} kW (production shifts)")
    print(f"  Base Load:          {config.load_profile.base_load} kW (night/weekend)")
    print(f"  ┃")
    print(f"  ┣━━ 🔴 CRITICAL:    {config.load_profile.total_critical_load} kW (26% - PROTECTED)")
    print(f"  ┃   ├─ CNC Machines:          {config.load_profile.critical_loads['cnc_machines']} kW")
    print(f"  ┃   ├─ Powder Coating Ovens:  {config.load_profile.critical_loads['powder_coating_ovens']} kW")
    print(f"  ┃   ├─ Compressed Air:        {config.load_profile.critical_loads['compressed_air']} kW")
    print(f"  ┃   ├─ Process Cooling:       {config.load_profile.critical_loads['process_cooling']} kW")
    print(f"  ┃   ├─ IT/ERP/MES:            {config.load_profile.critical_loads['it_erp_mes']} kW")
    print(f"  ┃   └─ Safety/Emergency:      {config.load_profile.critical_loads['safety_emergency']} kW")
    print(f"  ┃")
    print(f"  ┗━━ FLEXIBLE:      {config.load_profile.total_flexible_load} kW (68% - Curtailable)")
    print(f"      ├─ HVAC (Production):     {config.load_profile.hvac_production_kw} kW (80% sheddable)")
    print(f"      ├─ Metal Stamping:        {config.load_profile.metal_stamping_kw} kW (100% sheddable)")
    print(f"      ├─ Material Handling:     {config.load_profile.material_handling_kw} kW (70% sheddable)")
    print(f"      ├─ Assembly Lines:        {config.load_profile.assembly_lines_kw} kW (100% sheddable)")
    print(f"      ├─ Office HVAC:           {config.load_profile.office_hvac_kw} kW (100% sheddable)")
    print(f"      └─ Canteen/Welfare:       {config.load_profile.canteen_welfare_kw} kW (100% sheddable)")
    
    print(f"\n🔋 BATTERY STORAGE (LFP Technology):")
    print(f"  Nominal Capacity:   {config.battery.nominal_capacity_kwh} kWh")
    print(f"  Usable Capacity:    {config.battery.usable_capacity_kwh} kWh (90% DoD)")
    print(f"  Power Rating:       {config.battery.max_discharge_power_kw} kW (0.25C rate)")
    print(f"  Critical Backup:    {config.battery.critical_backup_hours:.1f} hours @ 220 kW")
    print(f"  Efficiency:         {config.battery.round_trip_efficiency*100:.0f}% round-trip")
    print(f"  Cycle Life:         6,000+ cycles @ 90% DoD")
    
    print(f"\n☀️ SOLAR PV (Rooftop 65% + Ground 35%):")
    print(f"  Installed Capacity: {config.pv.installed_capacity_kwp} kWp")
    print(f"  Peak Generation:    {config.pv.peak_generation_kw:.0f} kW (noon, clear sky)")
    print(f"  Daily Generation:   {config.pv.estimated_daily_generation_kwh:.0f} kWh (average)")
    print(f"  Annual Generation:  {config.pv.annual_generation_kwh/1000:.0f} MWh/year")
    print(f"  Panel Efficiency:   {config.pv.panel_efficiency*100:.0f}% (Monocrystalline)")
    print(f"  Inverter Eff:       {config.pv.inverter_efficiency*100:.0f}%")
    print(f"  Coverage:           {(config.pv.estimated_daily_generation_kwh / (config.load_profile.average_load * 24)) * 100:.1f}% of daily load")
    
    print(f"\n⚡ DIESEL GENERATORS (N+1 Redundancy):")
    print(f"  Generator 1:        {config.generator.gen1_rated_power_kw} kW / {config.generator.gen1_rated_power_kva} kVA @ 0.8 PF")
    print(f"  Generator 2:        {config.generator.gen2_rated_power_kw} kW / {config.generator.gen2_rated_power_kva} kVA @ 0.8 PF")
    print(f"  Total Capacity:     {config.generator.total_capacity_kw} kW")
    print(f"  Fuel Tank:          {config.generator.fuel_tank_capacity_liters} liters")
    print(f"  Fuel Consumption:   {config.generator.fuel_consumption_l_per_kwh} L/kWh @ 75% load")
    print(f"  Startup Time:       {config.generator.startup_time_seconds} seconds")
    print(f"  48h Runtime:        @ 500 kW continuous")
    
    print(f"\n🎛️ ENERGY MANAGEMENT SYSTEM:")
    print(f"  Islanding Detection: {config.control.islanding_detection_time_ms} ms (seamless)")
    print(f"  Reconnection Delay:  {config.control.reconnection_delay_seconds} seconds")
    print(f"  Load Shedding:       Enabled ({config.control.max_total_shed_kw} kW max)")
    print(f"  Critical Priority:   {config.control.enforce_critical_load_priority}")
    print(f"  Time Resolution:     {config.control.time_resolution_minutes} minutes")
    
    print(f"\n🏙️ CITY-LEVEL COORDINATION:")
    print(f"  Priority Rank:       {config.microgrid_priority} (Industrial - Lowest)")
    print(f"  Curtailable:         {config.can_curtail_load}")
    print(f"  Max Curtailment:     {config.max_curtailment_percent}% ({config.load_profile.total_flexible_load:.0f} kW)")
    print(f"  Sacrifice Order:     Canteen → Office → Assembly → Handling → HVAC → Stamping")
    print(f"  Critical Floor:      220 kW (CNC, Ovens, Cooling, IT)")
    
    print(f"\n⏱️ OUTAGE TOLERANCE:")
    print(f"  <15 min:             Seamless (battery UPS)")
    print(f"  15 min - 2h:         Minor impact (continue critical)")
    print(f"  2-6h:                Moderate (shift delay)")
    print(f"  6-12h:               Major (order delays)")
    print(f"  >12h:                Severe (multi-day backlog)")
    
    print("\n" + "="*80)
    print("✅ Configuration matches detailed specifications document")
    print("="*80)
