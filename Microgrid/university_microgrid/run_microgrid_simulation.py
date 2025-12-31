import argparse
import logging
from datetime import datetime
import json
import sys
import numpy as np
import os

from parameters import MicrogridConfig, create_default_config
from microgrid_simulator import MicrogridSimulator
from microgrid_visualizer import visualize_all_scenarios

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def to_python(obj):
    if isinstance(obj, dict):
        return {k: to_python(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [to_python(v) for v in obj]
    elif isinstance(obj, np.generic):
        return obj.item()
    else:
        return obj

def run_single_scenario(config: MicrogridConfig, 
                       scenario_name: str,
                       duration_hours: float,
                       outage_start: float = None,
                       outage_duration: float = None,
                       output_dir: str = '.'):
    """Run a single scenario simulation"""
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Running Scenario: {scenario_name}")
    logger.info(f"{'='*60}")
    
    # Create simulator
    sim = MicrogridSimulator(config)
    
    # Run scenario
    start_time = datetime(2025, 1, 15, 0, 0, 0)
    df = sim.run_scenario(
        duration_hours=duration_hours,
        outage_start_hour=outage_start,
        outage_duration_hours=outage_duration,
        start_time=start_time
    )
    
    # Calculate metrics
    metrics = sim.calculate_resilience_metrics(df)
    
    # Print summary
    logger.info(f"\nScenario Summary:")
    logger.info(f"  Duration: {duration_hours} hours")
    if outage_start is not None:
        logger.info(f"  Outage: {outage_start}h - {outage_start + outage_duration}h")
    logger.info(f"  Total Load: {metrics['total_load_energy_kwh']:.1f} kWh")
    logger.info(f"  PV Generation: {metrics['total_pv_energy_kwh']:.1f} kWh")
    logger.info(f"  PV Penetration: {metrics['pv_penetration_percent']:.1f}%")
    
    if 'outage_duration_hours' in metrics:
        logger.info(f"\nResilience Metrics:")
        logger.info(f"  Critical Load Served: {metrics['critical_load_served_percent']:.1f}%")
        logger.info(f"  Min Battery SoC: {metrics['min_battery_soc_percent']:.1f}%")
        logger.info(f"  Load Shed Events: {metrics['load_shed_events']}")
        logger.info(f"  Generator Runtime: {metrics['generator_runtime_hours']:.1f} hours")
        logger.info(f"  Fuel Consumed: {metrics['generator_fuel_consumed_liters']:.1f} liters")
        logger.info(f"  Survived Full Outage: {metrics['survived_full_outage']}")

    # Prepare output folders for this scenario (always create)
    scenario_dir = os.path.join(output_dir, scenario_name)
    csv_dir = os.path.join(scenario_dir, 'csv')
    metrics_dir = os.path.join(scenario_dir, 'metrics')
    png_dir = os.path.join(scenario_dir, 'png')
    pdf_dir = os.path.join(scenario_dir, 'pdf')

    for d in (csv_dir, metrics_dir, png_dir, pdf_dir):
        os.makedirs(d, exist_ok=True)

    # Export results into scenario folders
    csv_path = os.path.join(csv_dir, f"results_{scenario_name}.csv")
    metrics_path = os.path.join(metrics_dir, f"{scenario_name}_metrics.json")
    sim.export_results(df, csv_path)
    sim.export_metrics(metrics, metrics_path)
    
    return df, metrics


def run_all_scenarios(config: MicrogridConfig, output_dir: str = '.'):
    """Run all predefined scenarios"""
    
    scenarios = {
        'daytime_outage': {
            'duration_hours': 24,
            'outage_start': 14,
            'outage_duration': 6,
            'description': 'Daytime outage with high solar generation'
        },
        'night_outage': {
            'duration_hours': 24,
            'outage_start': 20,
            'outage_duration': 8,
            'description': 'Night outage, battery-only (worst case)'
        },
        'extended_outage': {
            'duration_hours': 48,
            'outage_start': 10,
            'outage_duration': 24,
            'description': 'Extended 24-hour outage requiring generator'
        },
        'normal_operation': {
            'duration_hours': 72,
            'outage_start': None,
            'outage_duration': None,
            'description': 'Normal grid-connected operation'
        },
        'peak_load_outage': {
            'duration_hours': 24,
            'outage_start': 13,
            'outage_duration': 4,
            'description': 'Outage during peak load period'
        }
    }
    
    all_results = {}
    all_metrics = {}
    
    for scenario_name, params in scenarios.items():
        logger.info(f"\n{params['description']}")
        
        df, metrics = run_single_scenario(
            config,
            scenario_name,
            params['duration_hours'],
            params['outage_start'],
            params['outage_duration'],
            output_dir=output_dir
        )
        
        all_results[scenario_name] = df
        all_metrics[scenario_name] = metrics
    
    # Save combined metrics at output root
    os.makedirs(output_dir, exist_ok=True)
    all_metrics_path = os.path.join(output_dir, 'all_scenario_metrics.json')
    with open(all_metrics_path, 'w') as f:
        json.dump(to_python(all_metrics), f, indent=2)
    
    logger.info(f"\n{'='*60}")
    logger.info("All scenarios complete!")
    logger.info(f"{'='*60}")
    
    return all_results, all_metrics


def run_custom_scenario(config: MicrogridConfig, args):
    """Run custom user-defined scenario"""
    
    logger.info("\nRunning custom scenario...")
    
    df, metrics = run_single_scenario(
        config,
        args.name,
        args.duration,
        args.outage_start,
        args.outage_duration
    )
    
    return {args.name: df}, {args.name: metrics}


def main():
    """Main entry point"""
    
    parser = argparse.ArgumentParser(
        description='Amrita University Microgrid Simulator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all predefined scenarios
  python run_microgrid_simulation.py --all
  
  # Run single predefined scenario
  python run_microgrid_simulation.py --scenario night_outage
  
  # Run custom scenario
  python run_microgrid_simulation.py --custom --name my_test --duration 48 --outage-start 15 --outage-duration 12
  
  # Use custom configuration
  python run_microgrid_simulation.py --all --config my_config.json
  
  # Skip visualization
  python run_microgrid_simulation.py --all --no-viz
        """
    )
    
    # Mode selection
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument('--all', action='store_true',
                           help='Run all predefined scenarios')
    mode_group.add_argument('--scenario', type=str,
                           choices=['daytime_outage', 'night_outage', 'extended_outage', 
                                   'normal_operation', 'peak_load_outage'],
                           help='Run single predefined scenario')
    mode_group.add_argument('--custom', action='store_true',
                           help='Run custom scenario')
    
    # Custom scenario parameters
    parser.add_argument('--name', type=str, default='custom',
                       help='Custom scenario name')
    parser.add_argument('--duration', type=float, default=24,
                       help='Simulation duration in hours')
    parser.add_argument('--outage-start', type=float, default=None,
                       help='Outage start time in hours')
    parser.add_argument('--outage-duration', type=float, default=None,
                       help='Outage duration in hours')
    
    # Configuration
    parser.add_argument('--config', type=str, default=None,
                       help='Path to custom configuration JSON file')
    
    # Output options
    parser.add_argument('--no-viz', action='store_true',
                       help='Skip visualization generation')
    parser.add_argument('--output-dir', type=str, default='.',
                       help='Output directory for results')
    
    args = parser.parse_args()
    
    # Load configuration
    if args.config:
        logger.info(f"Loading configuration from: {args.config}")
        config = MicrogridConfig.from_json(args.config)
    else:
        logger.info("Using default configuration")
        config = create_default_config()
    
    # Validate configuration
    warnings = config.validate()
    if warnings:
        logger.warning("Configuration warnings:")
        for w in warnings:
            logger.warning(f"  {w}")
    
    # Print configuration summary
    logger.info("\n" + "="*60)
    logger.info("Microgrid Configuration Summary")
    logger.info("="*60)
    logger.info(f"Campus: {config.campus_name}")
    logger.info(f"Peak Load: {config.load_profile.peak_load} kW")
    logger.info(f"Critical Load: {config.load_profile.total_critical_load} kW")
    logger.info(f"Battery: {config.battery.usable_capacity_kwh} kWh / {config.battery.max_discharge_power_kw} kW")
    logger.info(f"Solar PV: {config.pv.installed_capacity_kwp} kWp")
    logger.info(f"Generator: {config.generator.rated_power_kw} kW")
    logger.info(f"Backup Duration: {config.control.backup_duration_hours} hours")
    logger.info("="*60 + "\n")
    
    # Run simulation
    try:
        if args.all:
            results_dict, metrics_dict = run_all_scenarios(config, output_dir=args.output_dir)
        elif args.scenario:
            scenario_params = {
                'daytime_outage': (24, 14, 6),
                'night_outage': (24, 20, 8),
                'extended_outage': (48, 10, 24),
                'normal_operation': (72, None, None),
                'peak_load_outage': (24, 13, 4)
            }
            duration, start, dur = scenario_params[args.scenario]
            df, metrics = run_single_scenario(config, args.scenario, duration, start, dur, output_dir=args.output_dir)
            results_dict = {args.scenario: df}
            metrics_dict = {args.scenario: metrics}
        else:  # custom
            results_dict, metrics_dict = run_custom_scenario(config, args)
        
        # Generate visualizations
        if not args.no_viz:
            logger.info("\nGenerating visualizations...")
            try:
                try:
                    visualize_all_scenarios(results_dict, metrics_dict, output_dir=args.output_dir)
                except TypeError:
                    # Fallback if visualizer signature hasn't been updated
                    visualize_all_scenarios(results_dict, metrics_dict)
                logger.info("Visualizations complete!")
            except Exception as e:
                logger.error(f"Visualization failed: {e}")
                logger.warning("Continuing without visualizations...")
        
        logger.info("\n" + "="*60)
        logger.info("SIMULATION COMPLETE!")
        logger.info("="*60)
        logger.info("\nOutput files:")
        logger.info("  - results_*.csv : Time-series simulation data")
        logger.info("  - *_metrics.json : Resilience and performance metrics")
        logger.info("  - plot_*.png : Power flow visualizations")
        logger.info("  - report_*.pdf : Comprehensive PDF reports")
        logger.info("  - parameters.json : Configuration used")
        
        # Export configuration for reproducibility
        config.to_json('parameters.json')
        
    except Exception as e:
        logger.error(f"Simulation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()