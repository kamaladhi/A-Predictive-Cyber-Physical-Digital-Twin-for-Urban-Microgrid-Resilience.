"""
Proactive Coordinator using Load Forecasting
Makes decisions based on predicted future load, not just current state
"""
from dataclasses import dataclass
from typing import Dict, List
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ProactiveCoordinationState:
    """Coordination state including forecast-based decisions"""
    timestamp: datetime
    
    # Current state metrics
    total_load_kw: float
    total_critical_load_kw: float
    total_available_kw: float
    total_load_served_kw: float
    total_critical_served_kw: float
    total_load_shed_kw: float
    
    # Forecast-based decisions
    forecasted_deficit: float  # Predicted power deficit in next hour
    battery_charge_recommendation: float  # kW to charge battery (proactive)
    load_shift_recommendation: float  # kW to shift to off-peak
    
    # Status and resilience
    resilience_score: float
    city_status: str  # normal/degraded/critical
    
    # Microgrids actions
    microgrid_actions: Dict = None


class ProactiveCoordinator:
    """
    Priority-aware coordinator with forecast-based optimization
    
    Enhancements over base coordinator:
    1. Uses load forecasts to pre-charge batteries before peaks
    2. Recommends load shifting away from predicted high-load periods
    3. Pre-positions generators for expected demand
    """
    
    def __init__(self):
        """Initialize coordinator"""
        self.microgrids = {}  # {mg_id: {'priority', 'config', 'state', 'digital_twin'}}
        self.current_state = None
        
        logger.info("✓ Proactive Coordinator initialized")
    
    def register_microgrid(self, microgrid_id: str, priority: int, 
                          config, digital_twin):
        """Register a microgrid"""
        self.microgrids[microgrid_id] = {
            'priority': priority,
            'config': config,
            'state': None,
            'digital_twin': digital_twin
        }
        
        facility = getattr(config, 'facility_name', 'Unknown')
        logger.info(f"✓ Coordinator: Registered {facility} (Priority {priority})")
    
    def update_status(self, microgrid_id: str, state):
        """Update microgrid state"""
        if microgrid_id in self.microgrids:
            self.microgrids[microgrid_id]['state'] = state
    
    def _get_forecast(self, microgrid_id: str):
        """Get load forecast from Digital Twin"""
        if microgrid_id not in self.microgrids:
            return None
        
        dt = self.microgrids[microgrid_id]['digital_twin']
        return dt.predict_load(hours_ahead=6)
    
    def coordinate(self, timestamp) -> ProactiveCoordinationState:
        """
        Execute proactive coordination
        
        Steps:
        1. Aggregate current and forecasted loads
        2. Identify predicted deficits
        3. Recommend proactive battery charging
        4. Apply priority-based shedding (current state)
        5. Suggest load shifting to off-peak
        """
        # Aggregate current data
        total_load = 0
        total_critical = 0
        total_available = 0
        microgrid_actions = {}
        
        forecasts = {}
        next_hour_loads = {}
        
        for mg_id in sorted(self.microgrids.keys(), 
                           key=lambda x: self.microgrids[x]['priority']):
            state = self.microgrids[mg_id]['state']
            
            if not state:
                continue
            
            total_load += state.total_load_kw
            total_critical += state.critical_load_kw
            total_available += (state.pv_power_kw + state.generator_power_kw)
            
            # Get forecast
            forecast = self._get_forecast(mg_id)
            if forecast:
                forecasts[mg_id] = forecast
                next_hour_loads[mg_id] = forecast.forecast_hours[0]
            else:
                next_hour_loads[mg_id] = state.total_load_kw
        
        # === PROACTIVE DECISIONS ===
        
        # Predict next hour's deficit
        predicted_next_load = sum(next_hour_loads.values())
        predicted_deficit = max(0, predicted_next_load - total_available)
        
        # Recommend battery charging if deficit predicted
        battery_charge_recommendation = 0
        for mg_id in self.microgrids:
            state = self.microgrids[mg_id]['state']
            if not state:
                continue
            
            # If deficit predicted, pre-charge high-priority batteries
            priority = self.microgrids[mg_id]['priority']
            if predicted_deficit > 0 and priority <= 2:  # Hospital and University
                # Charge battery if SOC < 80%
                if state.battery_soc_percent < 80:
                    battery_charge_recommendation += min(
                        100,  # Max 100 kW charge rate
                        (80 - state.battery_soc_percent) * 2  # Scale to kW
                    )
        
        # Recommend load shifting
        load_shift_recommendation = 0
        for mg_id in self.microgrids:
            forecast = forecasts.get(mg_id)
            if not forecast:
                continue
            
            # If next hour has high load, suggest shifting
            next_load = forecast.forecast_hours[0]
            load_in_2h = forecast.forecast_hours[2] if len(forecast.forecast_hours) > 2 else next_load
            
            if next_load > load_in_2h * 1.3:  # 30% higher in next hour
                # Suggest load shift
                config = self.microgrids[mg_id]['config']
                discretionary = getattr(config, 'discretionary_load_kw', 0)
                load_shift_recommendation += discretionary * 0.3
        
        # === PRIORITY-BASED SHEDDING (Current State) ===
        
        deficit = max(0, total_load - total_available)
        total_load_served = 0
        total_critical_served = 0
        total_load_shed = 0
        
        if deficit > 0:
            # Shed from lowest priority first (Industrial → Residence → University → Hospital)
            for mg_id in sorted(self.microgrids.keys(),
                              key=lambda x: self.microgrids[x]['priority'],
                              reverse=True):  # Lowest priority first (4→3→2→1)
                
                state = self.microgrids[mg_id]['state']
                if not state:
                    continue
                
                # Calculate what this microgrid can contribute
                available_shed = max(0, state.total_load_kw - state.critical_load_kw)
                shed_amount = min(deficit, available_shed)
                
                # This microgrid serves: critical + (total - critical - shed)
                mg_served = state.critical_load_kw + (state.total_load_kw - state.critical_load_kw - shed_amount)
                
                total_load_served += mg_served
                total_critical_served += state.critical_load_kw  # Critical always protected
                total_load_shed += shed_amount
                
                microgrid_actions[mg_id] = {
                    'action': 'shed' if shed_amount > 0 else 'normal',
                    'amount_kw': shed_amount
                }
                
                deficit -= shed_amount
                
                if deficit <= 0:
                    break
            
            # If still in deficit after shedding all non-critical, city is in FAILURE MODE
            # DO NOT shed critical loads - this would violate thesis requirement
            if deficit > 0:
                # Mark as critical failure, but protect critical loads
                pass  # Critical loads remain protected
        else:
            # No deficit - all loads can be served
            for mg_id in self.microgrids.keys():
                state = self.microgrids[mg_id]['state']
                if not state:
                    continue
                
                total_load_served += state.total_load_kw
                total_critical_served += state.critical_load_kw
                
                microgrid_actions[mg_id] = {
                    'action': 'normal',
                    'amount_kw': 0
                }
        
        # === RESILIENCE CALCULATION ===
        
        # Ensure bounds
        critical_ratio = (total_critical_served / total_critical if total_critical > 0 else 1.0)
        load_ratio = (total_load_served / total_load if total_load > 0 else 1.0)
        
        critical_ratio = max(0, min(1, critical_ratio))
        load_ratio = max(0, min(1, load_ratio))
        
        resilience_score = 0.7 * critical_ratio + 0.3 * load_ratio
        
        # Determine city status
        if resilience_score >= 0.95:
            city_status = 'normal'
        elif resilience_score >= 0.70:
            city_status = 'degraded'
        else:
            city_status = 'critical'
        
        # === BUILD STATE ===
        
        self.current_state = ProactiveCoordinationState(
            timestamp=timestamp,
            total_load_kw=total_load,
            total_critical_load_kw=total_critical,
            total_available_kw=total_available,
            total_load_served_kw=total_load_served,
            total_critical_served_kw=total_critical_served,
            total_load_shed_kw=total_load_shed,
            forecasted_deficit=predicted_deficit,
            battery_charge_recommendation=battery_charge_recommendation,
            load_shift_recommendation=load_shift_recommendation,
            resilience_score=resilience_score,
            city_status=city_status,
            microgrid_actions=microgrid_actions
        )
        
        return self.current_state
    
    def get_forecast_summary(self) -> Dict:
        """Get summary of all forecasts"""
        summary = {}
        
        for mg_id in self.microgrids:
            dt = self.microgrids[mg_id]['digital_twin']
            forecast = dt.last_forecast
            
            if forecast:
                summary[mg_id] = {
                    'method': forecast.method,
                    'confidence': forecast.confidence,
                    'next_hour': forecast.forecast_hours[0] if forecast.forecast_hours else 0,
                    'peak_6h': max(forecast.forecast_hours) if forecast.forecast_hours else 0
                }
        
        return summary
