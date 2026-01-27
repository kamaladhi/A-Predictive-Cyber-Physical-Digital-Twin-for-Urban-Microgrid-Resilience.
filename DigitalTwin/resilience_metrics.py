"""
Enhanced Resilience Metrics Calculator

Implements IEEE 2030.5-aligned metrics with proper critical load tracking,
priority violation detection, and cascading failure risk assessment.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
from datetime import datetime, timedelta
from DigitalTwin.twin_state import TwinState
from EMS.city_ems import MicrogridPriority
import logging

logger = logging.getLogger(__name__)


@dataclass
class DetailedResilienceScorecard:
    """Comprehensive resilience metrics with IEEE 2030.5 alignment"""
    
    # Primary Metrics
    city_survivability_index: float = 1.0  # 0-1 composite score
    critical_load_preservation_ratio: float = 1.0  # 0-1, must be > 0.95
    
    # Energy Metrics
    total_unserved_energy_kwh: float = 0.0
    critical_unserved_energy_kwh: float = 0.0  # NEW: Track critical separately
    non_critical_unserved_energy_kwh: float = 0.0
    
    # Time Metrics
    recovery_time_hours: float = 0.0
    time_to_first_critical_failure_hours: Optional[float] = None  # NEW
    cumulative_critical_downtime_hours: float = 0.0  # NEW
    
    # Priority Metrics
    priority_violation_penalty: float = 0.0
    priority_violation_count: int = 0  # NEW: Number of timesteps with violations
    priority_inversion_events: List[dict] = field(default_factory=list)  # NEW: Detailed tracking
    
    # Risk Metrics
    cascading_failure_risk_score: float = 0.0  # NEW: 0-1 probability estimate
    resource_exhaustion_timeline: Dict[str, float] = field(default_factory=dict)  # NEW
    
    # Per-Microgrid Breakdown
    microgrid_metrics: Dict[str, dict] = field(default_factory=dict)
    
    # Confidence
    state_estimation_confidence: float = 1.0  # NEW: From state estimator

    @property
    def critical_load_preservation_rate(self) -> float:
        """Alias for backward compatibility."""
        return self.critical_load_preservation_ratio

    @property
    def state_confidence(self) -> float:
        """Alias for backward compatibility."""
        return self.state_estimation_confidence

    def __getattr__(self, name):
        """Gracefully provide legacy aliases when cached bytecode lags behind source."""
        if name == "state_confidence":
            return self.state_estimation_confidence
        if name == "critical_load_preservation_rate":
            return self.critical_load_preservation_ratio
        raise AttributeError(name)


class EnhancedResilienceMetricCalculator:
    """
    Advanced resilience metric computation with proper critical load tracking.
    """
    
    def __init__(self, priority_map: Dict[str, MicrogridPriority]):
        self.priority_map = priority_map
        
        # Cumulative tracking
        self.cumulative_critical_served = 0.0
        self.cumulative_critical_demand = 0.0
        self.cumulative_critical_unserved = 0.0  # NEW
        self.cumulative_non_critical_unserved = 0.0  # NEW
        self.cumulative_total_unserved = 0.0
        
        # Time tracking
        self.outage_start_time = None
        self.outage_end_time = None
        self.first_critical_failure_time = None  # NEW
        self.critical_downtime_steps = 0  # NEW
        
        # Priority tracking
        self.priority_violations = 0
        self.priority_violation_timesteps = 0  # NEW
        self.priority_inversion_log = []  # NEW
        
        # Per-microgrid tracking
        self.mg_critical_served: Dict[str, float] = {}
        self.mg_critical_demand: Dict[str, float] = {}
        self.mg_total_unserved: Dict[str, float] = {}
        
        # Priority weights for penalty calculation
        self.priority_weights = {
            MicrogridPriority.CRITICAL: 100.0,
            MicrogridPriority.HIGH: 50.0,
            MicrogridPriority.MEDIUM: 20.0,
            MicrogridPriority.LOW: 10.0
        }
        
        # Cascading failure tracking
        self.low_battery_warnings: Dict[str, List[datetime]] = {}  # NEW
        
    def update(self, twin_state: TwinState, dt_hours: float):
        """
        Update cumulative metrics based on current TwinState.
        NOW PROPERLY TRACKS CRITICAL VS NON-CRITICAL LOAD.
        """
        physical = twin_state.physical
        timestamp = twin_state.timestamp
        
        # Track outage timing
        if twin_state.is_outage_active:
            if self.outage_start_time is None:
                self.outage_start_time = timestamp
        else:
            if self.outage_start_time is not None and self.outage_end_time is None:
                self.outage_end_time = timestamp
        
        # Initialize per-MG tracking
        for mg_id in physical.microgrid_states.keys():
            if mg_id not in self.mg_critical_served:
                self.mg_critical_served[mg_id] = 0.0
                self.mg_critical_demand[mg_id] = 0.0
                self.mg_total_unserved[mg_id] = 0.0
        
        # Check for priority violations this timestep
        violation_this_step = False
        
        # Process each microgrid
        for mg_id, status in physical.microgrid_states.items():
            shed_kw = status.load_shed_kw
            critical_load_kw = status.critical_load_kw
            total_load_kw = status.total_load_kw
            
            # Calculate non-critical load
            non_critical_load_kw = total_load_kw - critical_load_kw
            
            # Determine how much critical load was actually shed
            # Logic: If shed > non_critical, then some critical is shed
            critical_shed_kw = max(0.0, shed_kw - non_critical_load_kw)
            non_critical_shed_kw = min(shed_kw, non_critical_load_kw)
            
            # Update cumulative critical tracking
            critical_served_kw = critical_load_kw - critical_shed_kw
            self.cumulative_critical_demand += critical_load_kw * dt_hours
            self.cumulative_critical_served += critical_served_kw * dt_hours
            self.cumulative_critical_unserved += critical_shed_kw * dt_hours
            
            # Update non-critical tracking
            self.cumulative_non_critical_unserved += non_critical_shed_kw * dt_hours
            
            # Update total unserved
            self.cumulative_total_unserved += shed_kw * dt_hours
            
            # Per-MG tracking
            self.mg_critical_demand[mg_id] += critical_load_kw * dt_hours
            self.mg_critical_served[mg_id] += critical_served_kw * dt_hours
            self.mg_total_unserved[mg_id] += shed_kw * dt_hours
            
            # Track first critical failure
            if critical_shed_kw > 0.1 and self.first_critical_failure_time is None:
                self.first_critical_failure_time = timestamp
                logger.warning(
                    f"First critical load failure at {timestamp}: "
                    f"{mg_id} shedding {critical_shed_kw:.1f} kW critical load"
                )
            
            # Track critical downtime
            if critical_shed_kw > 0.1:
                self.critical_downtime_steps += 1
            
            # Priority violation detection: High-priority MG shedding while low-priority is not
            priority = self.priority_map.get(mg_id, MicrogridPriority.LOW)
            weight = self.priority_weights.get(priority, 10.0)
            
            if critical_shed_kw > 0:
                # This MG is shedding critical load - check if lower-priority MGs have spare capacity
                violation = self._check_priority_inversion(
                    mg_id, priority, critical_shed_kw, physical.microgrid_states, timestamp
                )
                if violation:
                    violation_this_step = True
                    self.priority_inversion_log.append(violation)
                
                # Add weighted penalty
                self.priority_violations += (critical_shed_kw * weight * dt_hours)
            
            # Cascading failure risk: Track low battery warnings
            if status.battery_soc_percent < 20.0 and status.is_islanded:
                if mg_id not in self.low_battery_warnings:
                    self.low_battery_warnings[mg_id] = []
                self.low_battery_warnings[mg_id].append(timestamp)
        
        # Count violation timesteps
        if violation_this_step:
            self.priority_violation_timesteps += 1
    
    def _check_priority_inversion(
        self, 
        failing_mg_id: str,
        failing_priority: MicrogridPriority,
        shed_amount: float,
        all_statuses: Dict,
        timestamp: datetime
    ) -> Optional[dict]:
        """
        Detect if a higher-priority MG is shedding load while lower-priority
        MGs have available resources.
        
        Returns violation details if detected, None otherwise.
        """
        # Define priority ordering
        priority_order = [
            MicrogridPriority.CRITICAL,
            MicrogridPriority.HIGH,
            MicrogridPriority.MEDIUM,
            MicrogridPriority.LOW
        ]
        
        failing_priority_level = priority_order.index(failing_priority)
        
        # Check all lower-priority microgrids
        for mg_id, status in all_statuses.items():
            if mg_id == failing_mg_id:
                continue
            
            mg_priority = self.priority_map.get(mg_id, MicrogridPriority.LOW)
            mg_priority_level = priority_order.index(mg_priority)
            
            # Is this MG lower priority?
            if mg_priority_level > failing_priority_level:
                # Does it have spare capacity that could help?
                # Check: Not shedding load AND (has battery capacity OR generator available)
                has_spare_battery = status.battery_soc_percent > 30.0
                not_shedding = status.load_shed_kw < 0.1
                
                if not_shedding and has_spare_battery:
                    # VIOLATION: Lower priority MG has resources while higher priority is failing
                    return {
                        'timestamp': timestamp,
                        'failing_mg': failing_mg_id,
                        'failing_priority': failing_priority.value,
                        'shed_amount_kw': shed_amount,
                        'violating_mg': mg_id,
                        'violating_priority': mg_priority.value,
                        'violating_mg_battery_soc': status.battery_soc_percent,
                        'severity': 'CRITICAL' if failing_priority == MicrogridPriority.CRITICAL else 'HIGH'
                    }
        
        return None
    
    def compute_final_metrics(self, state_confidence: Optional[float] = None) -> DetailedResilienceScorecard:
        """
        Compute final resilience scorecard with all metrics.
        """
        # Critical Load Preservation Ratio
        clpr = 1.0
        if self.cumulative_critical_demand > 0:
            clpr = self.cumulative_critical_served / self.cumulative_critical_demand
        
        # Recovery Time
        recovery_hours = 0.0
        if self.outage_end_time and self.outage_start_time:
            recovery_hours = (self.outage_end_time - self.outage_start_time).total_seconds() / 3600.0
        
        # Time to first critical failure
        time_to_failure = None
        if self.first_critical_failure_time and self.outage_start_time:
            time_to_failure = (
                self.first_critical_failure_time - self.outage_start_time
            ).total_seconds() / 3600.0
        
        # Cumulative critical downtime
        critical_downtime_hours = self.critical_downtime_steps * 0.25  # 15-min steps
        
        # City Survivability Index
        # Formula: Weighted combination of CLPR and unserved energy penalty
        # CSI = CLPR * exp(-lambda * critical_unserved) * (1 - priority_penalty_factor)
        
        energy_penalty = np.exp(-1e-4 * self.cumulative_critical_unserved)
        
        # Normalize priority violations
        priority_penalty_factor = min(
            1.0, 
            self.priority_violation_timesteps / max(1, self.critical_downtime_steps)
        )
        
        survivability = clpr * energy_penalty * (1.0 - 0.3 * priority_penalty_factor)
        
        # Cascading failure risk
        # Count how many MGs had repeated low-battery warnings
        cascading_risk = 0.0
        for mg_id, warnings in self.low_battery_warnings.items():
            if len(warnings) > 5:  # Multiple warnings = high risk
                cascading_risk += 0.25
        cascading_risk = min(1.0, cascading_risk)
        
        # Per-microgrid breakdown
        mg_metrics = {}
        for mg_id in self.mg_critical_demand.keys():
            if self.mg_critical_demand[mg_id] > 0:
                mg_clpr = self.mg_critical_served[mg_id] / self.mg_critical_demand[mg_id]
            else:
                mg_clpr = 1.0
            
            mg_metrics[mg_id] = {
                'critical_preservation_ratio': mg_clpr,
                'total_unserved_kwh': self.mg_total_unserved[mg_id],
                'low_battery_warnings': len(self.low_battery_warnings.get(mg_id, []))
            }
        
        return DetailedResilienceScorecard(
            city_survivability_index=survivability,
            critical_load_preservation_ratio=clpr,
            total_unserved_energy_kwh=self.cumulative_total_unserved,
            critical_unserved_energy_kwh=self.cumulative_critical_unserved,
            non_critical_unserved_energy_kwh=self.cumulative_non_critical_unserved,
            recovery_time_hours=recovery_hours,
            time_to_first_critical_failure_hours=time_to_failure,
            cumulative_critical_downtime_hours=critical_downtime_hours,
            priority_violation_penalty=self.priority_violations,
            priority_violation_count=self.priority_violation_timesteps,
            priority_inversion_events=self.priority_inversion_log,
            cascading_failure_risk_score=cascading_risk,
            microgrid_metrics=mg_metrics,
            state_estimation_confidence=state_confidence if state_confidence else 1.0
        )
    
    def print_summary(self, scorecard: DetailedResilienceScorecard):
        """Print human-readable summary of metrics"""
        print("\n" + "="*60)
        print("RESILIENCE SCORECARD SUMMARY")
        print("="*60)
        
        print(f"\nCity Survivability Index: {scorecard.city_survivability_index:.3f}")
        print(f"   (Target: > 0.90 for resilient operation)")
        
        print(f"\nCritical Load Preservation: {scorecard.critical_load_preservation_ratio*100:.1f}%")
        print(f"   (Target: > 95%)")
        
        print(f"\nEnergy Metrics:")
        print(f"   Total Unserved: {scorecard.total_unserved_energy_kwh:.1f} kWh")
        print(f"   Critical Unserved: {scorecard.critical_unserved_energy_kwh:.1f} kWh")
        print(f"   Non-Critical Unserved: {scorecard.non_critical_unserved_energy_kwh:.1f} kWh")
        
        print(f"\nTime Metrics:")
        print(f"   Recovery Time: {scorecard.recovery_time_hours:.1f} hours")
        if scorecard.time_to_first_critical_failure_hours:
            print(f"   Time to First Critical Failure: {scorecard.time_to_first_critical_failure_hours:.1f} hours")
        print(f"   Total Critical Downtime: {scorecard.cumulative_critical_downtime_hours:.1f} hours")
        
        print(f"\nPriority Compliance:")
        print(f"   Violation Timesteps: {scorecard.priority_violation_count}")
        if scorecard.priority_inversion_events:
            print(f"   {len(scorecard.priority_inversion_events)} Priority Inversion Events Detected!")
            for event in scorecard.priority_inversion_events[:3]:  # Show first 3
                print(f"      - {event['timestamp']}: {event['failing_mg']} ({event['failing_priority']}) "
                      f"shedding while {event['violating_mg']} ({event['violating_priority']}) has capacity")
        
        print(f"\nRisk Assessment:")
        print(f"   Cascading Failure Risk: {scorecard.cascading_failure_risk_score:.2f}")
        
        print(f"\nPer-Microgrid Performance:")
        for mg_id, metrics in scorecard.microgrid_metrics.items():
            print(f"   {mg_id.upper()}:")
            print(f"      Critical Preservation: {metrics['critical_preservation_ratio']*100:.1f}%")
            print(f"      Total Unserved: {metrics['total_unserved_kwh']:.1f} kWh")
            if metrics['low_battery_warnings'] > 0:
                print(f"      Low Battery Warnings: {metrics['low_battery_warnings']}")
        
        print("\n" + "="*60 + "\n")


# Example usage
if __name__ == "__main__":
    print("Enhanced Resilience Metric Calculator initialized.")
    print("Key improvements:")
    print("  - Separate critical vs non-critical unserved energy tracking")
    print("  - Priority inversion detection with detailed logging")
    print("  - Time-to-failure and downtime metrics")
    print("  - Cascading failure risk assessment")
    print("  - Per-microgrid breakdown")