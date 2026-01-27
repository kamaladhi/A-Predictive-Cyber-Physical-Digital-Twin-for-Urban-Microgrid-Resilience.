"""
Load Forecasting Module for Digital Twin
Predicts microgrid load 6 hours ahead using historical data
"""
from dataclasses import dataclass
from typing import Dict, List
import numpy as np
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


@dataclass
class LoadForecast:
    """Load forecast for next 6 hours"""
    timestamp: datetime
    microgrid_id: str
    forecast_hours: List[float]  # kW for each hour (0-6h)
    forecast_critical: List[float]  # Critical load forecast
    confidence: float  # 0-1 confidence level
    method: str  # 'naive', 'ma', 'exponential_smoothing'


class LoadForecaster:
    """
    Predicts microgrid loads using time-series analysis
    
    Methods:
    1. Naive: Repeat last observed load
    2. Moving Average: Average of last N hours
    3. Exponential Smoothing: Weighted average with more recent = higher weight
    4. Time-of-Day Pattern: Matches similar hour from previous day
    """
    
    def __init__(self, microgrid_id: str, microgrid_type: str):
        """
        Initialize forecaster
        
        Args:
            microgrid_id: e.g., 'hospital_0'
            microgrid_type: e.g., 'hospital', 'university'
        """
        self.microgrid_id = microgrid_id
        self.microgrid_type = microgrid_type
        
        # Historical load data
        self.load_history: List[float] = []
        self.critical_history: List[float] = []
        self.timestamps: List[datetime] = []
        
        # Typical hourly patterns for each microgrid type
        self.daily_patterns = {
            'hospital': {
                0: 0.85, 1: 0.80, 2: 0.78, 3: 0.75, 4: 0.76, 5: 0.80,   # Night
                6: 0.85, 7: 0.90, 8: 0.95, 9: 1.00, 10: 0.98, 11: 0.96,  # Morning
                12: 0.94, 13: 0.92, 14: 0.94, 15: 0.96, 16: 0.98, 17: 1.00,  # Afternoon
                18: 0.99, 19: 0.97, 20: 0.94, 21: 0.90, 22: 0.87, 23: 0.86   # Evening
            },
            'university': {
                0: 0.50, 1: 0.48, 2: 0.46, 3: 0.44, 4: 0.45, 5: 0.50,
                6: 0.55, 7: 0.65, 8: 0.90, 9: 1.00, 10: 1.00, 11: 0.95,
                12: 0.85, 13: 0.95, 14: 1.00, 15: 1.00, 16: 0.95, 17: 0.90,
                18: 0.80, 19: 0.70, 20: 0.60, 21: 0.55, 22: 0.52, 23: 0.50
            },
            'residence': {
                0: 0.60, 1: 0.55, 2: 0.52, 3: 0.50, 4: 0.51, 5: 0.55,
                6: 0.70, 7: 0.75, 8: 0.65, 9: 0.50, 10: 0.45, 11: 0.48,
                12: 0.60, 13: 0.55, 14: 0.50, 15: 0.52, 16: 0.60, 17: 0.75,
                18: 0.95, 19: 1.00, 20: 0.98, 21: 0.90, 22: 0.75, 23: 0.65
            },
            'industrial': {
                0: 0.30, 1: 0.30, 2: 0.30, 3: 0.30, 4: 0.30, 5: 0.30,
                6: 0.50, 7: 0.80, 8: 1.00, 9: 1.00, 10: 0.98, 11: 0.96,
                12: 0.80, 13: 0.95, 14: 1.00, 15: 0.98, 16: 0.95, 17: 0.80,
                18: 0.50, 19: 0.35, 20: 0.32, 21: 0.31, 22: 0.30, 23: 0.30
            }
        }
    
    def add_observation(self, timestamp: datetime, load_kw: float, 
                       critical_load_kw: float):
        """Add a real observation to history"""
        self.timestamps.append(timestamp)
        self.load_history.append(load_kw)
        self.critical_history.append(critical_load_kw)
        
        # Keep only last 168 hours (1 week) for memory efficiency
        if len(self.load_history) > 168:
            self.timestamps.pop(0)
            self.load_history.pop(0)
            self.critical_history.pop(0)
    
    def forecast_naive(self, horizon_hours: int = 6) -> List[float]:
        """
        Naive forecast: repeat last observed load
        
        Simple baseline, works OK for stable loads
        """
        if not self.load_history:
            return [0.0] * horizon_hours
        
        last_load = self.load_history[-1]
        return [last_load] * horizon_hours
    
    def forecast_moving_average(self, window: int = 4, 
                               horizon_hours: int = 6) -> List[float]:
        """
        Moving average forecast
        
        Args:
            window: Number of hours to average (default 4)
            horizon_hours: Hours to forecast
        
        Better than naive, captures recent trend
        """
        if len(self.load_history) < window:
            return self.forecast_naive(horizon_hours)
        
        # Average last 'window' hours
        avg_load = np.mean(self.load_history[-window:])
        return [avg_load] * horizon_hours
    
    def forecast_exponential_smoothing(self, alpha: float = 0.3,
                                       horizon_hours: int = 6) -> List[float]:
        """
        Exponential smoothing forecast
        
        Args:
            alpha: Smoothing factor (0-1), higher = more recent data weighted
            horizon_hours: Hours to forecast
        
        More recent observations weighted higher
        """
        if not self.load_history:
            return [0.0] * horizon_hours
        
        # Start with last observed load
        smoothed = self.load_history[-1]
        
        # If we have history, calculate exponential smoothed value
        if len(self.load_history) > 1:
            for i in range(len(self.load_history) - 2, -1, -1):
                smoothed = alpha * self.load_history[i] + (1 - alpha) * smoothed
        
        # Forecast as constant from smoothed value
        return [smoothed] * horizon_hours
    
    def forecast_time_of_day(self, current_timestamp: datetime,
                            peak_load_kw: float,
                            horizon_hours: int = 6) -> List[float]:
        """
        Time-of-day pattern forecast
        
        Uses typical daily pattern for microgrid type to forecast loads
        
        Args:
            current_timestamp: Current time
            peak_load_kw: Nominal peak load for this microgrid
            horizon_hours: Hours to forecast
        
        Best method - uses domain knowledge of microgrid type
        """
        pattern = self.daily_patterns.get(self.microgrid_type, 
                                          self.daily_patterns['industrial'])
        
        forecast = []
        current_hour = current_timestamp.hour
        
        for h in range(horizon_hours):
            # What hour will this be?
            future_hour = (current_hour + h) % 24
            
            # Get pattern factor (0-1) for this hour
            pattern_factor = pattern.get(future_hour, 0.5)
            
            # Scale by peak load
            forecasted_load = pattern_factor * peak_load_kw
            forecast.append(forecasted_load)
        
        return forecast
    
    def forecast(self, current_timestamp: datetime, 
                peak_load_kw: float,
                horizon_hours: int = 6,
                method: str = 'time_of_day') -> LoadForecast:
        """
        Generate load forecast for next 6 hours
        
        Args:
            current_timestamp: Current time
            peak_load_kw: Peak load capacity
            horizon_hours: Hours to forecast (default 6)
            method: 'naive', 'ma', 'exponential', 'time_of_day' (best)
        
        Returns:
            LoadForecast object with prediction
        """
        # Choose forecasting method
        if method == 'naive':
            forecast_loads = self.forecast_naive(horizon_hours)
            confidence = 0.5
        elif method == 'ma':
            forecast_loads = self.forecast_moving_average(window=4, 
                                                         horizon_hours=horizon_hours)
            confidence = 0.65
        elif method == 'exponential':
            forecast_loads = self.forecast_exponential_smoothing(alpha=0.3,
                                                                 horizon_hours=horizon_hours)
            confidence = 0.70
        elif method == 'time_of_day':
            forecast_loads = self.forecast_time_of_day(current_timestamp, 
                                                       peak_load_kw,
                                                       horizon_hours)
            confidence = 0.80
        else:
            forecast_loads = self.forecast_naive(horizon_hours)
            confidence = 0.5
            method = 'naive'
        
        # Critical load is typically 20-30% of total load, more stable
        critical_pattern = [0.25] * horizon_hours  # 25% of load
        forecast_critical = [fl * 0.25 for fl in forecast_loads]
        
        return LoadForecast(
            timestamp=current_timestamp,
            microgrid_id=self.microgrid_id,
            forecast_hours=forecast_loads,
            forecast_critical=forecast_critical,
            confidence=confidence,
            method=method
        )
    
    def get_summary(self) -> Dict:
        """Get forecaster summary stats"""
        if not self.load_history:
            return {
                'observations': 0,
                'avg_load': 0,
                'min_load': 0,
                'max_load': 0
            }
        
        return {
            'observations': len(self.load_history),
            'avg_load': float(np.mean(self.load_history)),
            'min_load': float(np.min(self.load_history)),
            'max_load': float(np.max(self.load_history)),
            'type': self.microgrid_type
        }
