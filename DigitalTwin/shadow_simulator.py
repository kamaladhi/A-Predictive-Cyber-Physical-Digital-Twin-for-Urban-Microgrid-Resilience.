"""
Shadow Simulator - Enables What-If Analysis and Predictive Optimization

This module implements a fast-forward simulation capability that allows the
Digital Twin to test different control strategies before deploying them.
"""

import copy
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
from dataclasses import dataclass

@dataclass
class PredictionScenario:
    """Configuration for a shadow simulation run"""
    scenario_id: str
    description: str
    duration_hours: float
    modified_policies: Dict[str, dict]  # Override EMS policies for what-if
    assumed_outages: List  # Additional outage events to test
    

@dataclass
class PredictionResult:
    """Outcome of a shadow simulation"""
    scenario_id: str
    final_survivability_index: float
    critical_load_preservation: float
    total_unserved_energy_kwh: float
    time_to_critical_failure_hours: Optional[float]  # When first critical load is shed
    resource_exhaustion_timeline: Dict[str, float]  # microgrid_id -> hours until battery empty
    recommended_actions: List[str]  # Human-readable suggestions


class ShadowSimulator:
    """
    Fast-forward simulation engine for predictive Digital Twin capabilities.
    
    Key Features:
    1. Clones current system state and runs forward in time
    2. Tests alternative control policies without affecting physical system
    3. Provides decision support for CityEMS
    """
    
    def __init__(self, digital_twin_manager):
        """
        Args:
            digital_twin_manager: Reference to main DT manager to access simulators
        """
        self.dt_manager = digital_twin_manager
        self.acceleration_factor = 10.0  # Run 10x faster than real-time
        
    def predict_impact(
        self, 
        current_state,
        scenarios: List[PredictionScenario],
        mc_samples: int = 10
    ) -> Dict[str, PredictionResult]:
        """
        Run multiple shadow simulations to evaluate different strategies.
        
        Args:
            current_state: Current TwinState to use as starting point
            scenarios: List of what-if scenarios to test
            mc_samples: Number of Monte Carlo samples for stochastic scenarios
            
        Returns:
            Dictionary mapping scenario_id to PredictionResult
        """
        results = {}
        
        for scenario in scenarios:
            # Run Monte Carlo samples if outages are stochastic
            sample_results = []
            
            for sample in range(mc_samples):
                result = self._run_single_shadow_simulation(
                    current_state, 
                    scenario,
                    sample_idx=sample
                )
                sample_results.append(result)
            
            # Aggregate Monte Carlo results
            results[scenario.scenario_id] = self._aggregate_mc_results(
                scenario.scenario_id,
                sample_results
            )
            
        return results
    
    def _run_single_shadow_simulation(
        self,
        initial_state,
        scenario: PredictionScenario,
        sample_idx: int
    ) -> PredictionResult:
        """Execute one forward simulation"""
        
        # Clone simulators to avoid modifying real state
        shadow_simulators = self._clone_simulators(initial_state)
        
        # Create shadow EMS with modified policies
        shadow_ems = self._create_shadow_ems(scenario.modified_policies)
        
        # Setup shadow scenario engine
        from DigitalTwin.scenario_engine import ScenarioEngine, ScenarioConfig
        shadow_config = ScenarioConfig(
            scenario_id=f"shadow_{scenario.scenario_id}_{sample_idx}",
            name="Shadow Simulation",
            description="Predictive run",
            start_time=initial_state.timestamp,
            duration_hours=scenario.duration_hours,
            outage_events=scenario.assumed_outages
        )
        shadow_scenario_engine = ScenarioEngine(shadow_config)
        
        # Run simulation loop
        current_time = initial_state.timestamp
        steps = int(scenario.duration_hours * 3600 / 900)  # 15-min steps
        
        cumulative_unserved = 0.0
        time_to_critical_failure = None
        resource_timeline = {}
        
        for step in range(steps):
            # Check grid availability
            grid_availability = {}
            for mg_id in shadow_simulators.keys():
                grid_availability[mg_id] = shadow_scenario_engine.get_grid_availability(
                    current_time, mg_id
                )
            
            # Step each shadow simulator
            mg_statuses = {}
            for mg_id, sim in shadow_simulators.items():
                # Get supervisory command from shadow EMS
                # (Simplified - in full implementation, shadow EMS would run full logic)
                cmd = None
                
                data = sim.step(
                    grid_available=grid_availability[mg_id],
                    supervisory_cmd=cmd
                )
                
                mg_statuses[mg_id] = data
                
                # Track critical metrics
                if data.get('critical_load_shed', False) and time_to_critical_failure is None:
                    time_to_critical_failure = step * 0.25  # hours
                
                cumulative_unserved += data.get('shed_load_kw', 0) * 0.25
                
                # Estimate time to resource exhaustion
                if mg_id not in resource_timeline:
                    battery_soc = data.get('battery_soc_percent', 100)
                    battery_capacity = self.dt_manager.microgrid_configs[mg_id].battery.nominal_capacity_kwh
                    load_kw = data.get('critical_load_kw', 0)
                    
                    if load_kw > 0 and battery_soc < 90:
                        hours_remaining = (battery_soc / 100.0 * battery_capacity) / load_kw
                        resource_timeline[mg_id] = hours_remaining
            
            current_time += timedelta(minutes=15)
        
        # Calculate final metrics
        survivability = self._compute_survivability(mg_statuses, cumulative_unserved)
        preservation = self._compute_preservation(mg_statuses)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            scenario,
            mg_statuses,
            time_to_critical_failure,
            resource_timeline
        )
        
        return PredictionResult(
            scenario_id=scenario.scenario_id,
            final_survivability_index=survivability,
            critical_load_preservation=preservation,
            total_unserved_energy_kwh=cumulative_unserved,
            time_to_critical_failure_hours=time_to_critical_failure,
            resource_exhaustion_timeline=resource_timeline,
            recommended_actions=recommendations
        )
    
    def _clone_simulators(self, initial_state) -> Dict:
        """Create deep copies of simulators initialized to current state"""
        cloned = {}
        
        for mg_id, sim in self.dt_manager.simulators.items():
            # Deep copy the simulator
            cloned_sim = copy.deepcopy(sim)
            
            # Set internal state from TwinState
            status = initial_state.physical.microgrid_states[mg_id]
            cloned_sim.battery.soc_percent = status.battery_soc_percent
            cloned_sim.battery.energy_kwh = status.battery_capacity_kwh
            # Note: Full implementation would sync all component states
            
            cloned[mg_id] = cloned_sim
            
        return cloned
    
    def _create_shadow_ems(self, policy_overrides: Dict) -> object:
        """Create a shadow CityEMS with modified policies"""
        # Simplified - would create actual CityEMS instance
        return None
    
    def _compute_survivability(self, final_states: Dict, unserved: float) -> float:
        """Compute end-state survivability index"""
        # Average battery levels weighted by priority
        weighted_soc = 0.0
        total_weight = 0.0
        
        for mg_id, data in final_states.items():
            priority = self.dt_manager.city_ems.priority_map.get(mg_id)
            weight = 100.0 if priority.value == 'critical' else 50.0
            
            weighted_soc += data.get('battery_soc_percent', 0) * weight
            total_weight += weight
        
        avg_soc = weighted_soc / total_weight if total_weight > 0 else 0
        
        # Penalize based on unserved energy
        penalty = np.exp(-1e-5 * unserved)
        
        return (avg_soc / 100.0) * penalty
    
    def _compute_preservation(self, final_states: Dict) -> float:
        """Compute critical load preservation ratio"""
        # Simplified calculation
        preserved = sum(
            1 for data in final_states.values() 
            if not data.get('critical_load_shed', False)
        )
        return preserved / len(final_states) if final_states else 0.0
    
    def _generate_recommendations(
        self,
        scenario: PredictionScenario,
        final_states: Dict,
        time_to_failure: Optional[float],
        resource_timeline: Dict
    ) -> List[str]:
        """Generate human-readable action recommendations"""
        recommendations = []
        
        if time_to_failure is not None and time_to_failure < 2.0:
            recommendations.append(
                f"CRITICAL: Critical load failure predicted in {time_to_failure:.1f} hours. "
                "Recommend immediate load shedding for low-priority microgrids."
            )
        
        for mg_id, hours in resource_timeline.items():
            if hours < 4.0:
                recommendations.append(
                    f"WARNING {mg_id.upper()}: Battery exhaustion predicted in {hours:.1f} hours. "
                    "Consider pre-emptive generator start or load reduction."
                )
        
        if not recommendations:
            recommendations.append(
                "System stable under this scenario. Current policies are adequate."
            )
        
        return recommendations
    
    def _aggregate_mc_results(
        self,
        scenario_id: str,
        sample_results: List[PredictionResult]
    ) -> PredictionResult:
        """Aggregate Monte Carlo samples into single result with confidence intervals"""
        
        # Compute statistics across samples
        survivability_values = [r.final_survivability_index for r in sample_results]
        preservation_values = [r.critical_load_preservation for r in sample_results]
        unserved_values = [r.total_unserved_energy_kwh for r in sample_results]
        
        # Use mean as point estimate
        return PredictionResult(
            scenario_id=scenario_id,
            final_survivability_index=np.mean(survivability_values),
            critical_load_preservation=np.mean(preservation_values),
            total_unserved_energy_kwh=np.mean(unserved_values),
            time_to_critical_failure_hours=np.mean([
                r.time_to_critical_failure_hours for r in sample_results 
                if r.time_to_critical_failure_hours is not None
            ]) if any(r.time_to_critical_failure_hours for r in sample_results) else None,
            resource_exhaustion_timeline={},  # Would aggregate timeline data
            recommended_actions=sample_results[0].recommended_actions  # Use first sample
        )


# Example Usage
if __name__ == "__main__":
    # This would be called by DigitalTwinManager during runtime
    
    # Example: Test two strategies
    scenarios = [
        PredictionScenario(
            scenario_id="conservative",
            description="Shed 30% load immediately in low-priority MGs",
            duration_hours=4.0,
            modified_policies={
                'residential': {'max_shed_percent': 30},
                'industrial': {'max_shed_percent': 20}
            },
            assumed_outages=[]  # No additional outages
        ),
        PredictionScenario(
            scenario_id="wait_and_see",
            description="Wait 1 hour before shedding load",
            duration_hours=4.0,
            modified_policies={
                'residential': {'shed_delay_hours': 1.0}
            },
            assumed_outages=[]
        )
    ]
    
    # Shadow simulator would predict which strategy performs better
    print("Shadow simulation configured. Ready to predict outcomes.")