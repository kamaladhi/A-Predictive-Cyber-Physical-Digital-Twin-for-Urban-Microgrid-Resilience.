import json
import time
from pathlib import Path
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# Updated paths for simulation results
RESULTS_DIR = Path(__file__).parent.parent / "city_simulation_results"
LOG_DIR = Path(__file__).parent.parent / "digital_twin_logs"
DATA_FUSION_PATH = LOG_DIR / "data_fusion.csv"
ALERTS_PATH = LOG_DIR / "dr_alerts.json"

st.set_page_config(page_title="Digital Twin City Resilience Dashboard", layout="wide")
st.title("🏙️ Digital Twin City Resilience Dashboard")
st.caption("Comprehensive view of city-level microgrid coordination and resilience metrics")

# Sidebar controls
st.sidebar.title("🎛️ Dashboard Controls")
scenario = st.sidebar.selectbox(
    "Select Scenario",
    options=["normal_operation", "outage_6h", "outage_12h"],
    help="Choose which simulation scenario to analyze"
)

@st.cache_data
def load_simulation_results(scenario_name):
    """Load CSV results for the selected scenario"""
    scenario_dir = RESULTS_DIR / scenario_name
    if not scenario_dir.exists():
        return None, None, None, None
    
    try:
        city_metrics = pd.read_csv(scenario_dir / "city_metrics.csv")
        hospital_ts = pd.read_csv(scenario_dir / "hospital_timeseries.csv")
        university_ts = pd.read_csv(scenario_dir / "university_timeseries.csv")
        industrial_ts = pd.read_csv(scenario_dir / "industrial_timeseries.csv")
        residential_ts = pd.read_csv(scenario_dir / "residential_timeseries.csv")
        
        summary_path = scenario_dir / "summary.json"
        summary = None
        if summary_path.exists():
            with open(summary_path) as f:
                summary = json.load(f)
        
        return city_metrics, {
            "hospital": hospital_ts,
            "university": university_ts,
            "industrial": industrial_ts,
            "residential": residential_ts
        }, summary, city_metrics
    except Exception as e:
        st.error(f"Error loading data: {e}")
        return None, None, None, None

if not RESULTS_DIR.exists():
    st.error(f"Results directory not found: {RESULTS_DIR}")
    st.info("Please run the simulation first: `python run_digital_twin_city_simulation.py`")
    st.stop()

city_metrics, timeseries_data, summary, metrics_df = load_simulation_results(scenario)

if city_metrics is None:
    st.warning(f"No results found for scenario: {scenario}")
    st.stop()

# ==================== SECTION 1: KEY METRICS ====================
st.header("📊 Resilience Metrics Summary")

if summary:
    col1, col2, col3, col4 = st.columns(4)
    
    col1.metric(
        "City Survivability Index",
        f"{summary.get('city_survivability_index', 0):.4f}",
        delta="✅ Target: > 0.90"
    )
    col2.metric(
        "Critical Load Preservation",
        f"{summary.get('critical_load_preservation_ratio', 0)*100:.2f}%",
        delta="✅ Target: > 95%"
    )
    col3.metric(
        "Priority Violations",
        f"{summary.get('priority_violation_timesteps', 0)}",
        delta="✅ Target: 0"
    )
    col4.metric(
        "State Confidence",
        f"{summary.get('state_confidence', 0):.4f}",
        delta="✅ Target: > 0.96"
    )

st.divider()

# ==================== SECTION 2: CITY-LEVEL METRICS ====================
st.header("🏙️ City-Level Energy Metrics")

if not city_metrics.empty:
    # Energy metrics
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            "Total Unserved Energy",
            f"{city_metrics['unserved_energy_kwh'].sum():.2f} kWh",
            help="Total energy not supplied across city"
        )
    
    with col2:
        st.metric(
            "Priority Violations",
            f"{city_metrics['priority_violations'].sum():.0f}",
            help="Total priority violation events"
        )
    
    with col3:
        st.metric(
            "Scenario Duration",
            f"{len(city_metrics)} timesteps",
            help="Total simulation timesteps"
        )
    
    st.divider()
    
    # City metrics timeseries
    st.subheader("Metrics Over Time")
    
    # Add step column for plotting (convert timestamp index to step)
    city_metrics_plot = city_metrics.copy()
    city_metrics_plot['step'] = range(len(city_metrics_plot))
    
    col1, col2 = st.columns(2)
    
    with col1:
        fig_csi = px.line(
            city_metrics_plot,
            x="step",
            y="city_survivability_index",
            markers=True,
            title="City Survivability Index (CSI)",
            labels={"step": "Time Step", "city_survivability_index": "CSI"},
            line_shape="linear"
        )
        fig_csi.add_hline(y=0.90, line_dash="dash", line_color="red", annotation_text="Target: 0.90")
        st.plotly_chart(fig_csi, use_container_width=True)
    
    with col2:
        fig_unserved = px.line(
            city_metrics_plot,
            x="step",
            y="unserved_energy_kwh",
            markers=True,
            title="Unserved Energy Over Time",
            labels={"step": "Time Step", "unserved_energy_kwh": "Unserved Energy (kWh)"},
            line_shape="linear"
        )
        st.plotly_chart(fig_unserved, use_container_width=True)

st.divider()

# ==================== SECTION 3: PER-MICROGRID PERFORMANCE ====================
st.header("🔋 Individual Microgrid Performance")

selected_mg = st.selectbox(
    "Select Microgrid",
    options=["hospital", "university", "industrial", "residential"],
    help="View detailed metrics for each microgrid"
)

if selected_mg and timeseries_data and selected_mg in timeseries_data:
    mg_data = timeseries_data[selected_mg]
    
    # KPIs
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Battery SoC (Final)", f"{mg_data['battery_soc_percent'].iloc[-1]:.1f}%")
    col2.metric("Avg Load", f"{mg_data['total_load_kw'].mean():.1f} kW")
    col3.metric("Peak Load", f"{mg_data['total_load_kw'].max():.1f} kW")
    col4.metric("Total Load Shed", f"{mg_data['load_shed_kw'].sum():.1f} kWh")
    
    st.divider()
    
    # Battery SoC
    col1, col2 = st.columns(2)
    
    # Add step column
    mg_data_plot = mg_data.copy()
    mg_data_plot['step'] = range(len(mg_data_plot))
    
    with col1:
        fig_soc = px.line(
            mg_data_plot,
            x="step",
            y="battery_soc_percent",
            markers=True,
            title=f"{selected_mg.upper()} - Battery State of Charge",
            labels={"battery_soc_percent": "SoC (%)", "step": "Time Step"}
        )
        fig_soc.add_hline(y=20, line_dash="dash", line_color="orange", annotation_text="Warning: 20%")
        fig_soc.add_hline(y=0, line_dash="dash", line_color="red", annotation_text="Empty")
        st.plotly_chart(fig_soc, use_container_width=True)
    
    with col2:
        fig_power = px.line(
            mg_data_plot,
            x="step",
            y=["total_load_kw", "battery_power_kw", "generator_power_kw"],
            markers=True,
            title=f"{selected_mg.upper()} - Power Generation & Load",
            labels={"step": "Time Step", "value": "Power (kW)", "variable": "Source"},
            line_shape="linear"
        )
        st.plotly_chart(fig_power, use_container_width=True)

st.divider()

# ==================== SECTION 4: COMPARATIVE ANALYSIS ====================
st.header("📈 All Microgrids Comparison")

if timeseries_data:
    metric_choice = st.selectbox(
        "Select Metric",
        options=["battery_soc_percent", "total_load_kw", "battery_power_kw", "generator_power_kw", "load_shed_kw"],
        help="Compare this metric across all microgrids"
    )
    
    fig_compare = make_subplots(
        rows=2, cols=2,
        subplot_titles=("Hospital", "University", "Industrial", "Residential"),
        specs=[[{"secondary_y": False}, {"secondary_y": False}],
               [{"secondary_y": False}, {"secondary_y": False}]]
    )
    
    mg_names = ["hospital", "university", "industrial", "residential"]
    positions = [(1, 1), (1, 2), (2, 1), (2, 2)]
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
    
    for (mg_name, (row, col), color) in zip(mg_names, positions, colors):
        if mg_name in timeseries_data:
            data = timeseries_data[mg_name].copy()
            data['step'] = range(len(data))
            if metric_choice in data.columns:
                fig_compare.add_trace(
                    go.Scatter(
                        x=data["step"],
                        y=data[metric_choice],
                        name=mg_name.upper(),
                        line=dict(color=color),
                        mode="lines+markers"
                    ),
                    row=row, col=col
                )
    
    fig_compare.update_xaxes(title_text="Time Step")
    fig_compare.update_yaxes(title_text=metric_choice.replace("_", " "))
    fig_compare.update_layout(height=800, showlegend=False, title_text=f"Microgrid Comparison: {metric_choice.replace('_', ' ')}")
    st.plotly_chart(fig_compare, use_container_width=True)

st.divider()

# ==================== SECTION 5: SCENARIO INFO ====================
st.header("ℹ️ Scenario Information")

scenario_descriptions = {
    "normal_operation": "Grid is available throughout. Tests baseline performance and normal load management.",
    "outage_6h": "Grid outage from 0-6 hours. Tests battery-backed resilience and load shedding response.",
    "outage_12h": "Extended grid outage from 0-12 hours. Tests sustained island operation and critical load protection."
}

st.write(f"**{scenario}**")
st.info(scenario_descriptions.get(scenario, ""))

if summary:
    st.json(summary)

st.markdown("---")
st.caption("Dashboard created for Digital Twin City Resilience Analysis | Data from simulation results")
