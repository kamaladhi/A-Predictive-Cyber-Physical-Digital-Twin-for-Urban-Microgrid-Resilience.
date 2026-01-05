import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
import logging

from Microgrid.university_microgrid.parameters import MicrogridConfig
from Microgrid.university_microgrid.microgrid_components import Battery, PVArray, Generator, Load, ComponentState
from Microgrid.university_microgrid.microgrid_ems import EnergyManagementSystem, OperationMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MicrogridSimulator:
    """
    Complete microgrid simulation engine
    Simulates physical components and control system
    """
    
    def __init__(self, config: MicrogridConfig):
        self.config = config
        
        # Initialize components
        self.battery = Battery(config.battery)
        self.pv = PVArray(config.pv)
        self.generator = Generator(config.generator)
        self.load = Load(config)
        
        # Initialize EMS
        self.ems = EnergyManagementSystem(config, self.battery, self.pv, self.generator, self.load)
        
        # Simulation state
        self.current_time = None
        self.timestep_seconds = config.control.time_resolution_minutes * 60
        self.simulation_data = []
        
        logger.info("Microgrid simulator initialized")
        logger.info(f"Time resolution: {config.control.time_resolution_minutes} minutes")
    
    def reset(self, start_time: datetime = None):
        """Reset simulation to initial state"""
        if start_time is None:
            start_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        self.current_time = start_time
        self.simulation_data = []
        
        # Reset components
        self.battery = Battery(self.config.battery)
        self.pv = PVArray(self.config.pv)
        self.generator = Generator(self.config.generator)
        self.load = Load(self.config)
        self.ems = EnergyManagementSystem(self.config, self.battery, self.pv, self.generator, self.load)
        
        logger.info(f"Simulation reset to {start_time}")
    
    def step(self, grid_available: bool = True, 
             irradiance_w_m2: Optional[float] = None,
             ambient_temp_c: float = 25) -> Dict:
        """
        Execute one simulation timestep
        
        Args:
            grid_available: Whether grid is available (for outage simulation)
            irradiance_w_m2: Solar irradiance (if None, uses model)
            ambient_temp_c: Ambient temperature
            
        Returns:
            Dictionary with current state
        """
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
        gen_demand = 0
        if self.generator.state == ComponentState.RUNNING:
            # Generator tries to meet load deficit
            power_deficit = max(0, total_load - pv_power - self.battery.get_available_discharge_power())
            gen_demand = power_deficit
        
        gen_power = self.generator.update(dt_seconds, gen_demand)
        
        # Update EMS (this coordinates everything)
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
            
            # Generation
            'pv_power_kw': self.pv.power_kw,
            'pv_curtailment_kw': self.pv.curtailment_kw,
            'generator_power_kw': self.generator.power_kw,
            'generator_state': self.generator.state.name,
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
            'generator_fuel_liters': self.generator.fuel_consumed_liters,
            'battery_throughput_kwh': self.battery.cumulative_throughput_kwh
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
        """
        Run a complete simulation scenario
        
        Args:
            duration_hours: Total simulation duration
            outage_start_hour: When to start grid outage (None = no outage)
            outage_duration_hours: Duration of outage
            start_time: Simulation start time
            
        Returns:
            DataFrame with simulation results
        """
        self.reset(start_time)
        
        num_steps = int(duration_hours * 3600 / self.timestep_seconds)
        
        logger.info(f"Running scenario: {duration_hours}h duration, {num_steps} steps")
        if outage_start_hour is not None:
            logger.info(f"Grid outage: {outage_start_hour}h to {outage_start_hour + outage_duration_hours}h")
        
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
        """
        Run all predefined test scenarios
        
        Returns:
            Dictionary mapping scenario names to result DataFrames
        """
        scenarios = {}
        
        # Scenario 1: Daytime outage (high PV)
        logger.info("\n=== SCENARIO 1: Daytime Outage (14:00) ===")
        start_time = datetime(2025, 1, 15, 0, 0, 0)
        scenarios['daytime_outage'] = self.run_scenario(
            duration_hours=24,
            outage_start_hour=14,
            outage_duration_hours=6,
            start_time=start_time
        )
        
        # Scenario 2: Night outage (no PV, worst case)
        logger.info("\n=== SCENARIO 2: Night Outage (20:00) ===")
        scenarios['night_outage'] = self.run_scenario(
            duration_hours=24,
            outage_start_hour=20,
            outage_duration_hours=8,
            start_time=start_time
        )
        
        # Scenario 3: Extended outage (24 hours)
        logger.info("\n=== SCENARIO 3: Extended Outage (24h) ===")
        scenarios['extended_outage'] = self.run_scenario(
            duration_hours=48,
            outage_start_hour=10,
            outage_duration_hours=24,
            start_time=start_time
        )
        
        # Scenario 4: Normal operation (no outage)
        logger.info("\n=== SCENARIO 4: Normal Operation ===")
        scenarios['normal_operation'] = self.run_scenario(
            duration_hours=72,
            outage_start_hour=None,
            outage_duration_hours=None,
            start_time=start_time
        )
        
        return scenarios
    
    def calculate_resilience_metrics(self, df: pd.DataFrame) -> Dict:
        """
        Calculate resilience and performance metrics
        
        Args:
            df: Simulation results DataFrame
            
        Returns:
            Dictionary of metrics
        """
        metrics = {}
        
        # Identify islanded periods
        islanded = df[df['operation_mode'] == 'ISLANDED']
        
        if len(islanded) > 0:
            # Duration metrics
            outage_duration_hours = len(islanded) * self.timestep_seconds / 3600
            metrics['outage_duration_hours'] = outage_duration_hours
            
            # Critical load service
            critical_load_energy = islanded['critical_load_kw'].sum() * self.timestep_seconds / 3600
            actual_served = (islanded['critical_load_kw'] * (1 - islanded['shed_load_kw'] / islanded['total_load_kw'])).sum() * self.timestep_seconds / 3600
            metrics['critical_load_served_percent'] = (actual_served / critical_load_energy * 100) if critical_load_energy > 0 else 0
            
            # Shed events
            shed_events = (islanded['shed_load_kw'] > 0).sum()
            metrics['load_shed_events'] = shed_events
            metrics['max_shed_load_kw'] = islanded['shed_load_kw'].max()
            metrics['avg_shed_load_kw'] = islanded['shed_load_kw'].mean()
            
            # Battery performance
            metrics['min_battery_soc_percent'] = islanded['battery_soc_percent'].min()
            metrics['battery_cycles'] = islanded['battery_throughput_kwh'].iloc[-1] / self.config.battery.nominal_capacity_kwh / 2
            
            # Generator usage
            gen_running = islanded[islanded['generator_state'] == 'RUNNING']
            if len(gen_running) > 0:
                metrics['generator_runtime_hours'] = len(gen_running) * self.timestep_seconds / 3600
                metrics['generator_fuel_consumed_liters'] = gen_running['generator_fuel_liters'].iloc[-1] - islanded['generator_fuel_liters'].iloc[0]
            else:
                metrics['generator_runtime_hours'] = 0
                metrics['generator_fuel_consumed_liters'] = 0
            
            # Survival time
            if metrics['min_battery_soc_percent'] <= self.config.battery.min_soc_percent + 5:
                # Battery nearly depleted
                metrics['survival_time_hours'] = outage_duration_hours
                metrics['survived_full_outage'] = False
            else:
                metrics['survival_time_hours'] = outage_duration_hours
                metrics['survived_full_outage'] = True
        
        # Overall energy metrics
        metrics['total_load_energy_kwh'] = df['total_load_kw'].sum() * self.timestep_seconds / 3600
        metrics['total_pv_energy_kwh'] = df['pv_power_kw'].sum() * self.timestep_seconds / 3600
        metrics['total_grid_energy_kwh'] = df['grid_power_kw'].sum() * self.timestep_seconds / 3600
        metrics['pv_penetration_percent'] = (metrics['total_pv_energy_kwh'] / metrics['total_load_energy_kwh'] * 100) if metrics['total_load_energy_kwh'] > 0 else 0
        
        return metrics
    
    def export_results(self, df: pd.DataFrame, filename: str):
        """Export simulation results to CSV"""
        df.to_csv(filename, index=False)
        logger.info(f"Results exported to {filename}")
    
    def export_metrics(self, metrics: dict, filename: str):
        """Export metrics to JSON (safe for numpy types)"""
        def convert(obj):
            if hasattr(obj, "item"):
                return obj.item()   # converts numpy scalars
            return obj

        safe_metrics = {k: convert(v) for k, v in metrics.items()}

        with open(filename, 'w') as f:
            json.dump(safe_metrics, f, indent=2)



def main():
    """Main simulation entry point"""
    # Create configuration
    from Microgrid.university_microgrid.parameters import create_default_config
    config = create_default_config()
    
    # Create simulator
    sim = MicrogridSimulator(config)
    
    # Run all scenarios
    scenarios = sim.run_predefined_scenarios()
    
    # Calculate and export metrics for each scenario
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
    
    logger.info("\n=== Simulation Complete ===")
    logger.info("Results saved to CSV files")
    logger.info("Metrics saved to JSON file")


if __name__ == "__main__":
    main()