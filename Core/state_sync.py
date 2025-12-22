import json
import logging
from datetime import datetime
from typing import Dict, Optional
import redis

from Core.statemodel import (
    MicrogridState, ApplianceState, GenerationState, 
    BatteryState, GridInteraction, ForecastState
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StateSynchronizer:
    """
    Synchronizes data from multiple sources into unified microgrid state
    Core responsibility: Keep the "virtual microgrid" aligned with reality
    """
    
    def __init__(self, redis_host='localhost', redis_port=6379):
        # Connect to Redis for fast state storage
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            decode_responses=True
        )
        
        # Current state
        self.current_state: Optional[MicrogridState] = None
        
        # Initialize default state
        self._initialize_state()
        
        logger.info("State Synchronizer initialized")
    
    def _initialize_state(self):
        """Create initial state"""
        self.current_state = MicrogridState(
            timestamp=datetime.now(),
            total_load=0.0,
            battery=BatteryState(soc=50.0, capacity=10.0),
            grid=GridInteraction()
        )
        
        # Store in Redis
        self._persist_state()
        logger.info("Initial state created")
    
    def update_from_nilm(self, nilm_data: dict):
        """
        Update state with NILM data from Person 1
        
        Expected format:
        {
            "timestamp": "2024-12-07T14:30:00Z",
            "total_load": 4.5,
            "appliances": {
                "refrigerator": 0.15,
                "air_conditioner": 2.3,
                ...
            },
            "confidence": 0.92
        }
        """
        try:
            timestamp = datetime.fromisoformat(nilm_data['timestamp'])
            
            # Update timestamp
            self.current_state.timestamp = timestamp
            
            # Update total load
            self.current_state.total_load = nilm_data['total_load']
            self.current_state.nilm_confidence = nilm_data.get('confidence', 0.0)
            
            # Handle uncertainty (proxy from confidence or direct key)
            if 'uncertainty' in nilm_data:
                self.current_state.total_load_uncertainty = nilm_data['uncertainty']
            else:
                # Derive from confidence as fallback (aligns with paper's stochastic load modeling)
                self.current_state.total_load_uncertainty = 1 - self.current_state.nilm_confidence
            
            # Update appliances
            self.current_state.appliances.clear()
            
            for name, power in nilm_data['appliances'].items():
                # Determine if appliance is shiftable
                is_shiftable = self._is_appliance_shiftable(name)
                priority = self._get_appliance_priority(name)
                
                appliance = ApplianceState(
                    name=name,
                    power=power,
                    status='on' if power > 0.01 else 'off',
                    is_shiftable=is_shiftable,
                    priority=priority
                )
                
                self.current_state.appliances[name] = appliance
            
            # Recalculate power balance
            self.current_state.calculate_power_balance()
            self.current_state.update_metrics()
            
            # Validate state
            if not self.current_state.validate():
                logger.warning(f"State validation warnings: "
                            f"{self.current_state.error_messages}")
                if self.total_load_uncertainty < 0: 
                    errors.append("Negative uncertainty")
            
            # Persist to Redis
            self._persist_state()
            
            logger.debug(f"State updated from NILM: Load={self.current_state.total_load}kW, "
                        f"Uncertainty={self.current_state.total_load_uncertainty:.2f}")
        
        except Exception as e:
            logger.error(f"Error updating from NILM: {e}")
    def update_from_forecast(self, forecast_data: dict):
        """
        Update state with forecast data from Person 2
        
        Expected format:
        {
            "timestamp": "2024-12-07T14:30:00Z",
            "forecast_type": "solar",  # or "wind"
            "horizon": "24h",
            "predictions": [
                {"time": "2024-12-07T15:00:00Z", "value": 3.2, "confidence": 0.88},
                ...
            ],
            "model": "transformer"
        }
        """
        try:
            timestamp = datetime.fromisoformat(forecast_data['timestamp'])
            forecast_type = forecast_data['forecast_type']
            if 'uncertainty' in forecast_data:
                if forecast_type == 'solar':
                    self.current_state.generation.solar_uncertainty = forecast_data['uncertainty']
                elif forecast_type == 'wind':
                    self.current_state.generation.wind_uncertainty = forecast_data['uncertainty']
            if forecast_type == 'load':  # If Person 2 adds load forecast
                self.current_state.forecast.load_forecast = forecast_data['predictions']
                predictions = forecast_data['predictions']
            
            # Initialize forecast state if not exists
            if self.current_state.forecast is None:
                self.current_state.forecast = ForecastState(timestamp=timestamp)
            
            # Update appropriate forecast
            if forecast_type == 'solar':
                self.current_state.forecast.solar_forecast = predictions
                
                # Use first prediction for current generation estimate
                if predictions:
                    self.current_state.generation.solar = predictions[0]['value']
                    self.current_state.generation.calculate_total()
            
            elif forecast_type == 'wind':
                self.current_state.forecast.wind_forecast = predictions
                
                if predictions:
                    self.current_state.generation.wind = predictions[0]['value']
                    self.current_state.generation.calculate_total()
            
            elif forecast_type == 'price':
                self.current_state.forecast.price_forecast = predictions
                
                if predictions:
                    self.current_state.grid.current_price = predictions[0]['value']
            
            # Recalculate
            self.current_state.calculate_power_balance()
            self.current_state.update_metrics()
            
            # Persist
            self._persist_state()
            
            logger.debug(f"State updated from forecast: Type={forecast_type}")
            
        except Exception as e:
            logger.error(f"Error updating from forecast: {e}")
        
        if forecast_type == 'solar' and 'uncertainty' in forecast_data:
            self.current_state.generation.solar_uncertainty = forecast_data['uncertainty']
        elif forecast_type == 'wind' and 'uncertainty' in forecast_data:
            self.current_state.generation.wind_uncertainty = forecast_data['uncertainty']
        # Add load forecast if present (paper's load variations)
        if forecast_type == 'load':
            self.current_state.forecast.load_forecast = forecast_data['predictions']
    
    def update_battery(self, soc: float, charging_power: float = 0.0, 
                      discharging_power: float = 0.0):
        """Manually update battery state"""
        self.current_state.battery.soc = soc
        self.current_state.battery.charging_power = charging_power
        self.current_state.battery.discharging_power = discharging_power
        
        self.current_state.calculate_power_balance()
        self.current_state.update_metrics()
        self._persist_state()
    
    def update_generation(self, solar: float = None, wind: float = None, 
                         fuel_cell: float = None):
        """Manually update generation"""
        if solar is not None:
            self.current_state.generation.solar = solar
        if wind is not None:
            self.current_state.generation.wind = wind
        if fuel_cell is not None:
            self.current_state.generation.fuel_cell = fuel_cell
        
        self.current_state.generation.calculate_total()
        self.current_state.calculate_power_balance()
        self.current_state.update_metrics()
        self._persist_state()
    
    def _is_appliance_shiftable(self, appliance_name: str) -> bool:
        """Determine if appliance load can be shifted in time"""
        # Shiftable appliances (can delay usage)
        shiftable = [
            'washing_machine', 'dishwasher', 'dryer',
            'ev_charger', 'water_heater', 'pool_pump'
        ]
        
        return any(s in appliance_name.lower() for s in shiftable)
    
    def _get_appliance_priority(self, appliance_name: str) -> int:
        """
        Get appliance priority (1=critical, 10=optional)
        Used for demand response decisions
        """
        priority_map = {
            'refrigerator': 1,
            'freezer': 1,
            'medical': 1,
            'security': 2,
            'lighting': 3,
            'hvac': 4,
            'air_conditioner': 4,
            'heater': 4,
            'computer': 5,
            'tv': 6,
            'entertainment': 6,
            'washing_machine': 7,
            'dishwasher': 7,
            'dryer': 8,
            'ev_charger': 8,
            'pool_pump': 9,
            'spa': 10
        }
        
        # Find matching priority
        for keyword, priority in priority_map.items():
            if keyword in appliance_name.lower():
                return priority
        
        return 5  # Default medium priority
    
    def _persist_state(self):
        """Save current state to Redis"""
        try:
            # Store as JSON
            state_json = self.current_state.to_json()
            self.redis_client.set('dt:current_state', state_json)
            
            # Also store with timestamp key for history
            timestamp_key = f"dt:history:{self.current_state.timestamp.isoformat()}"
            self.redis_client.setex(timestamp_key, 86400, state_json)  # 24h TTL
            
        except Exception as e:
            logger.error(f"Error persisting state: {e}")
    
    def get_current_state(self) -> MicrogridState:
        """Get current state (returns copy)"""
        return self.current_state
    
    def get_state_summary(self) -> dict:
        """Get simplified state summary"""
        if not self.current_state:
            return {}
        
        return {
            'timestamp': self.current_state.timestamp.isoformat(),
            'load': {
                'total': self.current_state.total_load,
                'appliances': {
                    name: app.power 
                    for name, app in self.current_state.appliances.items()
                }
            },
            'generation': {
                'solar': self.current_state.generation.solar,
                'wind': self.current_state.generation.wind,
                'total': self.current_state.generation.total
            },
            'battery': {
                'soc': self.current_state.battery.soc,
                'charging': self.current_state.battery.charging_power,
                'discharging': self.current_state.battery.discharging_power
            },
            'grid': {
                'import': self.current_state.grid.import_power,
                'export': self.current_state.grid.export_power,
                'price': self.current_state.grid.current_price
            },
            'metrics': {
                'self_consumption': f"{self.current_state.metrics.self_consumption_ratio:.1%}",
                'renewable_penetration': f"{self.current_state.metrics.renewable_penetration:.1%}",
                'grid_independence': f"{self.current_state.metrics.grid_independence:.1%}"
            },
            'health': {
                'is_healthy': self.current_state.is_healthy,
                'errors': self.current_state.error_messages
            }
        }

