"""
City-Level Metrics Tracker - Survivability and resilience metrics
"""
from dataclasses import dataclass
from typing import List, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class CityMetrics:
    """Snapshot of city-level metrics"""
    timestamp: datetime
    
    # Load metrics
    total_demand_kw: float
    total_load_served_kw: float
    total_load_shed_kw: float
    load_satisfaction_percent: float
    
    # Critical load metrics
    total_critical_demand_kw: float
    total_critical_served_kw: float
    critical_satisfaction_percent: float
    
    # System status
    city_status: str  # normal, degraded, critical
    resilience_score: float  # 0-1
    
    # Microgrid status
    num_microgrids: int
    num_islanded: int
    num_in_critical: int


class CityMetricsTracker:
    """Tracks city-level survivability metrics"""
    
    def __init__(self):
        self.history: List[CityMetrics] = []
        self.num_microgrids = 4
    
    def record(self, timestamp: datetime, coordination_state, 
               microgrid_states: Dict) -> CityMetrics:
        """Record a snapshot of city metrics"""
        
        # Count islanded microgrids (states can be either dict or MicrogridState)
        num_islanded = 0
        for s in microgrid_states.values():
            if hasattr(s, 'is_islanded'):  # MicrogridState object
                if s.is_islanded:
                    num_islanded += 1
            elif isinstance(s, dict):  # dict
                if s.get('is_islanded', False):
                    num_islanded += 1
        
        # Calculate metrics
        total_critical = coordination_state.total_critical_load_kw
        critical_served = coordination_state.total_critical_served_kw
        
        if total_critical > 0:
            critical_sat = (critical_served / total_critical) * 100
        else:
            critical_sat = 100
        
        total_demand = coordination_state.total_load_kw
        if total_demand > 0:
            load_sat = (coordination_state.total_load_served_kw / total_demand) * 100
        else:
            load_sat = 100
        
        # Create metrics snapshot
        metrics = CityMetrics(
            timestamp=timestamp,
            total_demand_kw=total_demand,
            total_load_served_kw=coordination_state.total_load_served_kw,
            total_load_shed_kw=coordination_state.total_load_shed_kw,
            load_satisfaction_percent=load_sat,
            total_critical_demand_kw=total_critical,
            total_critical_served_kw=critical_served,
            critical_satisfaction_percent=critical_sat,
            city_status=coordination_state.city_status,
            resilience_score=coordination_state.resilience_score,
            num_microgrids=self.num_microgrids,
            num_islanded=num_islanded,
            num_in_critical=sum(1 for s in microgrid_states.values()
                               if (s.power_balance_kw < -100 if hasattr(s, 'power_balance_kw') 
                                   else s.get('power_balance_kw', 0) < -100))
        )
        
        self.history.append(metrics)
        return metrics
    
    def get_summary(self) -> Dict:
        """Get summary statistics"""
        if not self.history:
            return {}
        
        resilience_scores = [m.resilience_score for m in self.history]
        load_sat = [m.load_satisfaction_percent for m in self.history]
        critical_sat = [m.critical_satisfaction_percent for m in self.history]
        
        return {
            'num_samples': len(self.history),
            'avg_resilience': sum(resilience_scores) / len(resilience_scores),
            'min_resilience': min(resilience_scores),
            'avg_load_satisfaction': sum(load_sat) / len(load_sat),
            'avg_critical_satisfaction': sum(critical_sat) / len(critical_sat),
            'critical_satisfaction_min': min(critical_sat),
        }
