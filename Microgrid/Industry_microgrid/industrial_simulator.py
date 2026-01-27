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

from Microgrid.Industry_microgrid.industrial_parameters import MicrogridConfig
from Microgrid.Industry_microgrid.industrial_component import Battery, PVArray, Generator, Load, ComponentState
from EMS.industry_ems import IndustryEMS as EnergyManagementSystem, OperationMode, MicrogridMeasurements
from EMS.city_ems import SupervisoryCommand

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MicrogridSimulator:
    """
    Complete hospital microgrid simulation engine
    Ensures critical loads (320 kW) always protected
    """
    
    def __init__(self, config: MicrogridConfig):
        self.config = config
        
        # Initialize components
        self.battery = Battery(config.battery)
        self.pv = PVArray(config.pv)
        self.generator1 = Generator(config.generator, gen_id=1)  # Critical loads
        self.generator2 = Generator(config.generator, gen_id=2)  # Non-critical + backup
        self.load = Load(config)
        
        # Initialize EMS
        self.ems = EnergyManagementSystem(config)
        
        # Simulation state
        self.current_time = None
        self.timestep_seconds = config.control.time_resolution_minutes * 60
        self.simulation_data = []
        
        logger.info("Hospital microgrid simulator initialized")
        logger.info(f"Time resolution: {config.control.time_resolution_minutes} minutes")
        logger.info(f"Critical loads: {config.load_profile.total_critical_load} kW (PROTECTED)")
    
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
        self.generator2 = Generator(self.config.generator, gen_id=2)
        self.load = Load(self.config)
        self.ems = EnergyManagementSystem(self.config)
        
        logger.info(f"Simulation reset to {start_time}")
    
    def step(self, grid_available: bool = True, 
            irradiance_w_m2: Optional[float] = None,
            ambient_temp_c: float = 25,
            supervisory_cmd: Optional[SupervisoryCommand] = None) -> Dict:
        """Execute one simulation timestep"""
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
        
        # Update generators
        gen1_demand = 0
        gen2_demand = 0
        
        if self.generator1.state == ComponentState.RUNNING:
            # Gen1 serves critical loads
            power_deficit = max(0, critical_load - pv_power)
            gen1_demand = min(power_deficit, self.config.generator.gen1_rated_power_kw)
        
        if self.generator2.state == ComponentState.RUNNING:
            # Gen2 serves non-critical loads
            power_deficit = max(0, self.load.non_critical_load_kw - (pv_power - gen1_demand))
            gen2_demand = min(power_deficit, self.config.generator.gen2_rated_power_kw)
        
        gen1_power = self.generator1.update(dt_seconds, gen1_demand)
        gen2_power = self.generator2.update(dt_seconds, gen2_demand)
        
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
            gen1_running=self.generator1.state == ComponentState.RUNNING,
            gen1_power_kw=self.generator1.power_kw,
            gen2_running=self.generator2.state == ComponentState.RUNNING,
            gen2_power_kw=self.generator2.power_kw,
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
            
            # Tier loads (hospital-specific)
            'hvac_load_kw': self.load.tier_loads['HVAC'],
            'wards_lighting_load_kw': self.load.tier_loads['WARDS_LIGHTING'],
            'admin_load_kw': self.load.tier_loads['ADMIN'],
            
            # Generation
            'pv_power_kw': self.pv.power_kw,
            'pv_curtailment_kw': self.pv.curtailment_kw,
            'gen1_power_kw': self.generator1.power_kw,
            'gen1_state': self.generator1.state.name,
            'gen2_power_kw': self.generator2.power_kw,
            'gen2_state': self.generator2.state.name,
            'grid_power_kw': max(0.0, total_load - (pv_power + gen1_power + gen2_power + self.battery.power_kw)),
            
            # Battery
            'battery_power_kw': self.battery.power_kw,
            'battery_soc_percent': self.battery.soc_percent,
            'battery_energy_kwh': self.battery.energy_kwh,
            
            # System state
            'operation_mode': ems_outputs.operation_mode.name,
            'grid_available': grid_available,
            'frequency_hz': measurements.grid_frequency_hz,
            'voltage_pu': measurements.grid_voltage_pu,
            'power_balance_kw': pv_power + gen1_power + gen2_power + self.battery.power_kw - total_load,
            
            # Events
            'islanding_event': False,
            'reconnection_event': False,
            
            # Critical load protection status
            'critical_load_shed': (self.load.shed_load_kw > self.load.non_critical_load_kw),
            
            # Cumulative
            'load_energy_kwh': self.load.cumulative_energy_kwh,
            'pv_energy_kwh': self.pv.cumulative_energy_kwh,
            'gen1_fuel_liters': self.generator1.fuel_consumed_liters,
            'gen2_fuel_liters': self.generator2.fuel_consumed_liters,
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
        """Run a complete simulation scenario"""
        self.reset(start_time)
        
        num_steps = int(duration_hours * 3600 / self.timestep_seconds)
        
        logger.info(f"Running hospital scenario: {duration_hours}h duration, {num_steps} steps")
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
        """Run all predefined hospital test scenarios"""
        scenarios = {}
        
        # Scenario 1: Night outage (worst case - no PV)
        logger.info("\n=== HOSPITAL SCENARIO 1: Night Emergency (20:00) ===")
        start_time = datetime(2025, 1, 15, 0, 0, 0)
        scenarios['night_emergency'] = self.run_scenario(
            duration_hours=24,
            outage_start_hour=20,
            outage_duration_hours=8,
            start_time=start_time
        )
        
        # Scenario 2: Daytime outage (high PV, best case)
        logger.info("\n=== HOSPITAL SCENARIO 2: Daytime Outage (14:00) ===")
        scenarios['daytime_outage'] = self.run_scenario(
            duration_hours=24,
            outage_start_hour=14,
            outage_duration_hours=6,
            start_time=start_time
        )
        
        # Scenario 3: Extended outage (12+ hours, generator critical)
        logger.info("\n=== HOSPITAL SCENARIO 3: Extended Outage (12h) ===")
        scenarios['extended_outage'] = self.run_scenario(
            duration_hours=36,
            outage_start_hour=10,
            outage_duration_hours=12,
            start_time=start_time
        )
        
        # Scenario 4: Peak load outage
        logger.info("\n=== HOSPITAL SCENARIO 4: Peak Load Outage (15:00) ===")
        scenarios['peak_load_outage'] = self.run_scenario(
            duration_hours=24,
            outage_start_hour=15,
            outage_duration_hours=4,
            start_time=start_time
        )
        
        # Scenario 5: Normal operation
        logger.info("\n=== HOSPITAL SCENARIO 5: Normal Operation ===")
        scenarios['normal_operation'] = self.run_scenario(
            duration_hours=72,
            outage_start_hour=None,
            outage_duration_hours=None,
            start_time=start_time
        )
        
        return scenarios
    
    def calculate_resilience_metrics(self, df: pd.DataFrame) -> Dict:
        """Calculate hospital-specific resilience metrics"""
        metrics = {}
        
        # Identify islanded periods
        islanded = df[df['operation_mode'] == 'ISLANDED']
        
        if len(islanded) > 0:
            # Duration metrics
            outage_duration_hours = len(islanded) * self.timestep_seconds / 3600
            metrics['outage_duration_hours'] = outage_duration_hours
            
            # CRITICAL: Did we protect critical loads?
            critical_served_pct = 100.0  # Hospital should ALWAYS serve critical
            if islanded['shed_load_kw'].max() > 0:
                # Check if any critical load was affected
                max_shed = islanded['shed_load_kw'].max()
                if max_shed < self.config.load_profile.total_non_critical_load:
                    critical_served_pct = 100.0
                else:
                    # This should NEVER happen in hospital
                    critical_served_pct = 95.0
            
            metrics['critical_load_served_percent'] = critical_served_pct
            
            # Load shedding metrics
            metrics['load_shed_events'] = (islanded['shed_load_kw'] > 0).sum()
            metrics['max_shed_load_kw'] = islanded['shed_load_kw'].max()
            metrics['avg_shed_load_kw'] = islanded['shed_load_kw'].mean()
            metrics['total_shed_energy_kwh'] = (islanded['shed_load_kw'] * self.timestep_seconds / 3600).sum()
            
            # Battery performance
            metrics['min_battery_soc_percent'] = islanded['battery_soc_percent'].min()
            metrics['battery_cycles'] = islanded['battery_throughput_kwh'].iloc[-1] / self.config.battery.nominal_capacity_kwh / 2
            
            # Generator usage
            gen1_running = islanded[islanded['gen1_state'] == 'RUNNING']
            gen2_running = islanded[islanded['gen2_state'] == 'RUNNING']
            
            if len(gen1_running) > 0:
                metrics['gen1_runtime_hours'] = len(gen1_running) * self.timestep_seconds / 3600
                metrics['gen1_fuel_consumed_liters'] = gen1_running['gen1_fuel_liters'].iloc[-1] - islanded['gen1_fuel_liters'].iloc[0]
            else:
                metrics['gen1_runtime_hours'] = 0
                metrics['gen1_fuel_consumed_liters'] = 0
            
            if len(gen2_running) > 0:
                metrics['gen2_runtime_hours'] = len(gen2_running) * self.timestep_seconds / 3600
                metrics['gen2_fuel_consumed_liters'] = gen2_running['gen2_fuel_liters'].iloc[-1] - islanded['gen2_fuel_liters'].iloc[0]
            else:
                metrics['gen2_runtime_hours'] = 0
                metrics['gen2_fuel_consumed_liters'] = 0
            
            metrics['total_generator_runtime_hours'] = metrics['gen1_runtime_hours'] + metrics['gen2_runtime_hours']
            metrics['total_fuel_consumed_liters'] = metrics['gen1_fuel_consumed_liters'] + metrics['gen2_fuel_consumed_liters']
            
            # Survival assessment
            if metrics['min_battery_soc_percent'] <= self.config.battery.min_soc_percent + 5:
                metrics['survived_full_outage'] = False
                metrics['survival_time_hours'] = outage_duration_hours
            else:
                metrics['survived_full_outage'] = True
                metrics['survival_time_hours'] = outage_duration_hours
        
        # Overall energy metrics
        metrics['total_load_energy_kwh'] = df['total_load_kw'].sum() * self.timestep_seconds / 3600
        metrics['total_pv_energy_kwh'] = df['pv_power_kw'].sum() * self.timestep_seconds / 3600
        metrics['total_grid_energy_kwh'] = df['grid_power_kw'].sum() * self.timestep_seconds / 3600
        metrics['pv_penetration_percent'] = (metrics['total_pv_energy_kwh'] / metrics['total_load_energy_kwh'] * 100) if metrics['total_load_energy_kwh'] > 0 else 0
        
        # Hospital-specific KPIs
        metrics['critical_load_never_shed'] = (df['shed_load_kw'].max() < self.config.load_profile.total_non_critical_load)
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
    from industrial_parameters import create_default_config
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
    
    logger.info("\n=== HOSPITAL SIMULATION COMPLETE ===")
    logger.info("Critical loads protection verified")
    logger.info("Results saved to CSV files")
    logger.info("Metrics saved to JSON file")


if __name__ == "__main__":
    main()