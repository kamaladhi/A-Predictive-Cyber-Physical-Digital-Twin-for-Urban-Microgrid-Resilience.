"""
================================================================================
ENHANCED DIGITAL TWIN MANAGER
================================================================================

Module Purpose:
    Orchestrates the complete Digital Twin system for urban microgrid coordination.
    Acts as the meta-control layer that synchronizes all microgrids, injects fault
    scenarios, estimates system state, and computes resilience metrics.

Key Features:
    1. Bidirectional data flow (Physical System <-> Digital Twin)
    2. Shadow simulation for predictive what-if analysis
    3. State estimation with uncertainty quantification (Kalman filtering)
    4. Advanced resilience metrics aligned with IEEE 2030.5
    5. Anomaly detection and decision support

Architecture Integration:
    - Layer 4: Digital Twin Core (this module)
    - Coordinates: 4 heterogeneous microgrids (Hospital, University, Industrial, Residential)
    - Manages: State synchronization, outage injection, resilience calculation
    - Outputs: TwinState, ResilienceScorecard, recommendations

Design Pattern:
    Composite pattern: Manages multiple MicrogridSimulator instances
    Observer pattern: Subscribes to all simulator state changes
    Strategy pattern: Selectable resilience policies and control algorithms

Dependencies:
    - DigitalTwin.*: Core DT modules (state, metrics, scenario, shadow sim, estimator)
    - Microgrid.*.*.simulator: 4 microgrid simulators
    - EMS.city_ems: City-level energy management system

Input: ScenarioConfig (outage timeline) + initial system state
Output: TwinState history + final ResilienceScorecard + control recommendations

================================================================================
"""

import sys
import os
import logging
from datetime import datetime, timedelta
import pandas as pd
from typing import Dict, List, Optional
import json
import numpy as np

# Setup paths
sys.path.insert(0, r"D:\Digital-twin-microgrid")

from DigitalTwin.twin_state import TwinState, PhysicalState, CyberState, ResilienceState
from DigitalTwin.outage_event_model import ScenarioConfig, OutageType, OutageEvent
from DigitalTwin.scenario_engine import ScenarioEngine

# Import enhanced modules (place these in DigitalTwin folder)
from DigitalTwin.shadow_simulator import ShadowSimulator, PredictionScenario, PredictionResult
from DigitalTwin.state_estimator import CityStateEstimator, StateEstimate
from DigitalTwin.resilience_metrics import EnhancedResilienceMetricCalculator, DetailedResilienceScorecard

# Import Simulators and EMS
from Microgrid.Hospital.hospital_simulator import MicrogridSimulator as HospitalSim
from Microgrid.Hospital.parameters import create_default_config as create_hospital_config

from Microgrid.university_microgrid.university_simulator import MicrogridSimulator as UniversitySim
from Microgrid.university_microgrid.parameters import create_default_config as create_university_config

from Microgrid.Industry_microgrid.industrial_simulator import MicrogridSimulator as IndustrialSim
from Microgrid.Industry_microgrid.industrial_parameters import create_default_config as create_industrial_config

from Microgrid.residence.residential_simulator import MicrogridSimulator as ResidentialSim
from Microgrid.residence.residential_parameters import create_default_config as create_residential_config

from EMS.city_ems import CityEMS, CityWideMeasurements, MicrogridStatus, MicrogridInfo, MicrogridPriority, CityOperationMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("EnhancedDigitalTwin")


class EnhancedDigitalTwinManager:
    """
    RESEARCH-GRADE DIGITAL TWIN MANAGER
    
    New Capabilities vs Original:
    1. Shadow Simulation for predictive what-if analysis
    2. State Estimation with Kalman filtering and confidence tracking
    3. Enhanced resilience metrics with proper critical load tracking
    4. Anomaly detection and diagnostics
    5. Decision support recommendations
    """
    
    def __init__(self, enable_shadow_simulation: bool = True, enable_state_estimation: bool = True):
        logger.info("Initializing Enhanced Digital Twin Layer...")
        
        self.enable_shadow_sim = enable_shadow_simulation
        self.enable_state_est = enable_state_estimation
        
        # 1. Initialize City EMS
        self.city_ems = CityEMS()
        
        # 2. Initialize Microgrid Simulators
        self.simulators = {}
        self.microgrid_configs = {}
        
        # Hospital
        hosp_conf = create_hospital_config()
        self.microgrid_configs['hospital'] = hosp_conf
        self.simulators['hospital'] = HospitalSim(hosp_conf)
        self._register_microgrid('hospital', hosp_conf, MicrogridPriority.CRITICAL, "hospital")

        # University
        univ_conf = create_university_config()
        self.microgrid_configs['university'] = univ_conf
        self.simulators['university'] = UniversitySim(univ_conf)
        self._register_microgrid('university', univ_conf, MicrogridPriority.HIGH, "university")

        # Industrial
        ind_conf = create_industrial_config()
        self.microgrid_configs['industrial'] = ind_conf
        self.simulators['industrial'] = IndustrialSim(ind_conf)
        self._register_microgrid('industrial', ind_conf, MicrogridPriority.MEDIUM, "industrial")

        # Residential
        res_conf = create_residential_config()
        self.microgrid_configs['residential'] = res_conf
        self.simulators['residential'] = ResidentialSim(res_conf)
        self._register_microgrid('residential', res_conf, MicrogridPriority.LOW, "residential")

        # 3. NEW: Initialize State Estimator
        if self.enable_state_est:
            self.state_estimator = CityStateEstimator(self.microgrid_configs)
            logger.info("State Estimator initialized with Kalman filtering")
        else:
            self.state_estimator = None
        
        # 4. NEW: Initialize Shadow Simulator
        if self.enable_shadow_sim:
            self.shadow_sim = ShadowSimulator(self)
            logger.info("Shadow Simulator initialized for predictive analysis")
        else:
            self.shadow_sim = None
        
        # 5. NEW: Enhanced Resilience Calculator
        self.resilience_calculator = EnhancedResilienceMetricCalculator(self.city_ems.priority_map)
        logger.info("Enhanced Resilience Metrics initialized")
        
        # 6. State & History
        self.history: List[TwinState] = []
        self.state_estimates_history: List[Dict[str, StateEstimate]] = []
        self.prediction_history: List[Dict[str, PredictionResult]] = []
        
    def _get_generator_capacity(self, config) -> float:
        """Handle heterogeneous config schemas for generator capacity"""
        if hasattr(config.generator, 'total_capacity_kw'):
            return config.generator.total_capacity_kw
        elif hasattr(config.generator, 'rated_power_kw'):
            return config.generator.rated_power_kw
        elif hasattr(config.generator, 'gen1_rated_power_kw'):
            return config.generator.gen1_rated_power_kw
        return 0.0

    def _get_fuel_capacity(self, config) -> float:
        """Handle heterogeneous config schemas for fuel capacity"""
        if hasattr(config.generator, 'fuel_tank_capacity_liters'):
            return config.generator.fuel_tank_capacity_liters
        return 5000.0

    def _register_microgrid(self, mg_id: str, config, priority: MicrogridPriority, mg_type: str):
        """Helper to register MG with City EMS"""
        gen_cap = self._get_generator_capacity(config)
        total_cap = gen_cap + config.pv.installed_capacity_kwp
        crit_load = config.load_profile.total_critical_load
        
        info = MicrogridInfo(
            microgrid_id=mg_id,
            microgrid_type=mg_type,
            priority=priority,
            location=(config.latitude, config.longitude),
            critical_load_kw=crit_load,
            total_capacity_kw=total_cap,
            battery_capacity_kwh=config.battery.nominal_capacity_kwh,
            pv_capacity_kwp=config.pv.installed_capacity_kwp,
            generator_capacity_kw=gen_cap,
            min_runtime_hours=0,
            max_shed_percent=100 if priority == MicrogridPriority.LOW else 50,
            can_share_power=False
        )
        self.city_ems.register_microgrid(info)

    def _convert_to_status(self, data: dict, mg_id: str) -> MicrogridStatus:
        """Adapter: Dictionary Output -> MicrogridStatus Object"""
        
        return MicrogridStatus(
            microgrid_id=mg_id,
            timestamp=data['timestamp'],
            operation_mode=data.get('operation_mode', 'UNKNOWN'),
            is_islanded=not data.get('grid_available', True),
            grid_available=data.get('grid_available', True),
            total_load_kw=data.get('total_load_kw', 0),
            critical_load_kw=data.get('critical_load_kw', 0),
            pv_generation_kw=data.get('pv_power_kw', 0),
            battery_power_kw=data.get('battery_power_kw', 0),
            generator_power_kw=data.get('gen1_power_kw', 0) + data.get('gen2_power_kw', 0) if 'gen2_power_kw' in data else data.get('gen1_power_kw', 0) + data.get('generator_power_kw', 0) if 'generator_power_kw' in data else 0,
            grid_power_kw=data.get('grid_power_kw', 0),
            battery_soc_percent=data.get('battery_soc_percent', 0),
            battery_capacity_kwh=data.get('battery_energy_kwh', 0),
            fuel_remaining_liters=1000,
            load_shed_kw=data.get('shed_load_kw', 0),
            load_shed_percent=(data.get('shed_load_kw', 0) / data.get('total_load_kw', 1) * 100),
            critical_load_shed=(data.get('shed_load_kw', 0) > (data.get('total_load_kw', 0) - data.get('critical_load_kw', 0))),
            estimated_runtime_hours=10.0,
            resource_criticality="healthy"
        )
    
    def _add_measurement_noise(self, data: dict) -> dict:
        """NEW: Simulate realistic sensor noise for state estimator validation"""
        noisy_data = data.copy()
        
        # Add Gaussian noise to key measurements
        noisy_data['battery_soc_percent'] = data['battery_soc_percent'] + np.random.normal(0, 2.0)
        noisy_data['battery_power_kw'] = data['battery_power_kw'] + np.random.normal(0, 0.5)
        noisy_data['total_load_kw'] = data['total_load_kw'] + np.random.normal(0, 0.3)
        
        # Clip to physical limits
        noisy_data['battery_soc_percent'] = np.clip(noisy_data['battery_soc_percent'], 0, 100)
        
        return noisy_data
    
    def run_enhanced_simulation(self, scenario_config: ScenarioConfig, use_predictive_control: bool = True) -> Dict:
        """
        Execute Enhanced Digital Twin Simulation with all new features.
        
        Args:
            scenario_config: Outage scenario definition
            use_predictive_control: If True, use shadow sim predictions to inform EMS
            
        Returns:
            Comprehensive results with metrics, predictions, and recommendations
        """
        logger.info(f"Starting Enhanced Digital Twin Simulation: {scenario_config.name}")
        
        scenario_engine = ScenarioEngine(scenario_config)
        start_time = scenario_config.start_time
        duration = scenario_config.duration_hours
        steps = int(duration * 3600 / 900)
        
        current_time = start_time
        
        # Reset Simulators
        for sim in self.simulators.values():
            sim.reset(start_time)
        
        last_city_output = None
        all_twin_states = []
        
        for step in range(steps):
            # =====================================
            # NEW: Run Predictive Look-Ahead Every Hour
            # =====================================
            if self.enable_shadow_sim and use_predictive_control and step % 4 == 0:  # Every hour
                logger.info(f"[Step {step}] Running shadow simulation for predictive control...")
                
                # Create what-if scenarios
                scenarios = [
                    PredictionScenario(
                        scenario_id="current_policy",
                        description="Continue current strategy",
                        duration_hours=4.0,
                        modified_policies={},
                        assumed_outages=scenario_config.outage_events
                    ),
                    PredictionScenario(
                        scenario_id="aggressive_shed",
                        description="Aggressive load shedding in low-priority MGs",
                        duration_hours=4.0,
                        modified_policies={
                            'residential': {'max_shed_percent': 80},
                            'industrial': {'max_shed_percent': 40}
                        },
                        assumed_outages=scenario_config.outage_events
                    )
                ]
                
                # Get current state for prediction
                if all_twin_states:
                    current_twin_state = all_twin_states[-1]
                    predictions = self.shadow_sim.predict_impact(
                        current_twin_state,
                        scenarios,
                        mc_samples=5  # 5 Monte Carlo samples
                    )
                    
                    self.prediction_history.append({
                        'timestamp': current_time,
                        'predictions': predictions
                    })
                    
                    # Log recommendations
                    for scenario_id, result in predictions.items():
                        logger.info(f"  {scenario_id}: CSI={result.final_survivability_index:.3f}")
                        for rec in result.recommended_actions:
                            logger.info(f"    {rec}")
            
            # =====================================
            # Step Physical Simulators
            # =====================================
            mg_statuses = {}
            raw_measurements = {}
            
            for mg_id, sim in self.simulators.items():
                grid_available = scenario_engine.get_grid_availability(current_time, mg_id)
                
                cmd = None
                if last_city_output and mg_id in last_city_output.supervisory_commands:
                    cmd = last_city_output.supervisory_commands[mg_id]
                
                # Get raw simulator output
                data = sim.step(grid_available=grid_available, supervisory_cmd=cmd)
                
                # NEW: Add measurement noise (optional - for validating state estimator)
                # noisy_data = self._add_measurement_noise(data)
                noisy_data = data  # Comment out if you want perfect measurements
                
                raw_measurements[mg_id] = noisy_data
                
                status = self._convert_to_status(noisy_data, mg_id)
                
                # Fix fuel tracking
                if 'gen1_fuel_liters' in data:
                    cap = self._get_fuel_capacity(self.microgrid_configs[mg_id])
                    status.fuel_remaining_liters = cap - data['gen1_fuel_liters']
                
                mg_statuses[mg_id] = status
            
            # =====================================
            # NEW: State Estimation
            # =====================================
            state_estimates = None
            if self.enable_state_est:
                state_estimates = self.state_estimator.update_all(
                    dt_seconds=900,
                    measurements=raw_measurements,
                    control_inputs={}  # Simplified
                )
                
                self.state_estimates_history.append(state_estimates)
                
                # Log anomalies
                for mg_id, estimate in state_estimates.items():
                    estimator = self.state_estimator.mg_estimators[mg_id]
                    anomaly = estimator.detect_anomaly()
                    if anomaly:
                        logger.warning(f"{mg_id}: {anomaly}")
            
            # =====================================
            # City EMS Coordination
            # =====================================
            city_meas = CityWideMeasurements(
                timestamp=current_time,
                microgrid_statuses=mg_statuses,
                total_load_kw=sum(s.total_load_kw for s in mg_statuses.values()),
                total_critical_load_kw=sum(s.critical_load_kw for s in mg_statuses.values()),
                total_generation_kw=sum(s.pv_generation_kw + s.generator_power_kw for s in mg_statuses.values()),
                total_battery_energy_kwh=sum(s.battery_capacity_kwh for s in mg_statuses.values()),
                total_fuel_liters=sum(s.fuel_remaining_liters for s in mg_statuses.values()),
                grid_outage_active=any(not s.grid_available for s in mg_statuses.values()),
                outage_start_time=start_time,
                outage_duration_hours=0,
                microgrids_islanded=sum(1 for s in mg_statuses.values() if s.is_islanded),
                microgrids_in_emergency=0,
                city_survivability_hours=12.0
            )
            
            last_city_output = self.city_ems.update(city_meas)
            
            # =====================================
            # Update Digital Twin State
            # =====================================
            phy_state = PhysicalState(
                timestamp=current_time,
                microgrid_states=mg_statuses,
                total_active_load_kw=sum(s.total_load_kw for s in mg_statuses.values()),
                total_generation_kw=sum(s.pv_generation_kw + s.generator_power_kw for s in mg_statuses.values()),
                total_battery_energy_kwh=sum(s.battery_capacity_kwh for s in mg_statuses.values()),
                grid_connection_status={mid: s.grid_available for mid, s in mg_statuses.items()}
            )
            
            cyber_state = CyberState(
                city_ems_outputs=last_city_output,
                local_ems_decisions={mg_id: {'mode': s.operation_mode, 'shed': s.load_shed_kw} for mg_id, s in mg_statuses.items()},
                communication_health={mid: True for mid in mg_statuses}
            )
            
            # Update resilience metrics
            temp_twin = TwinState(
                timestamp=current_time,
                sim_step=step,
                physical=phy_state,
                cyber=cyber_state,
                resilience=None,
                is_outage_active=any(not s.grid_available for s in mg_statuses.values())
            )
            
            self.resilience_calculator.update(temp_twin, 0.25)  # 15 min = 0.25 hours
            
            # Get confidence from state estimator
            confidence = 1.0
            if state_estimates:
                confidence = self.state_estimator.get_city_confidence_score(state_estimates)
            
            # Get current metrics
            current_scorecard = self.resilience_calculator.compute_final_metrics(confidence)
            
            res_state = ResilienceState(
                city_survivability_index=current_scorecard.city_survivability_index,
                critical_load_at_risk_kw=sum(s.critical_load_kw for s in mg_statuses.values() if s.is_islanded),
                unserved_energy_kwh=current_scorecard.total_unserved_energy_kwh,
                priority_violation_count=current_scorecard.priority_violation_count,
                current_survivability_horizon_hours=0.0
            )
            
            final_twin = TwinState(
                timestamp=current_time,
                sim_step=step,
                physical=phy_state,
                cyber=cyber_state,
                resilience=res_state,
                is_outage_active=any(not s.grid_available for s in mg_statuses.values())
            )
            
            all_twin_states.append(final_twin)
            
            # Progress logging
            if step % 20 == 0:
                logger.info(
                    f"Step {step}/{steps}: CSI={res_state.city_survivability_index:.3f}, "
                    f"CLPR={current_scorecard.critical_load_preservation_ratio*100:.1f}%, "
                    f"Confidence={confidence:.3f}"
                )
            
            current_time += timedelta(minutes=15)
        
        # =====================================
        # Finalize Results
        # =====================================
        logger.info("Simulation Complete. Computing final metrics...")
        
        final_metrics = self.resilience_calculator.compute_final_metrics(
            state_confidence=confidence
        )
        
        # Print comprehensive summary
        self.resilience_calculator.print_summary(final_metrics)
        
        return {
            "history": all_twin_states,
            "metrics": final_metrics,
            "state_estimates": self.state_estimates_history,
            "predictions": self.prediction_history,
            "recommendations": self._generate_final_recommendations(final_metrics)
        }
    
    def _generate_final_recommendations(self, scorecard: DetailedResilienceScorecard) -> List[str]:
        """Generate actionable recommendations based on results"""
        recommendations = []
        
        if scorecard.critical_load_preservation_ratio < 0.95:
            recommendations.append(
                "Critical load preservation below 95% target. "
                "Consider: (1) Increase battery capacity, (2) Add redundant generators, "
                "(3) Implement more aggressive early load shedding."
            )
        
        if scorecard.priority_violation_count > 0:
            recommendations.append(
                f"{scorecard.priority_violation_count} priority violations detected. "
                "Review CityEMS coordination logic to ensure high-priority microgrids "
                "receive resources before low-priority ones."
            )
        
        if scorecard.cascading_failure_risk_score > 0.5:
            recommendations.append(
                "High cascading failure risk detected. "
                "Implement battery reserve requirements to maintain buffer capacity."
            )
        
        if scorecard.city_survivability_index > 0.90:
            recommendations.append(
                "Excellent resilience performance. System meets target thresholds."
            )
        
        return recommendations
    
    def run_scenario(self, scenario_config: ScenarioConfig) -> Dict:
        """
        Simplified wrapper for run_enhanced_simulation with result formatting.
        
        Args:
            scenario_config: ScenarioConfig object defining the simulation
            
        Returns:
            Dictionary with formatted results including:
            - timestamps: List of datetime objects
            - resilience_scores: City survivability index over time
            - critical_load_preservation: Percentage of critical load served
            - microgrid_data: Per-microgrid time series data
            - final_metrics: ResilienceScorecard summary
        """
        logger.info(f"\n{'='*80}")
        logger.info(f"Running Scenario: {scenario_config.name}")
        logger.info(f"{'='*80}\n")
        
        # Run the enhanced simulation
        raw_results = self.run_enhanced_simulation(
            scenario_config, 
            use_predictive_control=self.enable_shadow_sim
        )
        
        # Extract and format results
        history = raw_results['history']
        final_metrics = raw_results['metrics']
        
        # Format time series data
        formatted_results = {
            'scenario_id': scenario_config.scenario_id,
            'scenario_name': scenario_config.name,
            'timestamps': [state.timestamp for state in history],
            'resilience_scores': [state.resilience.city_survivability_index for state in history],
            'critical_load_preservation': [
                final_metrics.critical_load_preservation_ratio * 100  # Use final value
                for _ in history
            ],
            'unserved_energy_kwh': [state.resilience.unserved_energy_kwh for state in history],
            'priority_violations': [state.resilience.priority_violation_count for state in history],
            
            # Per-microgrid data
            'microgrid_data': self._extract_microgrid_timeseries(history),
            
            # Final metrics
            'final_metrics': {
                'city_survivability_index': final_metrics.city_survivability_index,
                'critical_load_preservation_ratio': final_metrics.critical_load_preservation_ratio,
                'total_unserved_energy_kwh': final_metrics.total_unserved_energy_kwh,
                'critical_unserved_energy_kwh': final_metrics.critical_unserved_energy_kwh,
                'priority_violation_count': final_metrics.priority_violation_count,
                'recovery_time_hours': final_metrics.recovery_time_hours,
                'state_estimation_confidence': final_metrics.state_estimation_confidence
            },
            
            # Recommendations
            'recommendations': raw_results['recommendations']
        }
        
        logger.info(f"\n✅ Scenario '{scenario_config.name}' completed successfully")
        logger.info(f"   Final CSI: {final_metrics.city_survivability_index:.4f}")
        logger.info(f"   Critical Load Preservation: {final_metrics.critical_load_preservation_ratio*100:.2f}%")
        logger.info(f"   Total Unserved Energy: {final_metrics.total_unserved_energy_kwh:.2f} kWh\n")
        
        return formatted_results
    
    def _extract_microgrid_timeseries(self, history: List[TwinState]) -> Dict:
        """Extract per-microgrid time series data from history"""
        microgrid_data = {}
        
        for mg_id in self.simulators.keys():
            microgrid_data[mg_id] = {
                'timestamps': [],
                'total_load_kw': [],
                'critical_load_kw': [],
                'load_shed_kw': [],
                'battery_soc_percent': [],
                'battery_power_kw': [],
                'pv_generation_kw': [],
                'generator_power_kw': [],
                'grid_power_kw': [],
                'is_islanded': []
            }
        
        for state in history:
            for mg_id, mg_status in state.physical.microgrid_states.items():
                microgrid_data[mg_id]['timestamps'].append(state.timestamp)
                microgrid_data[mg_id]['total_load_kw'].append(mg_status.total_load_kw)
                microgrid_data[mg_id]['critical_load_kw'].append(mg_status.critical_load_kw)
                microgrid_data[mg_id]['load_shed_kw'].append(mg_status.load_shed_kw)
                microgrid_data[mg_id]['battery_soc_percent'].append(mg_status.battery_soc_percent)
                microgrid_data[mg_id]['battery_power_kw'].append(mg_status.battery_power_kw)
                microgrid_data[mg_id]['pv_generation_kw'].append(mg_status.pv_generation_kw)
                microgrid_data[mg_id]['generator_power_kw'].append(mg_status.generator_power_kw)
                microgrid_data[mg_id]['grid_power_kw'].append(mg_status.grid_power_kw)
                microgrid_data[mg_id]['is_islanded'].append(mg_status.is_islanded)
        
        return microgrid_data


# Example Test Run
if __name__ == "__main__":
    # Create enhanced DT manager
    dt_manager = EnhancedDigitalTwinManager(
        enable_shadow_simulation=True,
        enable_state_estimation=True
    )
    
    # Define test scenario
    evt = OutageEvent(
        event_id="TEST_OUTAGE",
        outage_type=OutageType.PARTIAL,
        start_time=datetime.now() + timedelta(hours=2),
        duration_hours=6.0,
        affected_microgrids=["industrial", "residential"],
        description="6-Hour Partial Outage Test"
    )
    
    cfg = ScenarioConfig(
        scenario_id="test_enhanced_001",
        name="Enhanced DT Test Run",
        description="Testing all new capabilities",
        start_time=datetime.now(),
        duration_hours=12,
        outage_events=[evt]
    )
    
    # Run with predictive control
    results = dt_manager.run_enhanced_simulation(cfg, use_predictive_control=True)
    
    print("\n" + "="*60)
    print("FINAL RECOMMENDATIONS")
    print("="*60)
    for rec in results['recommendations']:
        print(f"  {rec}")
    
    print("\nEnhanced Digital Twin simulation complete!")