import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
import logging
import sys
import os

# Add project root to path for absolute imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from Microgrid.residence.residence_parameters import MicrogridConfig
from Microgrid.residence.residence_components import Battery, PVArray, Generator, Load, ComponentState
from Microgrid.residence.residence_ems import EnergyManagementSystem, OperationMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MicrogridSimulator:
    """
    Complete residential microgrid simulation engine
    Reality: Residents WILL experience discomfort during outages
    """
    
    def __init__(self, config: MicrogridConfig):
        self.config = config
        
        # Initialize components
        self.battery = Battery(config.battery)
        self.pv = PVArray(config.pv)
        self.generator1 = Generator(config.generator, gen_id=1)  # Single generator
        self.generator2 = None  # No Gen2 for residential
        self.load = Load(config)
        
        # Initialize EMS
        self.ems = EnergyManagementSystem(config, self.battery, self.pv, 
                                         self.generator1, None, self.load)
        
        # Simulation state
        self.current_time = None
        self.timestep_seconds = config.control.time_resolution_minutes * 60
        self.simulation_data = []
        
        logger.info("🏘️ Residential microgrid simulator initialized")
        logger.info(f"Time resolution: {config.control.time_resolution_minutes} minutes")
        logger.info(f"Critical loads: {config.load_profile.total_critical_load} kW (Safety only)")
        logger.info(f"⚠️ Residents will experience service degradation during outages")
    
    def reset(self, start_time: datetime = None):
        """Reset simulation to initial state"""
        if start_time is None:
            start_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        self.current_time = start_time
        self.simulation_data = []
        
        # Reset components
        self.battery = Battery(self.config.battery)
        self.pv = PVArray(self.config.pv)
        self.generator1 = Generator(self.config.generator, gen_id=1)
        self.load = Load(self.config)
        self.ems = EnergyManagementSystem(self.config, self.battery, self.pv, 
                                         self.generator1, None, self.load)
        
        logger.info(f"Simulation reset to {start_time}")
    
    def step(self, grid_available: bool = True, 
             irradiance_w_m2: Optional[float] = None,
             ambient_temp_c: float = 30) -> Dict:
        """Execute one simulation timestep"""
        dt_seconds = self.timestep_seconds
        dt_hours = dt_seconds / 3600
        
        # Update load
        total_load, critical_load = self.load.update_load(self.current_time)
        
        # Update PV generation
        pv_power = self.pv.calculate_generation(
            self.current_time, 
            irradiance_w_m2, 
            ambient_temp_c
        )
        
        # Update generator
        gen1_demand = 0
        
        if self.generator1.state == ComponentState.RUNNING:
            # Generator serves deficit
            power_deficit = max(0, total_load - pv_power - self.battery.power_kw)
            gen1_demand = min(power_deficit, self.config.generator.rated_power_kw)
        
        gen1_power = self.generator1.update(dt_seconds, gen1_demand)
        
        # Update EMS
        ems_state = self.ems.update(
            self.current_time,
            dt_seconds,
            simulated_grid_available=grid_available
        )
        
        # Update energy counters
        self.load.update_energy(dt_hours)
        self.pv.update_energy(dt_hours)
        
        # Collect data point
        data_point = {
            'timestamp': self.current_time,
            'time_minutes': (self.current_time - self.simulation_data[0]['timestamp']).total_seconds() / 60 if self.simulation_data else 0,
            
            # Load
            'total_load_kw': self.load.total_load_kw,
            'critical_load_kw': self.load.critical_load_kw,
            'non_critical_load_kw': self.load.non_critical_load_kw,
            'shed_load_kw': self.load.shed_load_kw,
            
            # Tier loads (residential-specific)
            'ev_charging_kw': self.load.tier_loads['EV_CHARGING'],
            'ac_load_kw': self.load.tier_loads['AIR_CONDITIONING'],
            'washing_load_kw': self.load.tier_loads['WASHING_MACHINES'],
            'lighting_load_kw': self.load.tier_loads['COMMON_LIGHTING'],
            
            # Generation
            'pv_power_kw': self.pv.power_kw,
            'pv_curtailment_kw': self.pv.curtailment_kw,
            'gen1_power_kw': self.generator1.power_kw,
            'gen1_state': self.generator1.state.name,
            'grid_power_kw': self.ems.grid_power_kw,
            
            # Battery
            'battery_power_kw': self.battery.power_kw,
            'battery_soc_percent': self.battery.soc_percent,
            'battery_energy_kwh': self.battery.energy_kwh,
            
            # System state
            'operation_mode': ems_state.mode.name,
            'grid_available': ems_state.grid_available,
            'frequency_hz': ems_state.frequency_hz,
            'voltage_pu': ems_state.voltage_pu,
            'power_balance_kw': ems_state.power_balance_kw,
            
            # Events
            'islanding_event': ems_state.islanding_event,
            'reconnection_event': ems_state.reconnection_event,
            
            # Cumulative
            'load_energy_kwh': self.load.cumulative_energy_kwh,
            'pv_energy_kwh': self.pv.cumulative_energy_kwh,
            'gen1_fuel_liters': self.generator1.fuel_consumed_liters,
            'battery_throughput_kwh': self.battery.cumulative_throughput_kwh,
            
            # Discomfort metrics
            'ac_shed_minutes': self.load.ac_shed_minutes,
            'ev_shed_minutes': self.load.ev_shed_minutes
        }
        
        self.simulation_data.append(data_point)
        
        # Advance time
        self.current_time += timedelta(seconds=dt_seconds)
        
        return data_point
    
    def run_scenario(self, 
                    duration_hours: float,
                    outage_start_hour: Optional[float] = None,
                    outage_duration_hours: Optional[float] = None,
                    start_time: datetime = None) -> pd.DataFrame:
        """Run a complete simulation scenario"""
        self.reset(start_time)
        
        num_steps = int(duration_hours * 3600 / self.timestep_seconds)
        
        logger.info(f"🏘️ Running residential scenario: {duration_hours}h duration, {num_steps} steps")
        if outage_start_hour is not None:
            logger.info(f"Grid outage: {outage_start_hour}h to {outage_start_hour + outage_duration_hours}h")
            logger.info("⚠️ Expect significant resident discomfort")
        
        for step_num in range(num_steps):
            elapsed_hours = step_num * self.timestep_seconds / 3600
            
            # Determine grid availability
            grid_available = True
            if outage_start_hour is not None and outage_duration_hours is not None:
                if outage_start_hour <= elapsed_hours < (outage_start_hour + outage_duration_hours):
                    grid_available = False
            
            # Execute step
            self.step(grid_available=grid_available)
            
            # Progress logging
            if step_num % 100 == 0:
                logger.info(f"Step {step_num}/{num_steps} ({elapsed_hours:.1f}h)")
        
        logger.info("Scenario complete")
        
        # Convert to DataFrame
        df = pd.DataFrame(self.simulation_data)
        return df
    
    def run_predefined_scenarios(self) -> Dict[str, pd.DataFrame]:
        """Run all predefined residential test scenarios"""
        scenarios = {}
        
        # Scenario 1: Night outage (worst case - no PV, peak AC usage ending)
        logger.info("\n=== RESIDENTIAL SCENARIO 1: Night Outage (22:00) ===")
        start_time = datetime(2025, 1, 15, 0, 0, 0)
        scenarios['night_outage'] = self.run_scenario(
            duration_hours=24,
            outage_start_hour=22,
            outage_duration_hours=8,
            start_time=start_time
        )
        
        # Scenario 2: Evening peak outage (WORST - AC + cooking + EV)
        logger.info("\n=== RESIDENTIAL SCENARIO 2: Evening Peak Outage (18:00) ===")
        scenarios['evening_peak_outage'] = self.run_scenario(
            duration_hours=24,
            outage_start_hour=18,
            outage_duration_hours=5,
            start_time=start_time
        )
        
        # Scenario 3: Extended outage (12+ hours, severe discomfort)
        logger.info("\n=== RESIDENTIAL SCENARIO 3: Extended Outage (12h) ===")
        scenarios['extended_outage'] = self.run_scenario(
            duration_hours=36,
            outage_start_hour=14,
            outage_duration_hours=12,
            start_time=start_time
        )
        
        # Scenario 4: Morning peak outage
        logger.info("\n=== RESIDENTIAL SCENARIO 4: Morning Peak Outage (07:00) ===")
        scenarios['morning_peak_outage'] = self.run_scenario(
            duration_hours=24,
            outage_start_hour=7,
            outage_duration_hours=4,
            start_time=start_time
        )
        
        # Scenario 5: Normal operation
        logger.info("\n=== RESIDENTIAL SCENARIO 5: Normal Operation ===")
        scenarios['normal_operation'] = self.run_scenario(
            duration_hours=72,
            outage_start_hour=None,
            outage_duration_hours=None,
            start_time=start_time
        )
        
        return scenarios
    
    def calculate_resilience_metrics(self, df: pd.DataFrame) -> Dict:
        """Calculate residential-specific resilience metrics"""
        metrics = {}
        
        # Identify islanded periods
        islanded = df[df['operation_mode'] == 'ISLANDED']
        
        if len(islanded) > 0:
            # Duration metrics
            outage_duration_hours = len(islanded) * self.timestep_seconds / 3600
            metrics['outage_duration_hours'] = outage_duration_hours
            
            # CRITICAL: Did we protect safety loads?
            critical_served_pct = 100.0  # Should always serve critical
            if islanded['shed_load_kw'].max() >= self.config.load_profile.total_non_critical_load:
                # This means critical loads were affected
                critical_served_pct = 95.0
            
            metrics['critical_load_served_percent'] = critical_served_pct
            
            # Load shedding metrics
            metrics['load_shed_events'] = (islanded['shed_load_kw'] > 0).sum()
            metrics['max_shed_load_kw'] = islanded['shed_load_kw'].max()
            metrics['avg_shed_load_kw'] = islanded['shed_load_kw'].mean()
            metrics['total_shed_energy_kwh'] = (islanded['shed_load_kw'] * self.timestep_seconds / 3600).sum()
            
            # Resident discomfort metrics (UNIQUE to residential)
            metrics['ac_outage_minutes'] = islanded['ac_shed_minutes'].max()
            metrics['ev_charging_blocked_minutes'] = islanded['ev_shed_minutes'].max()
            metrics['percent_time_ac_unavailable'] = (islanded['ac_shed_minutes'].max() / (outage_duration_hours * 60) * 100) if outage_duration_hours > 0 else 0
            metrics['percent_time_ev_blocked'] = (islanded['ev_shed_minutes'].max() / (outage_duration_hours * 60) * 100) if outage_duration_hours > 0 else 0
            
            # Battery performance
            metrics['min_battery_soc_percent'] = islanded['battery_soc_percent'].min()
            metrics['battery_cycles'] = islanded['battery_throughput_kwh'].iloc[-1] / self.config.battery.nominal_capacity_kwh / 2
            
            # Generator usage
            gen1_running = islanded[islanded['gen1_state'] == 'RUNNING']
            
            if len(gen1_running) > 0:
                metrics['gen1_runtime_hours'] = len(gen1_running) * self.timestep_seconds / 3600
                metrics['gen1_fuel_consumed_liters'] = gen1_running['gen1_fuel_liters'].iloc[-1] - islanded['gen1_fuel_liters'].iloc[0]
                metrics['generator_used'] = True
            else:
                metrics['gen1_runtime_hours'] = 0
                metrics['gen1_fuel_consumed_liters'] = 0
                metrics['generator_used'] = False
            
            # Survival assessment
            if metrics['min_battery_soc_percent'] <= self.config.battery.min_soc_percent + 5:
                metrics['survived_full_outage'] = False
                metrics['survival_time_hours'] = outage_duration_hours
            else:
                metrics['survived_full_outage'] = True
                metrics['survival_time_hours'] = outage_duration_hours
            
            # Comfort degradation score (0-100, lower is worse)
            ac_penalty = metrics['percent_time_ac_unavailable'] * 0.5
            ev_penalty = metrics['percent_time_ev_blocked'] * 0.3
            shed_penalty = (metrics['avg_shed_load_kw'] / self.config.load_profile.total_non_critical_load) * 20
            metrics['comfort_score'] = max(0, 100 - ac_penalty - ev_penalty - shed_penalty)
        
        # Overall energy metrics
        metrics['total_load_energy_kwh'] = df['total_load_kw'].sum() * self.timestep_seconds / 3600
        metrics['total_pv_energy_kwh'] = df['pv_power_kw'].sum() * self.timestep_seconds / 3600
        metrics['total_grid_energy_kwh'] = df['grid_power_kw'].sum() * self.timestep_seconds / 3600
        metrics['pv_penetration_percent'] = (metrics['total_pv_energy_kwh'] / metrics['total_load_energy_kwh'] * 100) if metrics['total_load_energy_kwh'] > 0 else 0
        
        # Residential-specific KPIs
        metrics['critical_load_protected'] = (df['shed_load_kw'].max() < self.config.load_profile.total_non_critical_load)
        metrics['max_non_critical_shed_percent'] = (df['shed_load_kw'].max() / self.config.load_profile.total_non_critical_load * 100) if self.config.load_profile.total_non_critical_load > 0 else 0
        
        return metrics
    
    def export_results(self, df: pd.DataFrame, filename: str):
        """Export simulation results to CSV"""
        df.to_csv(filename, index=False)
        logger.info(f"Results exported to {filename}")
    
    def export_metrics(self, metrics: dict, filename: str):
        """Export metrics to JSON"""
        def convert(obj):
            if hasattr(obj, "item"):
                return obj.item()
            return obj
        
        safe_metrics = {k: convert(v) for k, v in metrics.items()}
        
        with open(filename, 'w') as f:
            json.dump(safe_metrics, f, indent=2)
        
        logger.info(f"Metrics exported to {filename}")


def main():
    """Main simulation entry point"""
    from residence_parameters import create_default_config
    config = create_default_config()
    
    # Create simulator
    sim = MicrogridSimulator(config)
    
    # Run all scenarios
    scenarios = sim.run_predefined_scenarios()
    
    # Calculate and export metrics
    all_metrics = {}
    for scenario_name, df in scenarios.items():
        logger.info(f"\n=== Metrics for {scenario_name} ===")
        metrics = sim.calculate_resilience_metrics(df)
        all_metrics[scenario_name] = metrics
        
        # Print key metrics
        for key, value in metrics.items():
            if isinstance(value, float):
                logger.info(f"  {key}: {value:.2f}")
            else:
                logger.info(f"  {key}: {value}")
        
        # Export data
        sim.export_results(df, f'results_{scenario_name}.csv')
    
    # Export all metrics
    sim.export_metrics(all_metrics, 'all_scenario_metrics.json')
    
    logger.info("\n=== RESIDENTIAL SIMULATION COMPLETE ===")
    logger.info("🏘️ Resident discomfort quantified")
    logger.info("Results saved to CSV files")
    logger.info("Metrics saved to JSON file")


if __name__ == "__main__":
    main()