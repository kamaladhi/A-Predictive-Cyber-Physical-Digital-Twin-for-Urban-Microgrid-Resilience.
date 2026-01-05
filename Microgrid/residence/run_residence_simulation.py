import argparse
import logging
from datetime import datetime
import json
import sys
import numpy as np
import os

from residence_parameters import MicrogridConfig, create_default_config
from residence_simulator import MicrogridSimulator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def to_python(obj):
    """Convert numpy types to Python native types"""
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
    """Run a single residential scenario simulation"""
    
    logger.info(f"\n{'='*70}")
    logger.info(f"🏘️ Running Residential Scenario: {scenario_name}")
    logger.info(f"{'='*70}")
    
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
    logger.info(f"\n📊 Scenario Summary:")
    logger.info(f"  Duration: {duration_hours} hours")
    if outage_start is not None:
        logger.info(f"  Outage: {outage_start}h - {outage_start + outage_duration}h")
    logger.info(f"  Total Load: {metrics['total_load_energy_kwh']:.1f} kWh")
    logger.info(f"  PV Generation: {metrics['total_pv_energy_kwh']:.1f} kWh")
    logger.info(f"  PV Penetration: {metrics['pv_penetration_percent']:.1f}%")
    
    if 'outage_duration_hours' in metrics:
        logger.info(f"\n🏘️ Residential Resilience Metrics:")
        logger.info(f"  Critical Load Served: {metrics['critical_load_served_percent']:.1f}% ✅")
        logger.info(f"  Critical Loads Protected: {metrics['critical_load_protected']}")
        logger.info(f"  Min Battery SoC: {metrics['min_battery_soc_percent']:.1f}%")
        logger.info(f"  Load Shed Events: {metrics['load_shed_events']}")
        logger.info(f"  Max Non-Critical Shed: {metrics['max_non_critical_shed_percent']:.1f}%")
        logger.info(f"  Total Shed Energy: {metrics['total_shed_energy_kwh']:.1f} kWh")
        logger.info(f"\n😰 RESIDENT DISCOMFORT:")
        logger.info(f"  AC Unavailable: {metrics['ac_outage_minutes']:.0f} minutes ({metrics['percent_time_ac_unavailable']:.1f}% of outage)")
        logger.info(f"  EV Charging Blocked: {metrics['ev_charging_blocked_minutes']:.0f} minutes ({metrics['percent_time_ev_blocked']:.1f}% of outage)")
        logger.info(f"  Comfort Score: {metrics['comfort_score']:.1f}/100 (0=unbearable, 100=comfortable)")
        logger.info(f"\n⚡ Generator Performance:")
        logger.info(f"  Generator Used: {metrics['generator_used']}")
        logger.info(f"  Runtime: {metrics['gen1_runtime_hours']:.1f} hours")
        logger.info(f"  Fuel Consumed: {metrics['gen1_fuel_consumed_liters']:.1f} liters")
        logger.info(f"  Survived Full Outage: {metrics['survived_full_outage']}")
    
    # Prepare output folders
    scenario_dir = os.path.join(output_dir, scenario_name)
    csv_dir = os.path.join(scenario_dir, 'csv')
    metrics_dir = os.path.join(scenario_dir, 'metrics')
    png_dir = os.path.join(scenario_dir, 'png')
    pdf_dir = os.path.join(scenario_dir, 'pdf')
    
    for d in (csv_dir, metrics_dir, png_dir, pdf_dir):
        os.makedirs(d, exist_ok=True)
    
    # Export results
    csv_path = os.path.join(csv_dir, f"results_{scenario_name}.csv")
    metrics_path = os.path.join(metrics_dir, f"{scenario_name}_metrics.json")
    sim.export_results(df, csv_path)
    sim.export_metrics(metrics, metrics_path)
    
    return df, metrics


def run_all_scenarios(config: MicrogridConfig, output_dir: str = '.'):
    """Run all predefined residential scenarios"""
    
    scenarios = {
        'night_outage': {
            'duration_hours': 24,
            'outage_start': 22,
            'outage_duration': 8,
            'description': '🌙 Night outage (no PV available)'
        },
        'evening_peak_outage': {
            'duration_hours': 24,
            'outage_start': 18,
            'outage_duration': 5,
            'description': '🔥 Evening peak outage (WORST CASE - AC + cooking + EV)'
        },
        'extended_outage': {
            'duration_hours': 36,
            'outage_start': 14,
            'outage_duration': 12,
            'description': '⏳ Extended 12-hour outage (severe discomfort)'
        },
        'morning_peak_outage': {
            'duration_hours': 24,
            'outage_start': 7,
            'outage_duration': 4,
            'description': '🌅 Morning peak outage (hot water + breakfast)'
        },
        'normal_operation': {
            'duration_hours': 72,
            'outage_start': None,
            'outage_duration': None,
            'description': '✅ Normal grid-connected operation (72h)'
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
    
    # Save combined metrics
    os.makedirs(output_dir, exist_ok=True)
    all_metrics_path = os.path.join(output_dir, 'all_scenario_metrics.json')
    with open(all_metrics_path, 'w') as f:
        json.dump(to_python(all_metrics), f, indent=2)
    
    logger.info(f"\n{'='*70}")
    logger.info("🏘️ ALL RESIDENTIAL SCENARIOS COMPLETE!")
    logger.info(f"{'='*70}")
    
    return all_results, all_metrics


def run_custom_scenario(config: MicrogridConfig, args):
    """Run custom user-defined scenario"""
    logger.info("\n🏘️ Running custom residential scenario...")
    
    df, metrics = run_single_scenario(
        config,
        args.name,
        args.duration,
        args.outage_start,
        args.outage_duration,
        output_dir=args.output_dir
    )
    
    return {args.name: df}, {args.name: metrics}


def main():
    """Main entry point for residential microgrid simulation"""
    
    parser = argparse.ArgumentParser(
        description='🏘️ Residential Microgrid Simulator - Gated Community Digital Twin',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all predefined residential scenarios
  python run_residential_simulation.py --all
  
  # Run single predefined scenario
  python run_residential_simulation.py --scenario evening_peak_outage
  
  # Run custom scenario
  python run_residential_simulation.py --custom --name stress_test --duration 48 --outage-start 18 --outage-duration 14
  
  # Use custom configuration
  python run_residential_simulation.py --all --config my_residential_config.json
  
  # Specify output directory
  python run_residential_simulation.py --all --output-dir ./residential_results
  
Residential Reality Check:
  - 100 kW critical loads (safety only) - PROTECTED
  - 480 kW non-critical loads (comfort) - SHEDDABLE
  - Battery: 450 kWh (cannot sustain peak load)
  - Generator: 300 kW (insufficient for peak)
  - Residents WILL experience:
    • AC shutoff → hot apartments
    • EV charging blocked
    • Service degradation
  - Survival > Comfort
        """
    )
    
    # Mode selection
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument('--all', action='store_true',
                           help='Run all predefined residential scenarios')
    mode_group.add_argument('--scenario', type=str,
                           choices=['night_outage', 'evening_peak_outage', 'extended_outage', 
                                   'morning_peak_outage', 'normal_operation'],
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
    parser.add_argument('--output-dir', type=str, default='residential_results',
                       help='Output directory for results')
    
    args = parser.parse_args()
    
    # Load configuration
    if args.config:
        logger.info(f"Loading residential configuration from: {args.config}")
        config = MicrogridConfig.from_json(args.config)
    else:
        logger.info("Using default residential configuration")
        config = create_default_config()
    
    # Validate configuration
    warnings = config.validate()
    if warnings:
        logger.info("ℹ️ Configuration notes:")
        for w in warnings:
            logger.info(f"  {w}")
    else:
        logger.info("✅ Residential configuration validated")
    
    # Print configuration summary
    logger.info("\n" + "="*70)
    logger.info("🏘️ RESIDENTIAL MICROGRID CONFIGURATION")
    logger.info("="*70)
    logger.info(f"Facility: {config.facility_name} ({config.facility_type})")
    logger.info(f"Location: {config.location}")
    logger.info(f"City Priority: Level {config.city_priority_level} (Hospital=1, University=2, Residential=3)")
    logger.info(f"\n📊 LOAD PROFILE:")
    logger.info(f"  Peak Load: {config.load_profile.peak_load} kW (Evening)")
    logger.info(f"  Average Load: {config.load_profile.average_load:.1f} kW")
    logger.info(f"  🔴 CRITICAL: {config.load_profile.total_critical_load} kW (Safety only)")
    logger.info(f"  NON-CRITICAL: {config.load_profile.total_non_critical_load} kW (Comfort - SHEDDABLE)")
    logger.info(f"\n🔋 BATTERY: {config.battery.usable_capacity_kwh} kWh / {config.battery.max_discharge_power_kw} kW")
    logger.info(f"  ⚠️ CANNOT handle peak load ({config.load_profile.peak_load} kW)")
    logger.info(f"  Critical Backup: {config.battery.critical_backup_hours:.1f} hours")
    logger.info(f"\n☀️ SOLAR PV: {config.pv.installed_capacity_kwp} kWp")
    logger.info(f"  Coverage: {config.pv.estimated_daily_generation_kwh / (config.load_profile.average_load * 24) * 100:.1f}% of daily consumption")
    logger.info(f"\n⚡ GENERATOR: {config.generator.rated_power_kw} kW")
    logger.info(f"  ⚠️ Undersized for peak load")
    logger.info(f"  Fuel: {config.generator.fuel_tank_capacity_liters} liters")
    logger.info("\n⚠️ EXPECTED RESIDENT IMPACT:")
    logger.info("  • AC will be shed during extended outages")
    logger.info("  • EV charging disabled immediately")
    logger.info("  • Hot water may be limited")
    logger.info("  • Only safety systems guaranteed")
    logger.info("="*70 + "\n")
    
    # Run simulation
    try:
        if args.all:
            results_dict, metrics_dict = run_all_scenarios(config, output_dir=args.output_dir)
        elif args.scenario:
            scenario_params = {
                'night_outage': (24, 22, 8),
                'evening_peak_outage': (24, 18, 5),
                'extended_outage': (36, 14, 12),
                'morning_peak_outage': (24, 7, 4),
                'normal_operation': (72, None, None)
            }
            duration, start, dur = scenario_params[args.scenario]
            df, metrics = run_single_scenario(config, args.scenario, duration, start, dur, output_dir=args.output_dir)
            results_dict = {args.scenario: df}
            metrics_dict = {args.scenario: metrics}
        else:  # custom
            results_dict, metrics_dict = run_custom_scenario(config, args)
        
        # Generate visualizations (if module available)
        try:
            from residence_visualizer import visualize_all_scenarios
            logger.info("\n📊 Generating visualizations...")
            try:
                visualize_all_scenarios(results_dict, metrics_dict, output_dir=args.output_dir)
                logger.info("✅ Visualizations complete!")
            except TypeError:
                visualize_all_scenarios(results_dict, metrics_dict)
        except ImportError:
            logger.warning("⚠️ Visualizer module not found - skipping visualizations")
        except Exception as e:
            logger.error(f"❌ Visualization failed: {e}")
        
        logger.info("\n" + "="*70)
        logger.info("🏘️ RESIDENTIAL SIMULATION COMPLETE!")
        logger.info("="*70)
        logger.info("\n📁 Output files:")
        logger.info(f"  {args.output_dir}/")
        logger.info("    └── [scenario_name]/")
        logger.info("        ├── csv/results_*.csv       (Time-series data)")
        logger.info("        ├── metrics/*_metrics.json  (Performance + discomfort metrics)")
        logger.info("        ├── png/plot_*.png          (Visualizations)")
        logger.info("        └── pdf/report_*.pdf        (Detailed reports)")
        logger.info(f"\n  {args.output_dir}/all_scenario_metrics.json  (Combined metrics)")
        
        # Export configuration
        config_path = os.path.join(args.output_dir, 'residential_parameters.json')
        config.to_json(config_path)
        logger.info(f"  {config_path}  (Configuration used)")
        
        logger.info("\n✅ All data exported successfully!")
        
    except Exception as e:
        logger.error(f"❌ Simulation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()    cd d:\Digital-twin-microgrid
    python test_integration.py
    # Expected: ✅ ALL TESTS PASSED