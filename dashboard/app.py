import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
import json
import time
from typing import Dict, Any, List
import paho.mqtt.client as mqtt
from threading import Lock

# ─── Configuration ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CityMicrogrid | Digital Twin Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Dark theme CSS injection
st.markdown("""
<style>
    .main {
        background-color: #0e1117;
    }
    .stMetric {
        background-color: #161b22;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #30363d;
    }
    .stPlotlyChart {
        border-radius: 15px;
        overflow: hidden;
    }
</style>
""", unsafe_allow_html=True)

# ─── State Management (Thread-Safe) ─────────────────────────────────────────
class DashboardState:
    def __init__(self):
        self.lock = Lock()
        self.microgrid_data: Dict[str, List[Dict]] = {}
        self.alerts: List[Dict] = []
        self.metrics: Dict[str, Any] = {"ASAI": 0.0, "EENS": 0.0, "SAIDI": 0.0}
        self.last_update = datetime.now()

    def add_telemetry(self, mg_id: str, data: Dict):
        with self.lock:
            if mg_id not in self.microgrid_data:
                self.microgrid_data[mg_id] = []
            data['timestamp'] = datetime.now()
            self.microgrid_data[mg_id].append(data)
            # Keep only last 50 points
            if len(self.microgrid_data[mg_id]) > 50:
                self.microgrid_data[mg_id].pop(0)
            self.last_update = datetime.now()

    def add_alert(self, alert: Dict):
        with self.lock:
            alert['timestamp'] = datetime.now()
            self.alerts.insert(0, alert)
            if len(self.alerts) > 10:
                self.alerts.pop()

    def update_metrics(self, payload: Dict):
        with self.lock:
            self.metrics.update(payload)

if 'state' not in st.session_state:
    st.session_state.state = DashboardState()

# ─── MQTT Handler (Thread-Safe & Persistent) ────────────────────────────────
class MqttHandler:
    def __init__(self, state_obj: DashboardState):
        self.state = state_obj
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_message = self.on_message
        self.connected = False

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
            if "state" in topic:
                mg_id = topic.split('/')[1]
                self.state.add_telemetry(mg_id, payload)
            elif "alerts" in topic:
                self.state.add_alert(payload)
            elif "metrics" in topic:
                self.state.update_metrics(payload)
        except Exception as e:
            pass # Avoid printing to console from background thread unless critical

    def connect(self):
        try:
            self.client.connect("localhost", 1883, 60)
            self.client.subscribe("microgrid/+/state")
            self.client.subscribe("city/metrics")
            self.client.subscribe("city/alerts")
            self.client.loop_start()
            self.connected = True
            return True
        except:
            return False

if 'mqtt_handler' in st.session_state:
    # Stale object detection: if the old class didn't have 'state' attribute
    if not hasattr(st.session_state.mqtt_handler, 'state'):
        try:
            st.session_state.mqtt_handler.client.loop_stop()
        except: pass
        del st.session_state.mqtt_handler
    else:
        # Update state reference just in case
        st.session_state.mqtt_handler.state = st.session_state.state

if 'mqtt_handler' not in st.session_state:
    handler = MqttHandler(st.session_state.state)
    if handler.connect():
        st.session_state.mqtt_handler = handler
    else:
        st.sidebar.error("MQTT: Connection Failed")

# ─── Sidebar ────────────────────────────────────────────────────────────────
st.sidebar.title("⚡ Control Center")
st.sidebar.markdown("---")
op_mode = st.sidebar.selectbox("System Mode", ["Automatic", "Manual Override", "Emergency Islanding"])
selected_mg = st.sidebar.selectbox("Focal Microgrid", ["All", "Hospital", "University", "Industry", "Residence"])

st.sidebar.markdown("---")
st.sidebar.info("Triple-State Digital Twin synchronization active.")
if st.sidebar.button("Reset Dashboard"):
    st.session_state.state = DashboardState()

# Scenario HUD
st.sidebar.markdown("---")
st.sidebar.subheader("🕹️ System Scenario HUD")

# Logic to determine scenario
scenario = "🟢 NORMAL"
with st.session_state.state.lock:
    grid_outage = any(st.session_state.state.microgrid_data[mg][-1].get('is_islanded', False)
                      for mg in st.session_state.state.microgrid_data if st.session_state.state.microgrid_data[mg])
    total_shed = sum(st.session_state.state.microgrid_data[mg][-1].get('load_shed_kw', 0)
                     for mg in st.session_state.state.microgrid_data if st.session_state.state.microgrid_data[mg])

    if grid_outage:
        scenario = "🔴 GRID BLACKOUT"
    elif total_shed > 10.0:
        scenario = "🟠 POWER SHORTAGE"

st.sidebar.info(f"**Current State**: {scenario}")
if scenario == "🔴 GRID BLACKOUT":
    st.sidebar.caption("Simulation Mode: Forced Outage. Priority logic active.")
elif scenario == "🟠 POWER SHORTAGE":
    st.sidebar.caption("Simulation Mode: Demand Response active (High Stress).")
else:
    st.sidebar.caption("Simulation Mode: Optimized Economic Dispatch.")

# System Health Indicator
st.sidebar.markdown("---")
st.sidebar.subheader("🌐 System Health")
mqtt_connected = st.session_state.mqtt_handler.connected if 'mqtt_handler' in st.session_state else False
mqtt_status = "🟢 Connected" if mqtt_connected else "🔴 Disconnected"
st.sidebar.write(f"IoT Bus: {mqtt_status}")
sim_status = "🟢 Running" if (datetime.now() - st.session_state.state.last_update).total_seconds() < 15 else "🟡 Stalled"
st.sidebar.write(f"Sim Engine: {sim_status}")

# ─── Main Dashboard ─────────────────────────────────────────────────────────
st.title("🏙️ Urban Microgrid Digital Twin")
st.caption(f"Reality Synchronization: {st.session_state.state.last_update.strftime('%H:%M:%S')}")

# Top Row: City Metrics
m1, m2, m3, m4 = st.columns(4)
metrics = st.session_state.state.metrics

with m1:
    val = metrics.get('ASAI', 1.0)
    st.metric("City ASAI", f"{val:.4f}", help="Average Service Availability Index")
with m2:
    val = metrics.get('EENS', 0.0)
    st.metric("Total EENS", f"{val:.1f} kWh")
with m3:
    # If optimizer stats are available, show solve success rate, otherwise placeholder
    if 'total_solves' in metrics and metrics['total_solves'] > 0:
        rate = (metrics.get('optimal_count', 0) / metrics['total_solves']) * 100
        st.metric("Solver Success", f"{rate:.1f}%")
    else:
        st.metric("System Health", "Optimal")
with m4:
    active_count = len(st.session_state.state.microgrid_data)
    st.metric("Active Microgrids", f"{active_count}/4")

st.markdown("---")

# Middle Row: Real-time Trends
c1, c2 = st.columns(2)

def create_trend_chart(mg_filter="All"):
    fig = go.Figure()
    with st.session_state.state.lock:
        for mg_id, points in st.session_state.state.microgrid_data.items():
            if mg_filter != "All" and mg_id != mg_filter:
                continue
            df = pd.DataFrame(points)
            if not df.empty:
                # Support both field name variations
                y_col = 'soc' if 'soc' in df.columns else ('battery_soc_percent' if 'battery_soc_percent' in df.columns else None)
                if y_col:
                    fig.add_trace(go.Scatter(x=df['timestamp'], y=df[y_col], name=f"{mg_id} SOC", mode='lines+markers'))
    
    fig.update_layout(
        title=f"{mg_filter} Storage (SOC %)",
        template="plotly_dark",
        margin=dict(l=20, r=20, t=40, b=20),
        height=350,
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        yaxis=dict(range=[0, 105])
    )
    return fig

def create_power_chart(mg_filter="All"):
    fig = go.Figure()
    with st.session_state.state.lock:
        for mg_id, points in st.session_state.state.microgrid_data.items():
            if mg_filter != "All" and mg_id != mg_filter:
                continue
            df = pd.DataFrame(points)
            if not df.empty:
                # Load
                load_col = 'total_load_kw' if 'total_load_kw' in df.columns else 'current_load_kw'
                if load_col in df.columns:
                    fig.add_trace(go.Scatter(x=df['timestamp'], y=df[load_col], name=f"{mg_id} Load", mode='lines', line=dict(width=3)))
                
                # PV
                pv_col = 'pv_power_kw' if 'pv_power_kw' in df.columns else 'pv_generation_kw'
                if pv_col in df.columns:
                    fig.add_trace(go.Scatter(x=df['timestamp'], y=df[pv_col], name=f"{mg_id} PV", mode='lines', line=dict(dash='dash')))

    fig.update_layout(
        title=f"{mg_filter} Power Profile (kW)",
        template="plotly_dark",
        margin=dict(l=20, r=20, t=40, b=20),
        height=350,
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)'
    )
    return fig

with c1:
    st.plotly_chart(create_trend_chart(selected_mg), width='stretch')
    st.plotly_chart(create_power_chart(selected_mg), width='stretch')

with c2:
    # Alert Panel
    st.subheader("⚠️ Intelligence & Alerts")
    if not st.session_state.state.alerts:
        st.write("System healthy. No active alerts.")
    else:
        for alert in st.session_state.state.alerts:
            severity = "🔴 ERROR" if alert.get('severity') == "critical" else "🟡 WARNING"
            st.warning(f"**{severity}**: {alert.get('message', 'Unknown Alert')} ({alert.get('timestamp', datetime.now()).strftime('%H:%M:%S')})")

# Bottom Row: Power Distribution
st.subheader("🔌 Power Flow Distribution")
col_a, col_b = st.columns([2, 1])

with col_a:
    labels = ["PV", "GEN", "BATT", "GRID"]
    vals = [0, 0, 0, 0]
    with st.session_state.state.lock:
        for mg_id, points in st.session_state.state.microgrid_data.items():
            if points:
                last = points[-1]
                vals[0] += last.get('pv_power_kw', last.get('pv_generation_kw', 0))
                vals[1] += last.get('generator_power_kw', 0)
                vals[2] += max(0, last.get('battery_power_kw', 0)) 
                vals[3] += abs(last.get('grid_power_kw', 0))
    
    if sum(vals) == 0:
        st.info("Waiting for live telemetry stream...")
    else:
        fig_pie = go.Figure(data=[go.Pie(labels=labels, values=vals, hole=.4, marker=dict(colors=['#f1c40f', '#e67e22', '#3498db', '#95a5a6']))])
        fig_pie.update_layout(template="plotly_dark", height=400, title="City-Wide Source Mix", margin=dict(l=0, r=0, b=0, t=40))
        st.plotly_chart(fig_pie, width='stretch')

with col_b:
    st.write("### Resource Status")
    with st.session_state.state.lock:
        if not st.session_state.state.microgrid_data:
            st.write("No microgrid data active.")
        for mg_id, points in st.session_state.state.microgrid_data.items():
            if points:
                last = points[-1]
                soc = last.get('soc', last.get('battery_soc_percent', 0))
                status = "Islanded" if last.get('is_islanded') else "Grid-tied"
                st.write(f"**{mg_id.capitalize()}** ({status})")
                st.progress(soc / 100.0, text=f"SOC: {soc:.1f}%")

st.markdown("---")
# New Section: Granular Analytics & Power Transfers
st.subheader("📊 Granular Energy Analytics")
t1, t2 = st.tabs(["Energy Exchange Ledger", "Microgrid Drill-down"])

with t1:
    st.write("#### 🏗️ City-Wide Energy Exchange Bridge")
    donors = []
    recipients = []
    with st.session_state.state.lock:
        for mg_id, points in st.session_state.state.microgrid_data.items():
            if points:
                last = points[-1]
                sharing = last.get('net_sharing_kw', 0)
                if sharing < -0.1: # Exporting
                    donors.append((mg_id, abs(sharing)))
                elif sharing > 0.1: # Importing
                    recipients.append((mg_id, sharing))
    
    if not donors and not recipients:
        st.info("🔄 **Idle Bus**: No active inter-microgrid transfers. System is in local balance.")
    else:
        # Show specific transfers
        total_transfer = sum(val for name, val in recipients)
        st.markdown(f"> **Current Inter-Microgrid Bus Loading: {total_transfer:.1f} kW**")
        
        c_don, c_rec = st.columns(2)
        with c_don:
            st.success("📤 **Energy Donors** (Surplus)")
            for name, val in donors:
                st.write(f"**{name.capitalize()}**: `{val:.1f} kW` → *To Bus*")
        with c_rec:
            st.warning("📥 **Energy Recipients** (Deficit)")
            for name, val in recipients:
                st.write(f"**{name.capitalize()}**: `{val:.1f} kW` ← *From Bus*")

with t2:
    # Convenience: Allow selection here if they haven't found the sidebar yet
    current_mg = selected_mg
    if selected_mg == "All":
        st.info("Select a microgrid to see its internal energy balance.")
        inner_mg = st.selectbox("Quick-select Microgrid", ["Hospital", "University", "Industry", "Residence"], key="inner_sel")
        current_mg = inner_mg
    
    st.write(f"#### 🔎 Local Power Composition: {current_mg}")
    with st.session_state.state.lock:
        points = st.session_state.state.microgrid_data.get(current_mg.lower())
        if not points:
            st.write(f"No live telemetry received for **{current_mg}** yet. Please ensure the simulation is running.")
        else:
            last = points[-1]
            # Breakdown for one MG
            comp_labels = ["PV Solar", "Diesel Gen", "Battery", "Main Grid / Transfer"]
            pv = last.get('pv_power_kw', last.get('pv_generation_kw', 0))
            gen = last.get('generator_power_kw', 0)
            batt = max(0, last.get('battery_power_kw', 0))
            sharing = last.get('net_sharing_kw', 0)
            grid = abs(last.get('grid_power_kw', 0))
            soc = last.get('soc', last.get('battery_soc_percent', 0)) # Ensure soc is defined
            
            # Combine grid and sharing into "Main Grid / Transfer"
            external_support = grid + max(0, sharing)
            
            comp_vals = [pv, gen, batt, external_support]
            
            # ── Asset Capacity vs Actual Generation ──
            st.write("#### 🛡️ Asset Utilization (Actual vs Rated)")
            a1, a2, a3 = st.columns(3)
            with a1:
                cap = last.get('generator_capacity_kw', 0)
                st.metric("Generator", f"{gen:.1f} kW", f"Cap: {cap} kW", delta_color="off")
            with a2:
                cap = last.get('pv_capacity_kw', 0)
                st.metric("Solar PV", f"{pv:.1f} kW", f"Cap: {cap} kW", delta_color="off")
            with a3:
                cap = last.get('battery_capacity_kwh', 0)
                st.metric("Battery", f"{soc:.1f}%", f"Cap: {cap} kWh", delta_color="off")

            st.write("#### 🍰 Local Supply Mix")
            fig_drill = go.Figure(data=[go.Pie(
                labels=comp_labels, 
                values=comp_vals, 
                hole=.5,
                marker=dict(colors=['#f1c40f', '#e67e22', '#3498db', '#95a5a6'])
            )])
            fig_drill.update_layout(template="plotly_dark", height=380, margin=dict(l=0, r=0, b=0, t=20))
            st.plotly_chart(fig_drill, width='stretch')
            
            # ── Shedding Details ──
            st.write("#### ⚖️ Resilience & Shedding Audit")
            ca, cb, cc = st.columns(3)
            
            shed_pct = last.get('load_shed_percent', 0)
            crit_shed = last.get('critical_load_shed', False)
            
            with ca:
                st.metric("Total Load Request", f"{last.get('total_load_kw', 0):.1f} kW")
            with cb:
                st.metric("Shedding Amount", f"{last.get('load_shed_kw', 0):.1f} kW", f"{shed_pct:.1f}%")
            with cc:
                if shed_pct < 0.1:
                    st.success("✅ HEALTHY: Full Power")
                elif not crit_shed:
                    # Determine why we are shedding
                    reason = "Preserving Higher-Priority Loads"
                    if current_mg.lower() == 'residential':
                        reason = "Preserving Hospital & University"
                    elif current_mg.lower() == 'industrial':
                        reason = "Preserving Hospital Critical Systems"
                    st.warning(f"⚠️ PARTIAL: {reason}")
                else:
                    st.error("🚨 CRITICAL: LIFE-SUPPORT RISK")
            
            # Support Details
            st.info(f"💡 **Support Detail**: Microgrid is currently drawing **{external_support:.1f} kW** from the City infrastructure to prevent further shedding.")

# Auto-refresh logic (Streamlit will rerun this script)
time.sleep(2)
st.rerun()
