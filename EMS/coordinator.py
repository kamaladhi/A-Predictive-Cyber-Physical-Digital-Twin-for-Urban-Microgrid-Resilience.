"""
Priority-Aware Coordinator - Enforces resilience policies
"""
from dataclasses import dataclass, field
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class CoordinationState:
    """City-level coordination state"""
    timestamp: str
    
    # City totals
    total_load_kw: float = 0.0
    total_critical_load_kw: float = 0.0
    total_available_kw: float = 0.0
    
    # Metrics
    total_load_served_kw: float = 0.0
    total_critical_served_kw: float = 0.0
    total_load_shed_kw: float = 0.0
    
    # Status
    city_status: str = "normal"  # normal, degraded, critical
    resilience_score: float = 1.0  # 0-1
    
    # Actions per microgrid
    microgrid_actions: Dict[str, Dict] = field(default_factory=dict)


class PriorityAwareCoordinator:
    """
    Coordinates multiple microgrids with priority enforcement
    
    Priority Order:
    1. Hospital (medical - life safety)
    2. University (education, research)
    3. Residence (residential safety)
    4. Industrial (manufacturing - most flexible)
    """
    
    def __init__(self):
        self.microgrids: Dict[str, Dict] = {}
        self.priority_order = {
            1: 'hospital',
            2: 'university',
            3: 'residence',
            4: 'industrial'
        }
    
    def register_microgrid(self, mg_id: str, mg_type: str, 
                          name: str, priority: int):
        """Register a microgrid with the coordinator"""
        self.microgrids[mg_id] = {
            'type': mg_type,
            'name': name,
            'priority': priority,
            'state': None
        }
        logger.info(f"✓ Coordinator: Registered {name} (Priority {priority})")
    
    def update_status(self, mg_id: str, state: Dict):
        """Update microgrid status"""
        if mg_id in self.microgrids:
            self.microgrids[mg_id]['state'] = state
    
    def coordinate(self, timestamp: str) -> CoordinationState:
        """
        Main coordination logic - Enforce priority-aware resilience
        
        Algorithm:
        1. Calculate city-level power balance
        2. Protect critical loads by priority
        3. Shed non-critical loads from lowest priority first
        """
        coordination = CoordinationState(timestamp=timestamp)
        
        # Step 1: Aggregate all microgrid data
        total_load = 0
        total_critical = 0
        total_available = 0
        
        microgrid_data = {}
        
        for mg_id, mg_info in self.microgrids.items():
            state = mg_info.get('state', {})
            
            load = state.get('total_load_kw', 0)
            critical = state.get('critical_load_kw', 0)
            pv = state.get('pv_power_kw', 0)
            gen = state.get('generator_power_kw', 0)
            batt = state.get('battery_power_kw', 0)
            
            total_load += load
            total_critical += critical
            total_available += max(0, pv) + max(0, gen) + max(0, batt)
            
            microgrid_data[mg_id] = {
                'load': load,
                'critical': critical,
                'available': total_available,
                'priority': mg_info['priority'],
                'name': mg_info['name']
            }
        
        coordination.total_load_kw = total_load
        coordination.total_critical_load_kw = total_critical
        coordination.total_available_kw = total_available
        
        # Step 2: Determine shedding requirement
        deficit = max(0, total_load - total_available)
        
        # Step 3: Apply priority-based shedding
        if deficit > 1e-6:
            # Need to shed - start from lowest priority
            shed_amount = deficit
            
            # Sort microgrids by priority (lowest first = industrial)
            sorted_mgs = sorted(
                microgrid_data.items(),
                key=lambda x: x[1]['priority'],
                reverse=True
            )
            
            microgrid_actions = {}
            
            for mg_id, mg_data in sorted_mgs:
                if shed_amount < 1e-6:
                    break
                
                # Can shed non-critical load
                non_critical = mg_data['load'] - mg_data['critical']
                
                if non_critical > 1e-6:
                    shed = min(shed_amount, non_critical)
                    microgrid_actions[mg_id] = {
                        'action': 'shed',
                        'shed_kw': shed,
                        'reason': 'Power deficit'
                    }
                    shed_amount -= shed
            
            coordination.microgrid_actions = microgrid_actions
            coordination.total_load_shed_kw = deficit - shed_amount
            coordination.total_load_served_kw = total_load - coordination.total_load_shed_kw
        else:
            # No shedding needed
            coordination.total_load_served_kw = total_load
        
        # Step 4: Calculate metrics
        coordination.total_critical_served_kw = total_critical
        
        # Resilience score: combination of critical protection + load served
        critical_ratio = (coordination.total_critical_served_kw / 
                         max(1, coordination.total_critical_load_kw))
        load_ratio = (coordination.total_load_served_kw / 
                     max(1, coordination.total_load_kw))
        
        coordination.resilience_score = 0.7 * critical_ratio + 0.3 * load_ratio
        
        # Determine status
        if coordination.resilience_score >= 0.95:
            coordination.city_status = "normal"
        elif coordination.resilience_score >= 0.70:
            coordination.city_status = "degraded"
        else:
            coordination.city_status = "critical"
        
        return coordination
