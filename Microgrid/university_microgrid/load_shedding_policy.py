"""
Load Shedding Policy Engine
Defines operational rules for curtailment and priority-based shedding
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ShedPriority(Enum):
    """Shedding priority levels"""
    NEVER = 0          # Critical loads
    ESSENTIAL = 1      # Must keep running (labs, HVAC essential)
    IMPORTANT = 2      # Can reduce but not cut (office, lighting)
    FLEXIBLE = 3       # Can shed without major impact (non-essential HVAC, labs)


@dataclass
class LoadGroup:
    """Represents a controllable load group"""
    name: str
    current_power_kw: float
    max_power_kw: float
    min_power_kw: float = 0.0
    priority: ShedPriority = ShedPriority.IMPORTANT
    max_shed_percent: float = 50.0  # Max % that can be shed
    min_runtime_minutes: float = 0.0  # Minimum continuous runtime
    
    def get_available_shed_kw(self) -> float:
        """Get maximum kW that can be shed from this group"""
        return self.max_power_kw * (self.max_shed_percent / 100)


class LoadShedPolicy:
    """
    Campus operational policy for load shedding
    
    Rules:
    1. Never shed critical loads (data center, medical, labs)
    2. Shed non-critical in priority order
    3. Enforce maximum shedding limits per group
    4. Gradual curtailment (not sudden)
    5. Time-based rules (e.g., HVAC off at night)
    """
    
    def __init__(self):
        """Initialize load groups with Amrita campus configuration"""
        
        self.load_groups: Dict[str, LoadGroup] = {
            # CRITICAL - never shed
            'data_center': LoadGroup(
                name='Data Center',
                current_power_kw=60,
                max_power_kw=60,
                priority=ShedPriority.NEVER,
                max_shed_percent=0
            ),
            'research_labs': LoadGroup(
                name='Research Labs',
                current_power_kw=80,
                max_power_kw=80,
                priority=ShedPriority.ESSENTIAL,
                max_shed_percent=30,  # Can pause non-critical experiments
                min_runtime_minutes=60
            ),
            'medical_clinic': LoadGroup(
                name='Medical Clinic',
                current_power_kw=15,
                max_power_kw=15,
                priority=ShedPriority.NEVER,
                max_shed_percent=0
            ),
            'communication': LoadGroup(
                name='Communication Systems',
                current_power_kw=25,
                max_power_kw=25,
                priority=ShedPriority.NEVER,
                max_shed_percent=0
            ),
            
            # ESSENTIAL - can reduce
            'hvac_essential': LoadGroup(
                name='Essential HVAC',
                current_power_kw=80,
                max_power_kw=80,
                min_power_kw=60,  # Keep minimum for server cooling
                priority=ShedPriority.ESSENTIAL,
                max_shed_percent=25
            ),
            'emergency_lighting': LoadGroup(
                name='Emergency Lighting',
                current_power_kw=20,
                max_power_kw=20,
                priority=ShedPriority.ESSENTIAL,
                max_shed_percent=0
            ),
            
            # FLEXIBLE - can shed
            'hvac_non_critical': LoadGroup(
                name='Non-Critical HVAC',
                current_power_kw=100,
                max_power_kw=100,
                min_power_kw=20,
                priority=ShedPriority.FLEXIBLE,
                max_shed_percent=80
            ),
            'general_lighting': LoadGroup(
                name='General Lighting',
                current_power_kw=80,
                max_power_kw=80,
                min_power_kw=10,
                priority=ShedPriority.FLEXIBLE,
                max_shed_percent=70  # Can dim lights
            ),
            'office_equipment': LoadGroup(
                name='Office Equipment',
                current_power_kw=120,
                max_power_kw=120,
                min_power_kw=20,
                priority=ShedPriority.FLEXIBLE,
                max_shed_percent=75
            ),
            'lab_non_essential': LoadGroup(
                name='Non-Essential Lab',
                current_power_kw=60,
                max_power_kw=60,
                min_power_kw=0,
                priority=ShedPriority.FLEXIBLE,
                max_shed_percent=90
            )
        }
        
        logger.info("Load Shedding Policy initialized")
    
    def get_shedding_sequence(self, required_shed_kw: float) -> Dict[str, float]:
        """
        Calculate optimal shedding sequence
        Returns: {group_name: shed_kw_from_this_group}
        """
        result = {}
        remaining_shed = required_shed_kw
        
        # Sort groups by priority (shed lowest priority first)
        sorted_groups = sorted(
            self.load_groups.items(),
            key=lambda x: x[1].priority.value,
            reverse=True
        )
        
        for group_name, group in sorted_groups:
            if remaining_shed <= 0:
                break
            
            # Maximum we can shed from this group
            max_available = group.get_available_shed_kw()
            shed_from_group = min(remaining_shed, max_available)
            
            if shed_from_group > 0:
                result[group_name] = shed_from_group
                remaining_shed -= shed_from_group
        
        return result
    
    def apply_shedding(self, shedding_sequence: Dict[str, float]) -> Dict[str, float]:
        """
        Apply shedding and return new power levels
        """
        result = {}
        
        for group_name, group in self.load_groups.items():
            if group_name in shedding_sequence:
                shed_amount = shedding_sequence[group_name]
                new_power = max(group.min_power_kw, 
                               group.current_power_kw - shed_amount)
                result[group_name] = new_power
                group.current_power_kw = new_power
            else:
                result[group_name] = group.current_power_kw
        
        return result
    
    def get_time_based_curtailment(self, hour_of_day: int) -> Dict[str, float]:
        """
        Apply time-based operational rules
        Returns adjustment factors (1.0 = normal, 0.5 = 50% of normal)
        """
        adjustments = {name: 1.0 for name in self.load_groups}
        
        # Night time: reduce non-critical HVAC
        if hour_of_day >= 22 or hour_of_day < 6:
            adjustments['hvac_non_critical'] = 0.3
            adjustments['general_lighting'] = 0.2
            adjustments['office_equipment'] = 0.4
        
        # Early morning: ramp up gradually
        elif 6 <= hour_of_day < 8:
            adjustments['general_lighting'] = 0.7
            adjustments['hvac_non_critical'] = 0.6
            adjustments['office_equipment'] = 0.5
        
        # Peak hours: potential curtailment
        elif 13 <= hour_of_day < 17:
            # Can reduce non-critical slightly during peak
            adjustments['lab_non_essential'] = 0.8
            adjustments['office_equipment'] = 0.85
        
        return adjustments
    
    def get_total_power(self) -> float:
        """Get total current power across all groups"""
        return sum(group.current_power_kw for group in self.load_groups.values())
    
    def get_critical_load(self) -> float:
        """Get non-sheddable critical load"""
        critical = 0
        for group in self.load_groups.values():
            if group.priority == ShedPriority.NEVER:
                critical += group.current_power_kw
        return critical
    
    def get_essential_load(self) -> float:
        """Get load that shouldn't be shed unless necessary"""
        essential = 0
        for group in self.load_groups.values():
            if group.priority in [ShedPriority.NEVER, ShedPriority.ESSENTIAL]:
                essential += group.current_power_kw
        return essential
    
    def get_flexible_load(self) -> float:
        """Get load that can be shed"""
        flexible = 0
        for group in self.load_groups.values():
            if group.priority == ShedPriority.FLEXIBLE:
                flexible += group.current_power_kw
        return flexible
    
    def get_shedding_potential(self) -> float:
        """Get maximum kW that could be shed"""
        potential = 0
        for group in self.load_groups.values():
            potential += group.get_available_shed_kw()
        return potential
    
    def get_status_report(self) -> Dict:
        """Get detailed status of all load groups"""
        return {
            'timestamp': None,
            'total_power_kw': self.get_total_power(),
            'critical_load_kw': self.get_critical_load(),
            'essential_load_kw': self.get_essential_load(),
            'flexible_load_kw': self.get_flexible_load(),
            'shedding_potential_kw': self.get_shedding_potential(),
            'groups': {
                name: {
                    'power_kw': group.current_power_kw,
                    'priority': group.priority.name,
                    'available_shed_kw': group.get_available_shed_kw()
                }
                for name, group in self.load_groups.items()
            }
        }


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    policy = LoadShedPolicy()
    
    print("\n=== Load Shedding Policy Report ===")
    report = policy.get_status_report()
    print(f"Total Load: {report['total_power_kw']:.1f} kW")
    print(f"Critical Load (never shed): {report['critical_load_kw']:.1f} kW")
    print(f"Essential Load: {report['essential_load_kw']:.1f} kW")
    print(f"Flexible Load: {report['flexible_load_kw']:.1f} kW")
    print(f"Maximum Shedding Potential: {report['shedding_potential_kw']:.1f} kW")
    
    print("\n=== Load Groups ===")
    for name, group in policy.load_groups.items():
        print(f"{name:25} {group.current_power_kw:6.1f} kW  "
              f"[Priority: {group.priority.name:10} Shed: {group.get_available_shed_kw():.1f} kW]")
    
    print("\n=== Example: Need to Shed 200 kW ===")
    shedding = policy.get_shedding_sequence(200)
    for group, amount in shedding.items():
        print(f"  {group:25} shed {amount:6.1f} kW")
