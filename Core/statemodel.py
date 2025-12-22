from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional
from enum import Enum
import json


class AlertLevel(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class DRActionType(Enum):
    """Demand Response action types"""
    LOAD_SHIFT = "load_shift"
    BATTERY_CHARGE = "battery_charge"
    BATTERY_DISCHARGE = "battery_discharge"
    REDUCE_LOAD = "reduce_load"
    GRID_EXPORT = "grid_export"


@dataclass
class ApplianceState:
    """Individual appliance state"""
    name: str
    power: float  # kW
    status: str  # 'on', 'off', 'standby'
    is_shiftable: bool = False
    priority: int = 5  # 1=critical, 10=optional
    uncertainty: float = 0.0  # Power uncertainty (kW) - ADDED per paper
    
    def to_dict(self):
        return asdict(self)


@dataclass
class GenerationState:
    """Renewable generation state with uncertainty (paper's UT requirement)"""
    solar: float = 0.0  # kW
    wind: float = 0.0  # kW
    fuel_cell: float = 0.0  # kW
    total: float = 0.0  # kW
    solar_uncertainty: float = 0.0  # Paper's UT for RER
    wind_uncertainty: float = 0.0
    fuel_cell_uncertainty: float = 0.0
    
    def calculate_total(self):
        self.total = self.solar + self.wind + self.fuel_cell
    
    def to_dict(self):
        return asdict(self)


@dataclass
class BatteryState:
    """Energy storage state"""
    soc: float  # State of Charge (0-100%)
    capacity: float  # kWh
    charging_power: float = 0.0  # kW
    discharging_power: float = 0.0  # kW
    max_charge_rate: float = 5.0  # kW
    max_discharge_rate: float = 5.0  # kW
    efficiency: float = 0.95  # round-trip efficiency
    
    @property
    def energy_stored(self) -> float:
        """Current energy in battery (kWh)"""
        return (self.soc / 100.0) * self.capacity
    
    @property
    def is_charging(self) -> bool:
        return self.charging_power > 0
    
    @property
    def is_discharging(self) -> bool:
        return self.discharging_power > 0
    
    def to_dict(self):
        return asdict(self)


@dataclass
class GridInteraction:
    """Grid import/export state with price"""
    import_power: float = 0.0  # kW from grid
    export_power: float = 0.0  # kW to grid
    current_price: float = 0.035  # $/kWh
    price_uncertainty: float = 0.0  # ADDED - market expense uncertainty (paper)
    
    @property
    def net_power(self) -> float:
        """Negative = importing, Positive = exporting"""
        return self.export_power - self.import_power
    
    def to_dict(self):
        return asdict(self)


@dataclass
class ForecastState:
    """Future predictions with uncertainty (paper's UT method)"""
    timestamp: datetime
    solar_forecast: List[Dict[str, float]] = field(default_factory=list)
    wind_forecast: List[Dict[str, float]] = field(default_factory=list)
    price_forecast: List[Dict[str, float]] = field(default_factory=list)
    load_forecast: List[Dict[str, float]] = field(default_factory=list)  # Paper's stochastic demand
    uncertainty_method: str = "unscented_transform"  # Paper's UT
    
    def get_peak_price_time(self) -> Optional[datetime]:
        """Find when price is highest"""
        if not self.price_forecast:
            return None
        peak = max(self.price_forecast, key=lambda x: x['value'])
        return datetime.fromisoformat(peak['time'])
    
    def get_peak_price_value(self) -> float:
        """Get maximum predicted price"""
        if not self.price_forecast:
            return 0.0
        peak = max(self.price_forecast, key=lambda x: x['value'])
        return peak['value']
    
    def to_dict(self):
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        return data


@dataclass
class MicrogridMetrics:
    """Calculated performance metrics"""
    self_consumption_ratio: float = 0.0
    renewable_penetration: float = 0.0
    grid_independence: float = 0.0
    cost_per_kwh: float = 0.0
    co2_intensity: float = 0.0  # kg CO2/kWh
    emissions_intensity: float = 0.0  # ADDED - paper's net-zero emissions control
    
    def to_dict(self):
        return asdict(self)


@dataclass
class MicrogridState:
    """
    Complete virtual microgrid state - PAPER COMPLIANT
    Includes all uncertainty modeling per paper's requirements
    """
    timestamp: datetime
    
    # Load information (from NILM)
    total_load: float = 0.0  # kW
    appliances: Dict[str, ApplianceState] = field(default_factory=dict)
    nilm_confidence: float = 0.0
    total_load_uncertainty: float = 0.0  # ADDED - paper's stochastic load modeling
    
    # Generation (from forecasts)
    generation: GenerationState = field(default_factory=GenerationState)
    
    # Storage
    battery: BatteryState = field(default_factory=lambda: BatteryState(
        soc=50.0, capacity=10.0
    ))
    
    # Grid interaction
    grid: GridInteraction = field(default_factory=GridInteraction)
    
    # Forecasts
    forecast: Optional[ForecastState] = None
    
    # Calculated metrics
    metrics: MicrogridMetrics = field(default_factory=MicrogridMetrics)
    
    # System status
    is_healthy: bool = True
    error_messages: List[str] = field(default_factory=list)
    
    # ADDED - Emissions tracking (paper's net-zero requirement)
    emissions_intensity: float = 0.0  # kg CO2/kWh
    
    def calculate_power_balance(self):
        """Ensure power balance: Generation + Grid = Load + Battery"""
        total_generation = self.generation.total
        total_consumption = (
            self.total_load + 
            self.battery.charging_power - 
            self.battery.discharging_power
        )
        
        difference = total_consumption - total_generation
        
        if difference > 0:
            self.grid.import_power = difference
            self.grid.export_power = 0.0
        else:
            self.grid.export_power = abs(difference)
            self.grid.import_power = 0.0
    
    def update_metrics(self):
        """Calculate derived metrics"""
        # Self-consumption ratio
        if self.generation.total > 0:
            local_consumption = min(self.total_load, self.generation.total)
            self.metrics.self_consumption_ratio = (
                local_consumption / self.generation.total
            )
        
        # Renewable penetration
        total_supply = self.generation.total + self.grid.import_power
        if total_supply > 0:
            self.metrics.renewable_penetration = (
                self.generation.total / total_supply
            )
        
        # Grid independence
        self.metrics.grid_independence = (
            1.0 - (self.grid.import_power / (self.total_load + 1e-9))
        )
        
        # Cost calculation
        cost = self.grid.import_power * self.grid.current_price
        revenue = self.grid.export_power * (self.grid.current_price * 0.7)
        net_cost = cost - revenue
        
        if self.total_load > 0:
            self.metrics.cost_per_kwh = net_cost / self.total_load
        
        # ADDED - Emissions calculation (paper's net-zero control)
        self._calculate_emissions()
    
    def _calculate_emissions(self):
        """Calculate CO2 emissions intensity (paper requirement)"""
        # Emission factors (kg CO2/kWh)
        GRID_EMISSION_FACTOR = 0.5  # Grid electricity
        FC_EMISSION_FACTOR = 0.3  # Fuel cell
        RENEWABLE_EMISSION = 0.0  # Solar/wind
        
        total_emissions = (
            self.grid.import_power * GRID_EMISSION_FACTOR +
            self.generation.fuel_cell * FC_EMISSION_FACTOR +
            (self.generation.solar + self.generation.wind) * RENEWABLE_EMISSION
        )
        
        total_supply = self.generation.total + self.grid.import_power
        if total_supply > 0:
            self.emissions_intensity = total_emissions / total_supply
            self.metrics.emissions_intensity = self.emissions_intensity
        else:
            self.emissions_intensity = 0.0
            self.metrics.emissions_intensity = 0.0
    
    def validate(self) -> bool:
        """Check if state is physically valid"""
        errors = []
        
        # Check for negative values
        if self.total_load < 0:
            errors.append("Negative total load")
        
        if self.battery.soc < 0 or self.battery.soc > 100:
            errors.append(f"Invalid battery SOC: {self.battery.soc}%")
        
        # ADDED - Check uncertainty values
        if self.total_load_uncertainty < 0:
            errors.append("Negative load uncertainty")
        
        if self.generation.solar_uncertainty < 0 or self.generation.wind_uncertainty < 0:
            errors.append("Negative generation uncertainty")
        
        # Check appliance sum matches total load
        appliance_sum = sum(app.power for app in self.appliances.values())
        tolerance = 0.5  # 0.5 kW tolerance
        
        if abs(appliance_sum - self.total_load) > tolerance:
            errors.append(
                f"Appliance sum ({appliance_sum:.2f}kW) != "
                f"Total load ({self.total_load:.2f}kW)"
            )
        
        self.error_messages = errors
        self.is_healthy = len(errors) == 0
        
        return self.is_healthy
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'timestamp': self.timestamp.isoformat(),
            'total_load': self.total_load,
            'total_load_uncertainty': self.total_load_uncertainty,
            'appliances': {
                name: app.to_dict() 
                for name, app in self.appliances.items()
            },
            'nilm_confidence': self.nilm_confidence,
            'generation': self.generation.to_dict(),
            'battery': self.battery.to_dict(),
            'grid': self.grid.to_dict(),
            'forecast': self.forecast.to_dict() if self.forecast else None,
            'metrics': self.metrics.to_dict(),
            'emissions_intensity': self.emissions_intensity,
            'is_healthy': self.is_healthy,
            'error_messages': self.error_messages
        }
    
    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class DRAlert:
    """Demand Response alert"""
    timestamp: datetime
    level: AlertLevel
    alert_type: str
    message: str
    recommended_actions: List[str] = field(default_factory=list)
    potential_savings: float = 0.0
    expires_at: Optional[datetime] = None
    
    def to_dict(self):
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['level'] = self.level.value
        if self.expires_at:
            data['expires_at'] = self.expires_at.isoformat()
        return data
    
    def to_json(self):
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class DRAction:
    """Demand Response action recommendation"""
    timestamp: datetime
    action_type: DRActionType
    target_appliance: Optional[str] = None
    target_power: float = 0.0  # kW
    scheduled_time: Optional[datetime] = None
    reason: str = ""
    expected_savings: float = 0.0  # $
    
    def to_dict(self):
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['action_type'] = self.action_type.value
        if self.scheduled_time:
            data['scheduled_time'] = self.scheduled_time.isoformat()
        return data