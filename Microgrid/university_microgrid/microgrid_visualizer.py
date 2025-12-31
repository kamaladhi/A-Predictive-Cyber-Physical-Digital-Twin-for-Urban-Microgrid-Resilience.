
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
import numpy as np
from typing import Dict, List
import json
import os


class MicrogridVisualizer:
    """Create visualizations for microgrid simulation results"""
    
    def __init__(self, style='seaborn-v0_8-darkgrid'):
        try:
            plt.style.use(style)
        except:
            plt.style.use('default')
        
        # Color scheme
        self.colors = {
            'pv': '#FFA500',
            'battery_charge': '#4CAF50',
            'battery_discharge': '#F44336',
            'generator': '#9C27B0',
            'grid': '#2196F3',
            'load': '#607D8B',
            'critical_load': '#FF5722',
            'shed_load': '#FFEB3B'
        }
    
    def plot_power_flow(self, df: pd.DataFrame, title: str = "Microgrid Power Flow"):
        """
        Create comprehensive power flow visualization
        """
        fig = plt.figure(figsize=(16, 12))
        gs = GridSpec(4, 2, figure=fig, hspace=0.3, wspace=0.25)
        
        # Convert timestamp to hours for x-axis
        if 'time_minutes' in df.columns:
            time_axis = df['time_minutes'] / 60
            xlabel = 'Time (hours)'
        else:
            time_axis = range(len(df))
            xlabel = 'Timestep'
        
        # 1. Power Balance
        ax1 = fig.add_subplot(gs[0, :])
        ax1.plot(time_axis, df['pv_power_kw'], label='PV', color=self.colors['pv'], linewidth=2)
        ax1.plot(time_axis, df['generator_power_kw'], label='Generator', color=self.colors['generator'], linewidth=2)
        ax1.plot(time_axis, df['grid_power_kw'], label='Grid', color=self.colors['grid'], linewidth=2)
        ax1.plot(time_axis, -df['total_load_kw'], label='Load', color=self.colors['load'], linewidth=2, linestyle='--')
        ax1.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
        ax1.set_ylabel('Power (kW)')
        ax1.set_title(f'{title} - Power Balance', fontsize=14, fontweight='bold')
        ax1.legend(loc='upper right')
        ax1.grid(True, alpha=0.3)
        
        # Highlight islanded periods
        islanded = df['operation_mode'] == 'ISLANDED'
        if islanded.any():
            ax1.fill_between(time_axis, ax1.get_ylim()[0], ax1.get_ylim()[1], 
                           where=islanded, alpha=0.2, color='red', label='Islanded')
        
        # 2. Battery Status
        ax2 = fig.add_subplot(gs[1, 0])
        ax2_twin = ax2.twinx()
        
        # Battery power
        battery_charge = df['battery_power_kw'].clip(lower=0)
        battery_discharge = -df['battery_power_kw'].clip(upper=0)
        ax2.fill_between(time_axis, 0, battery_charge, label='Charging', 
                        color=self.colors['battery_charge'], alpha=0.6)
        ax2.fill_between(time_axis, 0, battery_discharge, label='Discharging', 
                        color=self.colors['battery_discharge'], alpha=0.6)
        ax2.set_ylabel('Battery Power (kW)')
        ax2.legend(loc='upper left')
        ax2.grid(True, alpha=0.3)
        
        # Battery SoC
        ax2_twin.plot(time_axis, df['battery_soc_percent'], label='SoC', 
                     color='blue', linewidth=2, linestyle='-')
        ax2_twin.set_ylabel('SoC (%)', color='blue')
        ax2_twin.tick_params(axis='y', labelcolor='blue')
        ax2_twin.set_ylim([0, 100])
        ax2_twin.legend(loc='upper right')
        
        ax2.set_title('Battery Energy Storage System', fontsize=12, fontweight='bold')
        
        # 3. Load Profile
        ax3 = fig.add_subplot(gs[1, 1])
        ax3.fill_between(time_axis, 0, df['critical_load_kw'], 
                        label='Critical Load', color=self.colors['critical_load'], alpha=0.7)
        ax3.fill_between(time_axis, df['critical_load_kw'], df['total_load_kw'], 
                        label='Non-Critical Load', color=self.colors['load'], alpha=0.5)
        
        if df['shed_load_kw'].max() > 0:
            ax3.plot(time_axis, df['shed_load_kw'], label='Shed Load', 
                    color=self.colors['shed_load'], linewidth=2, linestyle='-')
        
        ax3.set_ylabel('Load (kW)')
        ax3.set_title('Load Profile & Shedding', fontsize=12, fontweight='bold')
        ax3.legend(loc='upper right')
        ax3.grid(True, alpha=0.3)
        
        # 4. Generator Status
        ax4 = fig.add_subplot(gs[2, 0])
        gen_power = df['generator_power_kw']
        gen_running = df['generator_state'] == 'RUNNING'
        
        ax4.fill_between(time_axis, 0, gen_power, 
                        color=self.colors['generator'], alpha=0.6, label='Generator Output')
        ax4.scatter(time_axis[gen_running], gen_power[gen_running], 
                   color='red', s=10, alpha=0.5, label='Running')
        
        ax4.set_ylabel('Generator Power (kW)')
        ax4.set_title('Diesel Generator Operation', fontsize=12, fontweight='bold')
        ax4.legend(loc='upper right')
        ax4.grid(True, alpha=0.3)
        
        # 5. Grid & System Status
        ax5 = fig.add_subplot(gs[2, 1])
        ax5_twin = ax5.twinx()
        
        # Frequency
        ax5.plot(time_axis, df['frequency_hz'], label='Frequency', 
                color='blue', linewidth=2)
        ax5.axhline(y=50, color='green', linestyle='--', linewidth=1, alpha=0.5)
        ax5.axhline(y=50.5, color='red', linestyle='--', linewidth=1, alpha=0.5)
        ax5.axhline(y=49.5, color='red', linestyle='--', linewidth=1, alpha=0.5)
        ax5.set_ylabel('Frequency (Hz)', color='blue')
        ax5.tick_params(axis='y', labelcolor='blue')
        ax5.set_ylim([49, 51])
        ax5.legend(loc='upper left')
        
        # Voltage
        ax5_twin.plot(time_axis, df['voltage_pu'], label='Voltage', 
                     color='green', linewidth=2)
        ax5_twin.axhline(y=1.0, color='green', linestyle='--', linewidth=1, alpha=0.5)
        ax5_twin.set_ylabel('Voltage (p.u.)', color='green')
        ax5_twin.tick_params(axis='y', labelcolor='green')
        ax5_twin.set_ylim([0.9, 1.1])
        ax5_twin.legend(loc='upper right')
        
        ax5.set_title('Grid Parameters', fontsize=12, fontweight='bold')
        ax5.grid(True, alpha=0.3)
        
        # 6. Energy Balance
        ax6 = fig.add_subplot(gs[3, :])
        
        # Calculate cumulative energy
        dt_hours = df['time_minutes'].diff().fillna(0) / 60
        pv_energy_cum = (df['pv_power_kw'] * dt_hours).cumsum()
        gen_energy_cum = (df['generator_power_kw'] * dt_hours).cumsum()
        grid_energy_cum = (df['grid_power_kw'] * dt_hours).cumsum()
        load_energy_cum = (df['total_load_kw'] * dt_hours).cumsum()
        
        ax6.plot(time_axis, pv_energy_cum, label='PV Energy', 
                color=self.colors['pv'], linewidth=2)
        ax6.plot(time_axis, gen_energy_cum, label='Generator Energy', 
                color=self.colors['generator'], linewidth=2)
        ax6.plot(time_axis, grid_energy_cum, label='Grid Energy', 
                color=self.colors['grid'], linewidth=2)
        ax6.plot(time_axis, load_energy_cum, label='Load Energy', 
                color=self.colors['load'], linewidth=2, linestyle='--')
        
        ax6.set_xlabel(xlabel)
        ax6.set_ylabel('Cumulative Energy (kWh)')
        ax6.set_title('Cumulative Energy Flow', fontsize=12, fontweight='bold')
        ax6.legend(loc='upper left')
        ax6.grid(True, alpha=0.3)
        
        plt.suptitle(title, fontsize=16, fontweight='bold', y=0.995)
        
        return fig
    
    def plot_resilience_comparison(self, metrics_dict: Dict[str, Dict]):
        """
        Compare resilience metrics across multiple scenarios
        """
        fig, axes = plt.subplots(2, 3, figsize=(18, 10))
        fig.suptitle('Resilience Metrics Comparison', fontsize=16, fontweight='bold')
        
        scenarios = list(metrics_dict.keys())
        
        # 1. Critical Load Service
        ax = axes[0, 0]
        values = [metrics_dict[s].get('critical_load_served_percent', 100) for s in scenarios]
        bars = ax.bar(scenarios, values, color='green', alpha=0.7)
        ax.axhline(y=100, color='red', linestyle='--', linewidth=2)
        ax.set_ylabel('Critical Load Served (%)')
        ax.set_title('Critical Load Service')
        ax.set_ylim([0, 105])
        ax.tick_params(axis='x', rotation=45)
        
        for bar, val in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, 
                   f'{val:.1f}%', ha='center', va='bottom')
        
        # 2. Battery Performance
        ax = axes[0, 1]
        min_soc = [metrics_dict[s].get('min_battery_soc_percent', 100) for s in scenarios]
        bars = ax.bar(scenarios, min_soc, color='blue', alpha=0.7)
        ax.axhline(y=20, color='red', linestyle='--', linewidth=2, label='Min SoC Limit')
        ax.set_ylabel('Minimum SoC (%)')
        ax.set_title('Battery Minimum State of Charge')
        ax.tick_params(axis='x', rotation=45)
        ax.legend()
        
        # 3. Generator Usage
        ax = axes[0, 2]
        gen_hours = [metrics_dict[s].get('generator_runtime_hours', 0) for s in scenarios]
        bars = ax.bar(scenarios, gen_hours, color='purple', alpha=0.7)
        ax.set_ylabel('Runtime (hours)')
        ax.set_title('Generator Runtime')
        ax.tick_params(axis='x', rotation=45)
        
        for bar, val in zip(bars, gen_hours):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1, 
                       f'{val:.1f}h', ha='center', va='bottom')
        
        # 4. Load Shed Events
        ax = axes[1, 0]
        shed_events = [metrics_dict[s].get('load_shed_events', 0) for s in scenarios]
        bars = ax.bar(scenarios, shed_events, color='orange', alpha=0.7)
        ax.set_ylabel('Number of Events')
        ax.set_title('Load Shedding Events')
        ax.tick_params(axis='x', rotation=45)
        
        # 5. Fuel Consumption
        ax = axes[1, 1]
        fuel = [metrics_dict[s].get('generator_fuel_consumed_liters', 0) for s in scenarios]
        bars = ax.bar(scenarios, fuel, color='brown', alpha=0.7)
        ax.set_ylabel('Fuel (liters)')
        ax.set_title('Diesel Fuel Consumption')
        ax.tick_params(axis='x', rotation=45)
        
        # 6. PV Contribution
        ax = axes[1, 2]
        pv_pen = [metrics_dict[s].get('pv_penetration_percent', 0) for s in scenarios]
        bars = ax.bar(scenarios, pv_pen, color='orange', alpha=0.7)
        ax.set_ylabel('PV Penetration (%)')
        ax.set_title('Solar PV Contribution')
        ax.tick_params(axis='x', rotation=45)
        
        plt.tight_layout()
        return fig
    
    def create_report(self, scenario_name: str, df: pd.DataFrame, 
                     metrics: Dict, output_file: str):
        """
        Create comprehensive PDF report
        """
        from matplotlib.backends.backend_pdf import PdfPages
        
        with PdfPages(output_file) as pdf:
            # Page 1: Power Flow
            fig1 = self.plot_power_flow(df, title=f"Scenario: {scenario_name}")
            pdf.savefig(fig1, bbox_inches='tight')
            plt.close(fig1)
            
            # Page 2: Metrics Summary
            fig2, ax = plt.subplots(figsize=(11, 8.5))
            ax.axis('off')
            
            # Title
            title_text = f"Microgrid Simulation Report\nScenario: {scenario_name}"
            ax.text(0.5, 0.95, title_text, ha='center', va='top', 
                   fontsize=16, fontweight='bold')
            
            # Metrics table
            y_pos = 0.85
            line_height = 0.04
            
            metric_groups = {
                'Simulation Parameters': {
                    'Duration (hours)': df['time_minutes'].iloc[-1] / 60,
                    'Time Resolution (min)': df['time_minutes'].diff().mode()[0] if len(df) > 1 else 5,
                },
                'Resilience Metrics': {
                    'Outage Duration (h)': metrics.get('outage_duration_hours', 'N/A'),
                    'Critical Load Served (%)': metrics.get('critical_load_served_percent', 'N/A'),
                    'Min Battery SoC (%)': metrics.get('min_battery_soc_percent', 'N/A'),
                    'Survived Full Outage': metrics.get('survived_full_outage', 'N/A'),
                    'Load Shed Events': metrics.get('load_shed_events', 'N/A'),
                    'Max Shed Load (kW)': metrics.get('max_shed_load_kw', 'N/A'),
                },
                'Energy Balance': {
                    'Total Load (kWh)': metrics.get('total_load_energy_kwh', 0),
                    'PV Generation (kWh)': metrics.get('total_pv_energy_kwh', 0),
                    'Grid Energy (kWh)': metrics.get('total_grid_energy_kwh', 0),
                    'PV Penetration (%)': metrics.get('pv_penetration_percent', 0),
                },
                'Generator Performance': {
                    'Runtime (hours)': metrics.get('generator_runtime_hours', 0),
                    'Fuel Consumed (liters)': metrics.get('generator_fuel_consumed_liters', 0),
                    'Avg Fuel Rate (L/h)': metrics.get('generator_fuel_consumed_liters', 0) / max(metrics.get('generator_runtime_hours', 1), 0.1),
                }
            }
            
            for group_name, group_metrics in metric_groups.items():
                ax.text(0.1, y_pos, group_name, fontsize=12, fontweight='bold')
                y_pos -= line_height * 1.5
                
                for key, value in group_metrics.items():
                    if isinstance(value, float):
                        value_str = f"{value:.2f}"
                    else:
                        value_str = str(value)
                    
                    ax.text(0.15, y_pos, f"{key}:", fontsize=10)
                    ax.text(0.6, y_pos, value_str, fontsize=10, fontweight='bold')
                    y_pos -= line_height
                
                y_pos -= line_height * 0.5
            
            pdf.savefig(fig2, bbox_inches='tight')
            plt.close(fig2)
            
            # Set PDF metadata
            d = pdf.infodict()
            d['Title'] = f'Microgrid Simulation Report - {scenario_name}'
            d['Author'] = 'Amrita University Microgrid Simulator'
            d['Subject'] = 'Microgrid Performance Analysis'
            d['Keywords'] = 'Microgrid, Simulation, Resilience, Energy'
        
        print(f"Report saved to: {output_file}")


def visualize_all_scenarios(results_dict: Dict[str, pd.DataFrame], 
                           metrics_dict: Dict[str, Dict],
                           output_dir: str = '.'):
    """
    Create visualizations for all scenarios
    """
    viz = MicrogridVisualizer()
    
    # Individual scenario plots
    for scenario_name, df in results_dict.items():
        print(f"Creating visualization for {scenario_name}...")
        fig = viz.plot_power_flow(df, title=f"Scenario: {scenario_name.replace('_', ' ').title()}")

        # Ensure scenario subfolders exist
        png_dir = os.path.join(output_dir, scenario_name, 'png')
        pdf_dir = os.path.join(output_dir, scenario_name, 'pdf')
        os.makedirs(png_dir, exist_ok=True)
        os.makedirs(pdf_dir, exist_ok=True)

        png_path = os.path.join(png_dir, f'plot_{scenario_name}.png')
        fig.savefig(png_path, dpi=300, bbox_inches='tight')
        plt.close(fig)
        
        # Create detailed report PDF in scenario/pdf
        pdf_path = os.path.join(pdf_dir, f'report_{scenario_name}.pdf')
        viz.create_report(scenario_name, df, metrics_dict[scenario_name], pdf_path)
    
    # Comparison plot
    print("Creating comparison plot...")
    fig = viz.plot_resilience_comparison(metrics_dict)
    # Save comparison plot at output root (and in png folder)
    png_root = os.path.join(output_dir, 'png')
    os.makedirs(png_root, exist_ok=True)
    comp_path = os.path.join(png_root, 'plot_comparison.png')
    fig.savefig(comp_path, dpi=300, bbox_inches='tight')
    plt.close(fig)
    
    print("All visualizations created successfully!")


if __name__ == "__main__":
    # Load results and create visualizations
    import glob
    
    results_files = glob.glob('results_*.csv')
    
    if len(results_files) == 0:
        print("No results files found. Run microgrid_simulator.py first.")
    else:
        results_dict = {}
        for file in results_files:
            scenario_name = file.replace('results_', '').replace('.csv', '')
            results_dict[scenario_name] = pd.read_csv(file)
        
        # Load metrics
        with open('all_scenario_metrics.json', 'r') as f:
            metrics_dict = json.load(f)
        
        visualize_all_scenarios(results_dict, metrics_dict)