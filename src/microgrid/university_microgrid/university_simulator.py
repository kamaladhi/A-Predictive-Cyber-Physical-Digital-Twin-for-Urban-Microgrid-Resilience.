import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
import logging

from src.microgrid.university_microgrid.Uni_parameters import MicrogridConfig
from src.microgrid.university_microgrid.university_components import Battery, PVArray, Generator, Load, ComponentState
from src.ems.university_ems import UniversityEMS as EnergyManagementSystem, OperationMode, MicrogridMeasurements
from src.ems.city_ems import SupervisoryCommand

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
        self.ems = EnergyManagementSystem(config)
        
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
        self.ems = EnergyManagementSystem(self.config)
        
        logger.info(f"Simulation reset to {start_time}")
    
    def step(self, grid_available: bool = True, 
             irradiance_w_m2: Optional[float] = None,
             ambient_temp_c: float = 25,
             supervisory_cmd: Optional[SupervisoryCommand] = None) -> Dict:
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
        base_total_load = self.load.total_load_kw
        city_shed_kw = 0.0
        city_shed_pct = 0.0

        if supervisory_cmd and supervisory_cmd.target_shed_percent:
            shed_amount = max(0.0, self.load.non_critical_load_kw * (supervisory_cmd.target_shed_percent / 100.0))
            city_shed_kw = self.load.shed_non_critical(shed_amount)
            total_load = self.load.total_load_kw
            if base_total_load > 0:
                city_shed_pct = (city_shed_kw / base_total_load) * 100.0
        
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
        
        measurements = MicrogridMeasurements(
            timestamp=self.current_time,
            grid_available=grid_available,
            grid_voltage_pu=1.0 if grid_available else 0.0,
            grid_frequency_hz=50.0 if grid_available else 49.0,
            grid_power_kw=0.0,
            battery_soc_percent=self.battery.soc_percent,
            battery_power_kw=self.battery.power_kw,
            battery_available=True,
            pv_available_power_kw=pv_power,
            pv_actual_power_kw=self.pv.power_kw,
            gen1_running=self.generator.state == ComponentState.RUNNING,
            gen1_power_kw=self.generator.power_kw,
            gen2_running=False,
            gen2_power_kw=0.0,
            total_load_demand_kw=total_load,
            critical_load_kw=critical_load,
            non_critical_load_kw=self.load.non_critical_load_kw,
            active_load_sheds={}
        )

        ems_outputs = self.ems.update(measurements)
        
        # Update energy counters
        self.load.update_energy(dt_hours)
        self.pv.update_energy(dt_hours)
        
        # Collect data point
        data_point = {
            'timestamp': self.current_time,
            'time_minutes': (self.current_time - self.simulation_data[0]['timestamp']).total_seconds() / 60 if self.simulation_data else 0,
            'city_shed_kw': city_shed_kw,
            'city_shed_percent_of_load': city_shed_pct,
            'city_battery_reserve_target': supervisory_cmd.battery_reserve_percent if supervisory_cmd else None,
            
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
            'grid_power_kw': max(0.0, total_load - (pv_power + gen_power + self.battery.power_kw)),
            
            # Battery
            'battery_power_kw': self.battery.power_kw,
            'battery_soc_percent': self.battery.soc_percent,
            'battery_energy_kwh': self.battery.energy_kwh,
            
            # System state
            'operation_mode': ems_outputs.operation_mode.name,
            'grid_available': grid_available,
            'frequency_hz': measurements.grid_frequency_hz,
            'voltage_pu': measurements.grid_voltage_pu,
            'power_balance_kw': pv_power + gen_power + self.battery.power_kw - total_load,
            
            # Events
            'islanding_event': False,
            'reconnection_event': False,
            
            # Critical load protection status
            'critical_load_shed': (self.load.shed_load_kw > self.load.non_critical_load_kw),
            
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
    
    def run_solar_scenario(self,
                           solar_provider,
                           duration_hours: float,
                           outage_start_hour: Optional[float] = None,
                           outage_duration_hours: Optional[float] = None,
                           start_time: datetime = None) -> pd.DataFrame:
        """
        Run a simulation scenario driven by real NSRDB solar irradiance data.

        Instead of falling back to the synthetic sinusoidal PV model, this
        method looks up GHI and ambient temperature from a SolarDataProvider
        at each timestep and passes them to the component-level step().

        Args:
            solar_provider: SolarDataProvider instance (from src.solar.pv_power_model)
                            holding preprocessed NSRDB irradiance + temperature.
            duration_hours: Total simulation duration in hours.
            outage_start_hour: When to start grid outage (None = no outage).
            outage_duration_hours: Duration of outage in hours.
            start_time: Simulation start time (datetime).

        Returns:
            pd.DataFrame with simulation results (same schema as run_scenario).
        """
        self.reset(start_time)

        num_steps = int(duration_hours * 3600 / self.timestep_seconds)

        logger.info(f"🎓 Running university SOLAR scenario: {duration_hours}h, "
                    f"{num_steps} steps (real irradiance data)")
        if outage_start_hour is not None:
            logger.info(f"Grid outage: {outage_start_hour}h to "
                        f"{outage_start_hour + outage_duration_hours}h")

        for step_num in range(num_steps):
            elapsed_hours = step_num * self.timestep_seconds / 3600

            # Determine grid availability
            grid_available = True
            if outage_start_hour is not None and outage_duration_hours is not None:
                if outage_start_hour <= elapsed_hours < (outage_start_hour + outage_duration_hours):
                    grid_available = False

            # Look up real irradiance + temperature for current timestamp
            irradiance, temperature = solar_provider.get_irradiance(
                self.current_time)

            # Execute step with real solar data
            self.step(
                grid_available=grid_available,
                irradiance_w_m2=irradiance,
                ambient_temp_c=temperature
            )

            # Progress logging
            if step_num % 100 == 0:
                logger.info(f"Step {step_num}/{num_steps} ({elapsed_hours:.1f}h) "
                            f"GHI={irradiance:.0f} W/m², T={temperature:.1f}°C")

        logger.info("Solar scenario complete")

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
    from src.microgrid.university_microgrid.Uni_parameters import create_default_config
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
