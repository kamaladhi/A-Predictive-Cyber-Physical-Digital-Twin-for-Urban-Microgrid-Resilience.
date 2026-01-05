"""
Data Fusion Module for Digital Twin
Combines NILM data + Forecasts + Real-time telemetry into unified state
"""
import logging
from typing import Dict, Optional, List
from datetime import datetime, timedelta
from dataclasses import dataclass
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FusedMicrogridState:
    """
    Fused state combining multiple data sources
    
    Sources:
    1. Real-time telemetry (MQTT)
    2. NILM appliance breakdown (Person 1)
    3. Load forecasts (Person 2)
    4. Historical trends (Digital Twin)
    """
    timestamp: datetime
    microgrid_id: str
    
    # Real-time measurements (highest priority)
    measured_load_kw: float
    measured_pv_kw: float
    measured_battery_kw: float
    measured_soc_percent: float
    
    # NILM decomposition (Person 1's contribution)
    appliance_breakdown: Dict[str, float]  # {appliance_name: power_kw}
    critical_load_kw: float  # Identified by NILM
    non_critical_load_kw: float
    nilm_confidence: float  # 0-1
    
    # Forecasts (Person 2's contribution)
    forecasted_load_1h: float
    forecasted_load_6h: List[float]  # 6-hour horizon
    forecast_confidence: float  # 0-1
    
    # Derived metrics
    power_balance_kw: float
    estimated_runtime_hours: float  # Battery runtime at current load
    peak_predicted: bool  # Peak load predicted in next hour
    
    # Data quality
    overall_confidence: float  # Combined confidence score
    data_sources_active: List[str]  # Which sources are contributing


class DataFusionEngine:
    """
    Fuses data from multiple sources into coherent Digital Twin state
    
    Priority hierarchy:
    1. Real-time telemetry (ground truth)
    2. NILM decomposition (appliance-level insight)
    3. Forecasts (future prediction)
    4. Historical models (fallback)
    """
    
    def __init__(self):
        """Initialize data fusion engine"""
        # Historical data for fallback
        self.historical_loads: Dict[str, List[float]] = {}
        self.historical_timestamps: Dict[str, List[datetime]] = {}
        
        # NILM cache (Person 1's data)
        self.nilm_cache: Dict[str, Dict] = {}
        self.nilm_last_update: Dict[str, datetime] = {}
        
        # Forecast cache (Person 2's data)
        self.forecast_cache: Dict[str, Dict] = {}
        self.forecast_last_update: Dict[str, datetime] = {}
        
        # Fusion statistics
        self.fusion_count = 0
        self.nilm_miss_count = 0
        self.forecast_miss_count = 0
        
        logger.info("✓ Data Fusion Engine initialized")
    
    def update_nilm_data(self, microgrid_id: str, appliances: Dict[str, float], 
                         confidence: float):
        """
        Update NILM appliance breakdown (from Person 1)
        
        Args:
            microgrid_id: Microgrid identifier
            appliances: {appliance_name: power_kw}
            confidence: NILM algorithm confidence (0-1)
        """
        self.nilm_cache[microgrid_id] = {
            'appliances': appliances,
            'confidence': confidence,
            'critical_load': self._identify_critical_loads(appliances),
            'non_critical_load': self._identify_non_critical_loads(appliances)
        }
        self.nilm_last_update[microgrid_id] = datetime.now()
        
        logger.debug(f"📊 NILM data updated for {microgrid_id}: {len(appliances)} appliances")
    
    def update_forecast_data(self, microgrid_id: str, forecast_1h: float,
                            forecast_6h: List[float], confidence: float):
        """
        Update load forecasts (from Person 2)
        
        Args:
            microgrid_id: Microgrid identifier
            forecast_1h: 1-hour ahead load forecast (kW)
            forecast_6h: 6-hour ahead forecasts (kW)
            confidence: Forecast confidence (0-1)
        """
        self.forecast_cache[microgrid_id] = {
            'forecast_1h': forecast_1h,
            'forecast_6h': forecast_6h,
            'confidence': confidence,
            'peak_predicted': self._is_peak_predicted(forecast_6h)
        }
        self.forecast_last_update[microgrid_id] = datetime.now()
        
        logger.debug(f"📈 Forecast updated for {microgrid_id}: Next hour = {forecast_1h:.1f} kW")
    
    def fuse_state(self, 
                   microgrid_id: str,
                   telemetry: Dict,
                   historical_load: Optional[float] = None) -> FusedMicrogridState:
        """
        Fuse all available data sources into unified state
        
        Args:
            microgrid_id: Microgrid identifier
            telemetry: Real-time measurements (from MQTT)
            historical_load: Optional historical load for fallback
        
        Returns:
            FusedMicrogridState with combined data
        """
        self.fusion_count += 1
        timestamp = datetime.now()
        
        # Extract telemetry (highest priority - ground truth)
        measured_load = telemetry.get('total_load_kw', historical_load or 0)
        measured_pv = telemetry.get('pv_power_kw', 0)
        measured_battery = telemetry.get('battery_power_kw', 0)
        measured_soc = telemetry.get('battery_soc_percent', 50)
        
        # Get NILM data if available
        nilm_data = self.nilm_cache.get(microgrid_id, {})
        appliance_breakdown = nilm_data.get('appliances', {})
        critical_load = nilm_data.get('critical_load', measured_load * 0.3)
        non_critical_load = nilm_data.get('non_critical_load', measured_load * 0.7)
        nilm_confidence = nilm_data.get('confidence', 0.5)
        
        if not nilm_data:
            self.nilm_miss_count += 1
        
        # Get forecast data if available
        forecast_data = self.forecast_cache.get(microgrid_id, {})
        forecast_1h = forecast_data.get('forecast_1h', measured_load)
        forecast_6h = forecast_data.get('forecast_6h', [measured_load] * 6)
        forecast_confidence = forecast_data.get('confidence', 0.5)
        peak_predicted = forecast_data.get('peak_predicted', False)
        
        if not forecast_data:
            self.forecast_miss_count += 1
        
        # Calculate derived metrics
        power_balance = measured_pv + measured_battery - measured_load
        
        # Estimate battery runtime (hours until empty at current load)
        battery_capacity_kwh = telemetry.get('battery_capacity_kwh', 500)
        current_energy_kwh = (measured_soc / 100) * battery_capacity_kwh
        net_discharge_kw = max(0, measured_load - measured_pv)
        
        if net_discharge_kw > 0:
            estimated_runtime = current_energy_kwh / net_discharge_kw
        else:
            estimated_runtime = float('inf')
        
        # Data quality assessment
        data_sources_active = ['telemetry']
        if nilm_data:
            data_sources_active.append('nilm')
        if forecast_data:
            data_sources_active.append('forecast')
        
        # Overall confidence (weighted average)
        overall_confidence = (
            1.0 * 0.5 +  # Telemetry always trusted (50%)
            nilm_confidence * 0.25 +  # NILM (25%)
            forecast_confidence * 0.25  # Forecast (25%)
        )
        
        # Create fused state
        fused_state = FusedMicrogridState(
            timestamp=timestamp,
            microgrid_id=microgrid_id,
            measured_load_kw=measured_load,
            measured_pv_kw=measured_pv,
            measured_battery_kw=measured_battery,
            measured_soc_percent=measured_soc,
            appliance_breakdown=appliance_breakdown,
            critical_load_kw=critical_load,
            non_critical_load_kw=non_critical_load,
            nilm_confidence=nilm_confidence,
            forecasted_load_1h=forecast_1h,
            forecasted_load_6h=forecast_6h,
            forecast_confidence=forecast_confidence,
            power_balance_kw=power_balance,
            estimated_runtime_hours=estimated_runtime,
            peak_predicted=peak_predicted,
            overall_confidence=overall_confidence,
            data_sources_active=data_sources_active
        )
        
        # Update historical data
        self._update_historical(microgrid_id, measured_load, timestamp)
        
        return fused_state
    
    def _identify_critical_loads(self, appliances: Dict[str, float]) -> float:
        """
        Identify critical loads from NILM appliance breakdown
        
        Critical appliances (hospital example):
        - ICU equipment
        - Operating room
        - Emergency lighting
        - Elevators (emergency)
        - Medical refrigeration
        """
        critical_keywords = [
            'icu', 'critical', 'emergency', 'operating_room', 'or_',
            'medical', 'life_support', 'ventilator', 'dialysis',
            'elevator_emergency', 'emergency_lighting', 'refrigeration_medical'
        ]
        
        critical_total = 0
        for appliance_name, power_kw in appliances.items():
            appliance_lower = appliance_name.lower()
            if any(keyword in appliance_lower for keyword in critical_keywords):
                critical_total += power_kw
        
        return critical_total
    
    def _identify_non_critical_loads(self, appliances: Dict[str, float]) -> float:
        """Identify non-critical loads"""
        non_critical_keywords = [
            'hvac', 'ac', 'air_conditioning', 'heating',
            'admin', 'office', 'lighting_non_emergency',
            'cafeteria', 'laundry', 'parking'
        ]
        
        non_critical_total = 0
        for appliance_name, power_kw in appliances.items():
            appliance_lower = appliance_name.lower()
            if any(keyword in appliance_lower for keyword in non_critical_keywords):
                non_critical_total += power_kw
        
        return non_critical_total
    
    def _is_peak_predicted(self, forecast_6h: List[float]) -> bool:
        """
        Determine if peak load is predicted in next 6 hours
        
        Peak defined as: load > 1.3x current average
        """
        if not forecast_6h or len(forecast_6h) == 0:
            return False
        
        current_avg = np.mean(forecast_6h[:2])  # First 2 hours
        peak_threshold = current_avg * 1.3
        
        return any(load > peak_threshold for load in forecast_6h)
    
    def _update_historical(self, microgrid_id: str, load: float, timestamp: datetime):
        """Update historical data for trend analysis"""
        if microgrid_id not in self.historical_loads:
            self.historical_loads[microgrid_id] = []
            self.historical_timestamps[microgrid_id] = []
        
        self.historical_loads[microgrid_id].append(load)
        self.historical_timestamps[microgrid_id].append(timestamp)
        
        # Keep only last 7 days
        max_history = 7 * 24 * 12  # 7 days at 5-min intervals
        if len(self.historical_loads[microgrid_id]) > max_history:
            self.historical_loads[microgrid_id].pop(0)
            self.historical_timestamps[microgrid_id].pop(0)
    
    def get_historical_average(self, microgrid_id: str, 
                              hours_back: int = 24) -> float:
        """Get historical average load for comparison"""
        if microgrid_id not in self.historical_loads:
            return 0
        
        cutoff_time = datetime.now() - timedelta(hours=hours_back)
        recent_loads = [
            load for load, ts in zip(
                self.historical_loads[microgrid_id],
                self.historical_timestamps[microgrid_id]
            ) if ts >= cutoff_time
        ]
        
        return np.mean(recent_loads) if recent_loads else 0
    
    def get_fusion_stats(self) -> Dict:
        """Get data fusion statistics"""
        return {
            'fusion_count': self.fusion_count,
            'nilm_hit_rate': 1 - (self.nilm_miss_count / max(1, self.fusion_count)),
            'forecast_hit_rate': 1 - (self.forecast_miss_count / max(1, self.fusion_count)),
            'microgrids_tracked': len(self.historical_loads),
            'nilm_sources_active': len(self.nilm_cache),
            'forecast_sources_active': len(self.forecast_cache)
        }
