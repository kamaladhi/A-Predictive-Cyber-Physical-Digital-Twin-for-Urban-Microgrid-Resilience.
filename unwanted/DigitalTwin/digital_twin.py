"""
Digital Twin - Virtual model of a microgrid
Mirrors real microgrid state in real-time
"""
from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime
import logging
from DigitalTwin.twin_forecasting import LoadForecaster, LoadForecast

logger = logging.getLogger(__name__)


@dataclass
class MicrogridState:
    """State of a single microgrid at a point in time"""
    timestamp: datetime
    microgrid_id: str
    
    # Power flows (kW)
    total_load_kw: float
    critical_load_kw: float
    pv_power_kw: float
    generator_power_kw: float
    battery_power_kw: float  # positive = discharging, negative = charging
    grid_power_kw: float
    
    # Storage state
    battery_soc_percent: float
    
    # Status
    is_islanded: bool
    grid_available: bool
    
    # Derived metrics
    power_balance_kw: float = 0.0
    frequency_hz: float = 50.0
    voltage_pu: float = 1.0


class DigitalTwin:
    """
    Digital Twin for a single microgrid
    
    Responsibilities:
    1. Mirror the real microgrid state
    2. Run physics models for prediction
    3. Suggest optimal actions
    """
    
    def __init__(self, microgrid_id: str, microgrid_type: str, 
                 config, simulator):
        """
        Initialize Digital Twin
        
        Args:
            microgrid_id: Unique ID (e.g., 'hospital_0')
            microgrid_type: Type ('hospital', 'university', etc.)
            config: Configuration object
            simulator: MicrogridSimulator instance
        """
        self.microgrid_id = microgrid_id
        self.microgrid_type = microgrid_type
        self.config = config
        self.simulator = simulator
        
        # Current state
        self.current_state: Optional[MicrogridState] = None
        self.history = []
        
        # Load forecaster
        self.forecaster = LoadForecaster(microgrid_id, microgrid_type)
        self.last_forecast: Optional[LoadForecast] = None
        
        logger.info(f"✓ Digital Twin created for {microgrid_id}")
    
    def update(self, timestamp: datetime, simulator_state: Dict) -> MicrogridState:
        """
        Update Digital Twin state from simulator
        
        This mirrors the real microgrid state
        """
        state = MicrogridState(
            timestamp=timestamp,
            microgrid_id=self.microgrid_id,
            total_load_kw=simulator_state.get('total_load_kw', 0),
            critical_load_kw=simulator_state.get('critical_load_kw', 0),
            pv_power_kw=simulator_state.get('pv_power_kw', 0),
            generator_power_kw=simulator_state.get('generator_power_kw', 0),
            battery_power_kw=simulator_state.get('battery_power_kw', 0),
            grid_power_kw=simulator_state.get('grid_power_kw', 0),
            battery_soc_percent=simulator_state.get('battery_soc_percent', 50),
            is_islanded=simulator_state.get('is_islanded', False),
            grid_available=simulator_state.get('grid_available', True),
        )
        
        # Calculate power balance
        state.power_balance_kw = (
            state.pv_power_kw + 
            state.generator_power_kw + 
            state.battery_power_kw + 
            state.grid_power_kw - 
            state.total_load_kw
        )
        
        self.current_state = state
        self.history.append(state)
        
        # Add to forecaster history
        self.forecaster.add_observation(
            timestamp, 
            state.total_load_kw,
            state.critical_load_kw
        )
        
        return state
    
    def predict_load(self, hours_ahead: int = 6) -> LoadForecast:
        """
        Predict future load using time-series forecasting
        
        Uses time-of-day patterns for microgrid type + historical data
        
        Returns:
            LoadForecast with 6-hour ahead predictions
        """
        if not self.current_state:
            # Not initialized yet
            return None
        
        # Get peak load from config
        peak_load = getattr(self.config, 'peak_load_kw', 1000)
        
        # Forecast using time-of-day patterns (most accurate)
        forecast = self.forecaster.forecast(
            current_timestamp=self.current_state.timestamp,
            peak_load_kw=peak_load,
            horizon_hours=hours_ahead,
            method='time_of_day'
        )
        
        self.last_forecast = forecast
        return forecast
    
    def recommend_action(self) -> Dict:
        """Recommend optimal action (battery charge/discharge, shedding)"""
        if not self.current_state:
            return {}
        
        state = self.current_state
        deficit = max(0, state.total_load_kw - 
                     (state.pv_power_kw + state.generator_power_kw))
        
        return {
            'action': 'shed' if deficit > 100 else 'normal',
            'shed_amount_kw': deficit,
            'reason': 'Power deficit detected' if deficit > 0 else 'Normal operation'
        }
