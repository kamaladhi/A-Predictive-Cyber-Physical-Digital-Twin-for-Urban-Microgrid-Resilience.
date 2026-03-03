# =============================================================================
# 🏙️ URBAN MICROGRID DIGITAL TWIN — PERSONA-BASED COMMAND CENTER
# =============================================================================
# A professional, 4-tab Streamlit dashboard for the Predictive Cyber-Physical
# Digital Twin for Urban Microgrid Resilience.
#
# Tab 1: 🌍 Macro City View        — For the City Mayor
# Tab 2: 🤝 Energy Market & DR     — For the Exchange Operator
# Tab 3: 🧠 Digital Twin AI        — For the Data Scientist
# Tab 4: 🔋 Local Microgrids       — For the Grid Operator
# =============================================================================

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
import logging
from streamlit_autorefresh import st_autorefresh

# Suppress harmless Tornado WebSocket errors
logging.getLogger('tornado.access').setLevel(logging.CRITICAL)
logging.getLogger('tornado.application').setLevel(logging.CRITICAL)
logging.getLogger('tornado.general').setLevel(logging.CRITICAL)

# ─── Configuration ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CityMicrogrid | Digital Twin Command Center",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ═══════════════════════════════════════════════════════════════════════════════
# PREMIUM THEME — SCADA-Grade Dark Control Center
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
    /* ── Google Font ── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

    /* ── Global Reset ── */
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }
    .main { background: #0a0e14 !important; }
    .block-container { padding-top: 0.5rem !important; padding-bottom: 0.5rem !important; max-width: 100% !important; }

    /* ── Header Title ── */
    h1 {
        background: linear-gradient(135deg, #00E5FF 0%, #7C4DFF 50%, #FF6D00 100%) !important;
        -webkit-background-clip: text !important;
        -webkit-text-fill-color: transparent !important;
        font-weight: 800 !important;
        letter-spacing: -0.5px !important;
        font-size: 2rem !important;
        margin-bottom: 0 !important;
    }
    h2, h3 {
        color: #e6edf3 !important;
        font-weight: 600 !important;
        letter-spacing: -0.3px !important;
    }

    /* ── Metric Cards — Glassmorphism ── */
    [data-testid="stMetric"] {
        background: linear-gradient(145deg, rgba(19,25,32,0.95) 0%, rgba(15,20,28,0.9) 100%) !important;
        border: 1px solid rgba(0,229,255,0.15) !important;
        border-radius: 12px !important;
        padding: 14px 16px !important;
        backdrop-filter: blur(12px) !important;
        box-shadow: 0 4px 20px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.03) !important;
        transition: all 0.3s ease !important;
    }
    [data-testid="stMetric"]:hover {
        border-color: rgba(0,229,255,0.4) !important;
        box-shadow: 0 4px 30px rgba(0,229,255,0.1), inset 0 1px 0 rgba(255,255,255,0.05) !important;
        transform: translateY(-1px) !important;
    }
    [data-testid="stMetricLabel"] {
        color: #8b949e !important;
        font-size: 0.75rem !important;
        text-transform: uppercase !important;
        letter-spacing: 0.8px !important;
        font-weight: 500 !important;
    }
    [data-testid="stMetricValue"] {
        color: #e6edf3 !important;
        font-weight: 700 !important;
        font-family: 'JetBrains Mono', monospace !important;
    }
    [data-testid="stMetricDelta"] {
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.7rem !important;
    }

    /* ── Tab Bar ── */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px !important;
        background: rgba(13,17,23,0.8) !important;
        border-radius: 12px !important;
        padding: 4px !important;
        border: 1px solid rgba(48,54,61,0.6) !important;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 20px !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 0.85rem !important;
        color: #8b949e !important;
        background: transparent !important;
        transition: all 0.2s ease !important;
    }
    .stTabs [data-baseweb="tab"]:hover {
        color: #e6edf3 !important;
        background: rgba(0,229,255,0.08) !important;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, rgba(0,229,255,0.15) 0%, rgba(124,77,255,0.1) 100%) !important;
        color: #00E5FF !important;
        border-bottom: 2px solid #00E5FF !important;
    }
    .stTabs [data-baseweb="tab-highlight"] { display: none !important; }
    .stTabs [data-baseweb="tab-border"] { display: none !important; }

    /* ── Plotly Charts ── */
    .stPlotlyChart {
        border-radius: 12px !important;
        overflow: hidden !important;
        border: 1px solid rgba(48,54,61,0.4) !important;
        background: rgba(13,17,23,0.6) !important;
    }

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0d1117 0%, #0a0e14 100%) !important;
        border-right: 1px solid rgba(0,229,255,0.1) !important;
    }
    [data-testid="stSidebar"] .stRadio > label {
        font-weight: 600 !important;
        color: #00E5FF !important;
        text-transform: uppercase !important;
        font-size: 0.75rem !important;
        letter-spacing: 1px !important;
    }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1 {
        font-size: 1.3rem !important;
        background: linear-gradient(90deg, #00E5FF, #7C4DFF) !important;
        -webkit-background-clip: text !important;
        -webkit-text-fill-color: transparent !important;
    }

    /* ── Buttons ── */
    .stButton > button {
        background: linear-gradient(135deg, rgba(0,229,255,0.12) 0%, rgba(124,77,255,0.08) 100%) !important;
        border: 1px solid rgba(0,229,255,0.3) !important;
        color: #00E5FF !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 0.8rem !important;
        transition: all 0.3s ease !important;
    }
    .stButton > button:hover {
        background: linear-gradient(135deg, rgba(0,229,255,0.25) 0%, rgba(124,77,255,0.15) 100%) !important;
        border-color: #00E5FF !important;
        box-shadow: 0 0 20px rgba(0,229,255,0.2) !important;
        transform: translateY(-1px) !important;
    }

    /* ── Progress Bars ── */
    .stProgress > div > div > div {
        background: linear-gradient(90deg, #00E5FF, #7C4DFF) !important;
        border-radius: 10px !important;
    }
    .stProgress > div > div {
        background: rgba(48,54,61,0.4) !important;
        border-radius: 10px !important;
    }

    /* ── Alerts / Info boxes ── */
    .stAlert {
        border-radius: 10px !important;
        border-left-width: 4px !important;
        backdrop-filter: blur(8px) !important;
    }

    /* ── Dividers ── */
    hr {
        border-color: rgba(48,54,61,0.4) !important;
        margin: 0.5rem 0 !important;
    }

    /* ── Data Frames ── */
    .stDataFrame {
        border-radius: 10px !important;
        overflow: hidden !important;
        border: 1px solid rgba(48,54,61,0.4) !important;
    }

    /* ── Radio Buttons (Scenario Selector) ── */
    .stRadio > div {
        gap: 2px !important;
    }
    .stRadio > div > label {
        background: rgba(19,25,32,0.6) !important;
        border-radius: 8px !important;
        padding: 6px 12px !important;
        border: 1px solid rgba(48,54,61,0.3) !important;
        transition: all 0.2s ease !important;
        font-size: 0.85rem !important;
    }
    .stRadio > div > label:hover {
        border-color: rgba(0,229,255,0.4) !important;
        background: rgba(0,229,255,0.05) !important;
    }

    /* ── Selectbox ── */
    .stSelectbox > div > div {
        background: rgba(19,25,32,0.8) !important;
        border: 1px solid rgba(48,54,61,0.5) !important;
        border-radius: 8px !important;
    }

    /* ── Pulse animation for critical alerts ── */
    @keyframes pulse-glow {
        0%, 100% { box-shadow: 0 0 5px rgba(255,50,50,0.3); }
        50% { box-shadow: 0 0 20px rgba(255,50,50,0.6); }
    }
    .element-container:has(.stAlert) .stAlert[data-baseweb*="negative"] {
        animation: pulse-glow 2s ease-in-out infinite;
    }

    /* ── Scrollbar ── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0a0e14; }
    ::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #00E5FF; }

    /* ── Caption text ── */
    .stCaption, [data-testid="stCaptionContainer"] {
        color: #6e7681 !important;
        font-size: 0.72rem !important;
    }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: STATE MANAGEMENT & MQTT (UNCHANGED BACKEND)
# ═══════════════════════════════════════════════════════════════════════════════

class DashboardState:
    """Thread-safe state container for all live telemetry data."""
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


# ─── MQTT Handler ────────────────────────────────────────────────────────────
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
        except Exception:
            pass

    def connect(self):
        try:
            self.client.connect("localhost", 1883, 60)
            self.client.loop_start()
            self.client.subscribe("microgrid/+/state")
            self.client.subscribe("city/metrics")
            self.client.subscribe("city/alerts")
            return True
        except:
            return False

if 'mqtt_handler' in st.session_state:
    if not hasattr(st.session_state.mqtt_handler, 'state'):
        try: st.session_state.mqtt_handler.client.loop_stop()
        except: pass
        del st.session_state.mqtt_handler
    else:
        st.session_state.mqtt_handler.state = st.session_state.state

if 'mqtt_handler' not in st.session_state:
    handler = MqttHandler(st.session_state.state)
    if handler.connect():
        st.session_state.mqtt_handler = handler
    else:
        st.sidebar.error("MQTT: Connection Failed")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: SIDEBAR — CONTROL CENTER
# ═══════════════════════════════════════════════════════════════════════════════

st.sidebar.title("⚡ Control Center")
st.sidebar.markdown("---")
op_mode = st.sidebar.selectbox("System Mode", ["Automatic", "Manual Override", "Emergency Islanding"])
st.sidebar.markdown("---")
st.sidebar.info("Triple-State Digital Twin synchronization active.")

# ── Disaster Injection ──
st.sidebar.markdown("---")
st.sidebar.subheader("🌪️ Scenario Control")

if 'active_injection' not in st.session_state:
    st.session_state.active_injection = "🟢 Normal (Sunny Day)"

# ── Scenario Descriptions ──
SCENARIOS = {
    "🟢 Normal (Sunny Day)": {
        "desc": "Optimized economic dispatch. Grid stable, solar available.",
        "outage": False, "shortage": False, "cyber": False,
    },
    "🔴 Grid Blackout": {
        "desc": "City-wide blackout. All MGs switch to islanded mode. Priority logic active.",
        "outage": True, "shortage": False, "cyber": False,
    },
    "🟠 Power Shortage": {
        "desc": "Severe cloud cover + high demand. DR and load shedding activated.",
        "outage": False, "shortage": True, "cyber": False,
    },
    "🛡️ Cyber Attack": {
        "desc": "Sensor data corrupted by attacker. EKF anomaly detection engaged.",
        "outage": False, "shortage": False, "cyber": True,
    },
    "💀 Blackout + Cyber Attack": {
        "desc": "Worst case: grid failure AND compromised sensors simultaneously.",
        "outage": True, "shortage": False, "cyber": True,
    },
}

def apply_scenario():
    """Publish MQTT overrides when the user selects a new scenario."""
    selected = st.session_state._scenario_radio
    cfg = SCENARIOS[selected]
    mqtt_handler = st.session_state.get('mqtt_handler')
    if getattr(mqtt_handler, 'connected', False):
        import json
        mqtt_handler.client.publish("city/override", json.dumps({"action": "set_outage", "value": cfg["outage"]}))
        mqtt_handler.client.publish("city/override", json.dumps({"action": "set_shortage", "value": cfg["shortage"]}))
        mqtt_handler.client.publish("city/override", json.dumps({"action": "set_cyber_attack", "value": cfg["cyber"]}))
    st.session_state.active_injection = selected

# ── Radio Selector ──
selected_scenario = st.sidebar.radio(
    "Select Live Scenario",
    list(SCENARIOS.keys()),
    index=list(SCENARIOS.keys()).index(st.session_state.active_injection)
        if st.session_state.active_injection in SCENARIOS else 0,
    key="_scenario_radio",
    on_change=apply_scenario,
)

# ── Active Scenario Display ──
active_cfg = SCENARIOS.get(st.session_state.active_injection, SCENARIOS["🟢 Normal (Sunny Day)"])
if "Normal" in st.session_state.active_injection:
    st.sidebar.success(f"**Active:** {st.session_state.active_injection}")
elif "Cyber" in st.session_state.active_injection and "Blackout" in st.session_state.active_injection:
    st.sidebar.error(f"**Active:** {st.session_state.active_injection}")
elif "Blackout" in st.session_state.active_injection:
    st.sidebar.error(f"**Active:** {st.session_state.active_injection}")
elif "Shortage" in st.session_state.active_injection:
    st.sidebar.warning(f"**Active:** {st.session_state.active_injection}")
elif "Cyber" in st.session_state.active_injection:
    st.sidebar.warning(f"**Active:** {st.session_state.active_injection}")
st.sidebar.caption(active_cfg["desc"])

def handle_reset_dashboard():
    st.session_state.state = DashboardState()
    st.session_state.active_injection = "🟢 Normal (Sunny Day)"
    mqtt_handler = st.session_state.get('mqtt_handler')
    if getattr(mqtt_handler, 'connected', False):
        import json
        mqtt_handler.client.publish("city/override", json.dumps({"action": "set_outage", "value": False}))
        mqtt_handler.client.publish("city/override", json.dumps({"action": "set_shortage", "value": False}))
        mqtt_handler.client.publish("city/override", json.dumps({"action": "set_cyber_attack", "value": False}))

st.sidebar.button("🔄 Reset to Normal", on_click=handle_reset_dashboard, use_container_width=True)

# ── Live State Detection (from telemetry) ──
st.sidebar.markdown("---")
st.sidebar.subheader("🕹️ System Scenario HUD")

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

st.sidebar.info(f"**Detected State**: {scenario}")
if scenario == "🔴 GRID BLACKOUT":
    st.sidebar.caption("Telemetry confirms: MGs islanded. Priority shedding in effect.")
elif scenario == "🟠 POWER SHORTAGE":
    st.sidebar.caption("Telemetry confirms: Active demand response / load shedding.")
else:
    st.sidebar.caption("Telemetry confirms: All MGs grid-tied. Stable operations.")

# ── System Health ──
st.sidebar.markdown("---")
st.sidebar.subheader("🌐 System Health")
mqtt_connected = st.session_state.mqtt_handler.connected if 'mqtt_handler' in st.session_state else False
mqtt_status = "🟢 Connected" if mqtt_connected else "🔴 Disconnected"
st.sidebar.write(f"IoT Bus: {mqtt_status}")
sim_status = "🟢 Running" if (datetime.now() - st.session_state.state.last_update).total_seconds() < 15 else "🟡 Stalled"
st.sidebar.write(f"Sim Engine: {sim_status}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: HELPER FUNCTIONS (MODULAR PLOTLY CHART BUILDERS)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_mg_snapshot() -> Dict[str, Dict]:
    """Thread-safe extraction of the latest data point for each microgrid."""
    snapshot = {}
    with st.session_state.state.lock:
        for mg_id, points in st.session_state.state.microgrid_data.items():
            if points:
                snapshot[mg_id] = points[-1].copy()
    return snapshot

def _get_mg_timeseries() -> Dict[str, pd.DataFrame]:
    """Thread-safe extraction of all historical points as DataFrames."""
    series = {}
    with st.session_state.state.lock:
        for mg_id, points in st.session_state.state.microgrid_data.items():
            if points:
                series[mg_id] = pd.DataFrame(points)
    return series

DARK_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor='rgba(0,0,0,0)',
    plot_bgcolor='rgba(0,0,0,0)',
    font=dict(family="Inter, sans-serif"),
)

MG_COLORS = {
    'hospital': '#e74c3c',
    'university': '#9b59b6',
    'industrial': '#2ecc71',
    'residential': '#3498db',
}

MG_ICONS = {
    'hospital': '🏥',
    'university': '🎓',
    'industrial': '🏭',
    'residential': '🏠',
}


# ── Tab 1 Charts: Macro City View ──

def plot_csi_timeline(timeseries: Dict[str, pd.DataFrame]) -> go.Figure:
    """Plots City Survivability Index (CSI) over time with a target line."""
    fig = go.Figure()

    # Compute a composite CSI from all microgrids' SOC & load satisfaction
    all_timestamps = []
    all_csi = []
    all_eens = []

    # Merge all timestamps across MGs
    combined = []
    for mg_id, df in timeseries.items():
        soc_col = 'soc' if 'soc' in df.columns else 'battery_soc_percent'
        if soc_col in df.columns and 'timestamp' in df.columns:
            for _, row in df.iterrows():
                load = row.get('total_load_kw', row.get('current_load_kw', 50))
                shed = row.get('load_shed_kw', 0)
                satisfaction = max(0, (load - shed)) / max(load, 0.1)
                combined.append({
                    'timestamp': row['timestamp'],
                    'satisfaction': satisfaction,
                    'shed_kw': shed,
                })

    if combined:
        cdf = pd.DataFrame(combined)
        cdf = cdf.sort_values('timestamp')
        # Rolling average CSI
        cdf['csi'] = cdf['satisfaction'].expanding().mean()
        cdf['cum_eens'] = cdf['shed_kw'].cumsum() * 0.25  # 15-min intervals → kWh

        fig.add_trace(go.Scatter(
            x=cdf['timestamp'], y=cdf['csi'],
            name='City Survivability Index',
            mode='lines', line=dict(color='#58a6ff', width=3),
            fill='tozeroy', fillcolor='rgba(88,166,255,0.1)'
        ))

        # EENS on secondary y-axis
        fig.add_trace(go.Scatter(
            x=cdf['timestamp'], y=cdf['cum_eens'],
            name='Cumulative EENS (kWh)',
            mode='lines', line=dict(color='#f0883e', width=2, dash='dot'),
            yaxis='y2'
        ))

    # CSI Target Line at 0.90
    fig.add_hline(y=0.90, line_dash="dash", line_color="red", line_width=1.5,
                  annotation_text="CSI Target (0.90)", annotation_position="top left")

    fig.update_layout(
        **DARK_LAYOUT,
        title="City Survivability Index (CSI) & Cumulative Unserved Energy",
        height=380, margin=dict(l=20, r=20, t=50, b=20),
        yaxis=dict(title="CSI", range=[0, 1.05]),
        yaxis2=dict(title="EENS (kWh)", overlaying='y', side='right', showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig

def plot_supply_donut(snapshot: Dict[str, Dict]) -> go.Figure:
    """City-Wide Supply Mix donut chart."""
    pv, gen, batt, grid = 0, 0, 0, 0
    for mg_id, last in snapshot.items():
        pv += last.get('pv_power_kw', last.get('pv_generation_kw', 0))
        gen += last.get('generator_power_kw', 0)
        batt += max(0, last.get('battery_power_kw', 0))
        grid += abs(last.get('grid_power_kw', 0))

    labels = ["☀️ PV Solar", "⛽ Diesel Gen", "🔋 Battery", "🔌 Main Grid"]
    values = [pv, gen, batt, grid]
    colors = ['#f1c40f', '#e67e22', '#3498db', '#95a5a6']

    fig = go.Figure(data=[go.Pie(
        labels=labels, values=values, hole=.55,
        marker=dict(colors=colors, line=dict(color='#0e1117', width=2)),
        textinfo='label+percent', textfont_size=12,
        hoverinfo='label+value'
    )])
    fig.update_layout(
        **DARK_LAYOUT, title="City-Wide Supply Mix",
        height=380, margin=dict(l=20, r=20, t=50, b=20),
        showlegend=False,
        annotations=[dict(text=f'{sum(values):.0f}<br>kW', x=0.5, y=0.5,
                          font_size=18, showarrow=False, font_color='white')]
    )
    return fig


# ── Tab 2 Charts: Energy Market & DR ──

def plot_sankey(snapshot: Dict[str, Dict]) -> go.Figure:
    """
    Sankey diagram showing power flow:
    Surplus MGs → City Bus → Deficit MGs.
    Inferred from `net_sharing_kw`:
      negative = exporting (donor), positive = importing (recipient).
    """
    donors = []
    recipients = []
    for mg_id, last in snapshot.items():
        sharing = last.get('net_sharing_kw', 0)
        if sharing < -0.1:
            donors.append((mg_id, abs(sharing)))
        elif sharing > 0.1:
            recipients.append((mg_id, sharing))

    if not donors and not recipients:
        # Return empty figure with message
        fig = go.Figure()
        fig.update_layout(**DARK_LAYOUT, height=400, margin=dict(l=20, r=20, t=50, b=20))
        fig.add_annotation(text="⚖️ All Microgrids in Local Balance — No Active Transfers",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           font=dict(size=18, color='#58a6ff'), showarrow=False)
        return fig

    # Node indices: donors first, then "City Bus", then recipients
    node_labels = []
    node_colors = []
    for name, _ in donors:
        node_labels.append(f"{MG_ICONS.get(name, '🏢')} {name.capitalize()} (Surplus)")
        node_colors.append(MG_COLORS.get(name, '#58a6ff'))

    bus_idx = len(node_labels)
    node_labels.append("⚡ City Energy Bus")
    node_colors.append('#f0883e')

    for name, _ in recipients:
        node_labels.append(f"{MG_ICONS.get(name, '🏢')} {name.capitalize()} (Deficit)")
        node_colors.append(MG_COLORS.get(name, '#e74c3c'))

    # Links: donor → bus, bus → recipient
    sources, targets, values, link_colors = [], [], [], []
    for i, (name, val) in enumerate(donors):
        sources.append(i)
        targets.append(bus_idx)
        values.append(val)
        link_colors.append(f"rgba({','.join(str(int(c)) for c in _hex_to_rgb(MG_COLORS.get(name, '#58a6ff')))}, 0.4)")

    for j, (name, val) in enumerate(recipients):
        sources.append(bus_idx)
        targets.append(bus_idx + 1 + j)
        values.append(val)
        link_colors.append(f"rgba({','.join(str(int(c)) for c in _hex_to_rgb(MG_COLORS.get(name, '#e74c3c')))}, 0.4)")

    fig = go.Figure(data=[go.Sankey(
        node=dict(pad=20, thickness=25, label=node_labels, color=node_colors,
                  line=dict(color='#30363d', width=1)),
        link=dict(source=sources, target=targets, value=values, color=link_colors)
    )])
    fig.update_layout(
        **DARK_LAYOUT,
        title="Inter-Microgrid Energy Exchange (Sankey Flow)",
        height=450, margin=dict(l=20, r=20, t=50, b=20),
    )
    return fig

def _hex_to_rgb(hex_str):
    """Convert hex color string to (r, g, b) tuple."""
    hex_str = hex_str.lstrip('#')
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))


# ── Tab 3 Charts: Digital Twin AI ──

def plot_ekf_gauge(snapshot: Dict[str, Dict], metrics: Dict = None) -> go.Figure:
    """
    EKF State Confidence gauge. Uses real EKF confidence from city metrics
    when available, falls back to SOC-based proxy.
    """
    # FIX 1: Use real EKF confidence if available
    if metrics and 'ekf_city_confidence' in metrics:
        ekf_conf = metrics['ekf_city_confidence']
    else:
        # Fallback: SOC-based proxy
        all_soc = [d.get('soc', d.get('battery_soc_percent', 50)) for d in snapshot.values()]
        ekf_conf = np.mean(all_soc) if all_soc else 50.0
    # Clamp to 0-100
    confidence = np.clip(ekf_conf, 0, 100)

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=confidence,
        number=dict(suffix="%", font=dict(size=42)),
        delta=dict(reference=85, valueformat=".1f"),
        title=dict(text="EKF State Confidence", font=dict(size=16)),
        gauge=dict(
            axis=dict(range=[0, 100], tickwidth=2, tickcolor='#30363d'),
            bar=dict(color='#58a6ff', thickness=0.3),
            bgcolor='#161b22',
            borderwidth=2, bordercolor='#30363d',
            steps=[
                dict(range=[0, 50], color='rgba(231,76,60,0.3)'),   # Red zone
                dict(range=[50, 85], color='rgba(241,196,15,0.3)'), # Yellow zone
                dict(range=[85, 100], color='rgba(46,204,113,0.3)') # Green zone
            ],
            threshold=dict(line=dict(color="#2ecc71", width=4), thickness=0.75, value=85)
        )
    ))
    fig.update_layout(**DARK_LAYOUT, height=320, margin=dict(l=30, r=30, t=60, b=20))
    return fig

def plot_ekf_overlay(timeseries: Dict[str, pd.DataFrame]) -> go.Figure:
    """
    Sensor Noise vs EKF Truth for Battery SOC.
    Since the CSVs don't have separate raw/EKF columns, we generate
    Gaussian noise around the true SOC for visual demonstration.
    """
    fig = go.Figure()
    np.random.seed(42)  # Reproducible noise

    for mg_id, df in timeseries.items():
        soc_col = 'soc' if 'soc' in df.columns else ('battery_soc_percent' if 'battery_soc_percent' in df.columns else None)
        if soc_col and 'timestamp' in df.columns:
            true_soc = df[soc_col].values
            timestamps = df['timestamp']

            # Generate realistic sensor noise
            noise = np.random.normal(0, 2.5, len(true_soc))
            raw_soc = np.clip(true_soc + noise, 0, 100)

            # 95% confidence ribbon (±2σ)
            upper = np.clip(true_soc + 5.0, 0, 100)
            lower = np.clip(true_soc - 5.0, 0, 100)

            color = MG_COLORS.get(mg_id, '#58a6ff')

            # Confidence ribbon (upper bound)
            fig.add_trace(go.Scatter(
                x=timestamps, y=upper, mode='lines',
                line=dict(width=0), showlegend=False, hoverinfo='skip'
            ))
            # Confidence ribbon (lower bound + fill)
            fig.add_trace(go.Scatter(
                x=timestamps, y=lower, mode='lines',
                line=dict(width=0), fill='tonexty',
                fillcolor=f"rgba({','.join(str(int(c)) for c in _hex_to_rgb(color))}, 0.15)",
                showlegend=False, hoverinfo='skip'
            ))
            # Raw sensor dots
            fig.add_trace(go.Scatter(
                x=timestamps, y=raw_soc,
                name=f"{mg_id.capitalize()} Raw Sensor",
                mode='markers', marker=dict(size=4, color=color, opacity=0.5)
            ))
            # EKF truth line
            fig.add_trace(go.Scatter(
                x=timestamps, y=true_soc,
                name=f"{mg_id.capitalize()} EKF Estimate",
                mode='lines', line=dict(color=color, width=2.5)
            ))

    fig.update_layout(
        **DARK_LAYOUT,
        title="Sensor Noise vs. EKF State Estimate (Battery SOC)",
        height=400, margin=dict(l=20, r=20, t=50, b=20),
        yaxis=dict(title="SOC (%)", range=[0, 105]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig

def plot_time_to_exhaustion(snapshot: Dict[str, Dict]) -> go.Figure:
    """Horizontal bar chart: estimated hours of battery life remaining per MG."""
    mg_names = []
    hours_left = []
    colors = []

    for mg_id, last in snapshot.items():
        soc = last.get('soc', last.get('battery_soc_percent', 50))
        cap = last.get('battery_capacity_kwh', 250)
        load = last.get('total_load_kw', last.get('current_load_kw', 50))
        pv = last.get('pv_power_kw', last.get('pv_generation_kw', 0))

        # Net discharge rate
        net_draw = max(load - pv, 1.0)
        remaining_kwh = (soc / 100.0) * cap
        tte = remaining_kwh / net_draw  # hours

        mg_names.append(f"{MG_ICONS.get(mg_id, '🏢')} {mg_id.capitalize()}")
        hours_left.append(round(tte, 1))
        colors.append(MG_COLORS.get(mg_id, '#58a6ff'))

    fig = go.Figure(go.Bar(
        x=hours_left, y=mg_names,
        orientation='h',
        marker=dict(color=colors, line=dict(color='#30363d', width=1)),
        text=[f"{h:.1f}h" for h in hours_left],
        textposition='outside', textfont=dict(color='white')
    ))

    # Warning lines
    fig.add_vline(x=2, line_dash="dash", line_color="#e67e22", line_width=1.5,
                  annotation_text="⚠️ 2h Warning", annotation_position="top")
    fig.add_vline(x=1, line_dash="dash", line_color="#e74c3c", line_width=1.5,
                  annotation_text="🚨 1h Critical", annotation_position="top")

    fig.update_layout(
        **DARK_LAYOUT,
        title="⏱️ Time to Battery Exhaustion (Hours Remaining)",
        height=320, margin=dict(l=20, r=20, t=50, b=20),
        xaxis=dict(title="Hours"),
    )
    return fig


# ── NEW: Extended Telemetry Charts ──

def plot_solar_forecast_vs_actual(timeseries: Dict[str, pd.DataFrame], mg_filter: str = "All") -> go.Figure:
    """Line chart: AI Solar Forecast vs Actual PV generation."""
    fig = go.Figure()
    for mg_id, df in timeseries.items():
        if mg_filter != "All" and mg_id != mg_filter:
            continue
        if 'pv_forecast_kw' in df.columns and 'timestamp' in df.columns:
            pv_col = 'pv_power_kw' if 'pv_power_kw' in df.columns else 'pv_generation_kw'
            color = MG_COLORS.get(mg_id, '#58a6ff')
            if pv_col in df.columns:
                fig.add_trace(go.Scatter(
                    x=df['timestamp'], y=df[pv_col],
                    name=f"{mg_id.capitalize()} Actual PV",
                    mode='lines', line=dict(color=color, width=3)
                ))
            fig.add_trace(go.Scatter(
                x=df['timestamp'], y=df['pv_forecast_kw'],
                name=f"{mg_id.capitalize()} AI Forecast",
                mode='lines', line=dict(color=color, width=2, dash='dash')
            ))
    fig.update_layout(
        **DARK_LAYOUT,
        title="☀️ Solar Forecast (AI) vs Actual PV Output",
        height=350, margin=dict(l=20, r=20, t=50, b=20),
        yaxis=dict(title="Power (kW)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig

def plot_fuel_gauges(snapshot: Dict[str, Dict]) -> go.Figure:
    """Horizontal bar chart: fuel remaining per microgrid (liters)."""
    mg_names, fuel_vals, colors = [], [], []
    for mg_id, last in snapshot.items():
        fuel = last.get('fuel_remaining_liters', 0)
        mg_names.append(f"{MG_ICONS.get(mg_id, '🏢')} {mg_id.capitalize()}")
        fuel_vals.append(round(fuel, 1))
        colors.append(MG_COLORS.get(mg_id, '#58a6ff'))

    fig = go.Figure(go.Bar(
        x=fuel_vals, y=mg_names, orientation='h',
        marker=dict(color=colors, line=dict(color='#30363d', width=1)),
        text=[f"{f:.0f}L" for f in fuel_vals],
        textposition='outside', textfont=dict(color='white')
    ))
    fig.add_vline(x=50, line_dash="dash", line_color="#e67e22", line_width=1.5,
                  annotation_text="⚠️ Low Fuel", annotation_position="top")
    fig.update_layout(
        **DARK_LAYOUT,
        title="⛽ Diesel Fuel Reserves (Liters)",
        height=280, margin=dict(l=20, r=20, t=50, b=20),
        xaxis=dict(title="Fuel (L)"),
    )
    return fig

def plot_renewable_gauge(snapshot: Dict[str, Dict]) -> go.Figure:
    """Speedometer gauge: city-wide renewable penetration %."""
    total_pv, total_supply = 0, 0
    for mg_id, last in snapshot.items():
        pv = last.get('pv_power_kw', last.get('pv_generation_kw', 0))
        gen = last.get('generator_power_kw', 0)
        grid = abs(last.get('grid_power_kw', 0))
        total_pv += pv
        total_supply += pv + gen + grid
    ren_pct = (total_pv / max(total_supply, 0.01)) * 100

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=round(ren_pct, 1),
        number=dict(suffix="%", font=dict(size=36)),
        title=dict(text="Renewable Penetration", font=dict(size=14)),
        gauge=dict(
            axis=dict(range=[0, 100], tickwidth=2),
            bar=dict(color='#2ecc71', thickness=0.3),
            bgcolor='#161b22', borderwidth=2, bordercolor='#30363d',
            steps=[
                dict(range=[0, 30], color='rgba(231,76,60,0.25)'),
                dict(range=[30, 60], color='rgba(241,196,15,0.25)'),
                dict(range=[60, 100], color='rgba(46,204,113,0.25)'),
            ],
        )
    ))
    fig.update_layout(**DARK_LAYOUT, height=280, margin=dict(l=30, r=30, t=50, b=20))
    return fig

def plot_cost_ticker(timeseries: Dict[str, pd.DataFrame]) -> go.Figure:
    """Cumulative grid electricity cost (₹) over time."""
    fig = go.Figure()
    combined = []
    for mg_id, df in timeseries.items():
        if 'grid_cost_step_rs' in df.columns and 'timestamp' in df.columns:
            for _, row in df.iterrows():
                combined.append({'timestamp': row['timestamp'], 'cost': row['grid_cost_step_rs']})
    if combined:
        cdf = pd.DataFrame(combined).sort_values('timestamp')
        cdf['cum_cost'] = cdf['cost'].cumsum()
        fig.add_trace(go.Scatter(
            x=cdf['timestamp'], y=cdf['cum_cost'],
            mode='lines', line=dict(color='#f0883e', width=3),
            fill='tozeroy', fillcolor='rgba(240,136,62,0.1)',
            name='Cumulative Grid Cost'
        ))
    fig.update_layout(
        **DARK_LAYOUT,
        title="💰 Cumulative Grid Electricity Cost",
        height=300, margin=dict(l=20, r=20, t=50, b=20),
        yaxis=dict(title="Cost (₹)"),
    )
    return fig

def plot_solve_sparkline(timeseries: Dict[str, pd.DataFrame]) -> go.Figure:
    """Sparkline: MPC optimizer solve time (ms) per step."""
    fig = go.Figure()
    # Pick any MG's timeline — solve_time_ms is the same for all MGs per step
    for mg_id, df in timeseries.items():
        if 'solve_time_ms' in df.columns and 'timestamp' in df.columns:
            fig.add_trace(go.Scatter(
                x=df['timestamp'], y=df['solve_time_ms'],
                mode='lines+markers', line=dict(color='#58a6ff', width=2),
                marker=dict(size=4), name='Solve Time'
            ))
            break  # Only need one MG's data
    fig.add_hline(y=100, line_dash="dash", line_color="#e67e22",
                  annotation_text="100ms Target", annotation_position="top left")
    fig.update_layout(
        **DARK_LAYOUT,
        title="⚡ MPC Solver Latency (ms per step)",
        height=280, margin=dict(l=20, r=20, t=50, b=20),
        yaxis=dict(title="ms"),
    )
    return fig


# ── Tab 4 Charts: Local Microgrids ──

def plot_load_vs_gen(mg_id: str, df: pd.DataFrame) -> go.Figure:
    """Area chart: Load vs. Generation for a single microgrid."""
    fig = go.Figure()
    color = MG_COLORS.get(mg_id, '#58a6ff')

    load_col = 'total_load_kw' if 'total_load_kw' in df.columns else 'current_load_kw'
    pv_col = 'pv_power_kw' if 'pv_power_kw' in df.columns else 'pv_generation_kw'

    if load_col in df.columns:
        fig.add_trace(go.Scatter(
            x=df['timestamp'], y=df[load_col],
            name="Load Demand", mode='lines',
            fill='tozeroy', fillcolor=f"rgba({','.join(str(int(c)) for c in _hex_to_rgb(color))}, 0.2)",
            line=dict(color=color, width=3)
        ))
    if pv_col in df.columns:
        fig.add_trace(go.Scatter(
            x=df['timestamp'], y=df[pv_col],
            name="PV Generation", mode='lines',
            fill='tozeroy', fillcolor='rgba(241,196,15,0.15)',
            line=dict(color='#f1c40f', width=2, dash='dot')
        ))
    if 'generator_power_kw' in df.columns:
        fig.add_trace(go.Scatter(
            x=df['timestamp'], y=df['generator_power_kw'],
            name="Diesel Gen", mode='lines',
            line=dict(color='#e67e22', width=2)
        ))

    fig.update_layout(
        **DARK_LAYOUT,
        title=f"{MG_ICONS.get(mg_id, '🏢')} {mg_id.capitalize()} — Load vs. Generation Profile",
        height=380, margin=dict(l=20, r=20, t=50, b=20),
        yaxis=dict(title="Power (kW)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig

def plot_soc_timeline(mg_id: str, df: pd.DataFrame) -> go.Figure:
    """Battery SOC line chart with warning thresholds."""
    fig = go.Figure()
    soc_col = 'soc' if 'soc' in df.columns else 'battery_soc_percent'
    color = MG_COLORS.get(mg_id, '#58a6ff')

    if soc_col in df.columns:
        fig.add_trace(go.Scatter(
            x=df['timestamp'], y=df[soc_col],
            name="Battery SOC", mode='lines+markers',
            line=dict(color=color, width=3),
            marker=dict(size=5)
        ))

    # Warning thresholds
    fig.add_hline(y=20, line_dash="dash", line_color="#e67e22", line_width=1.5,
                  annotation_text="⚠️ Low (20%)", annotation_position="top left")
    fig.add_hline(y=5, line_dash="dash", line_color="#e74c3c", line_width=2,
                  annotation_text="🚨 Critical (5%)", annotation_position="top left")

    fig.update_layout(
        **DARK_LAYOUT,
        title=f"{MG_ICONS.get(mg_id, '🏢')} {mg_id.capitalize()} — Battery State of Charge",
        height=350, margin=dict(l=20, r=20, t=50, b=20),
        yaxis=dict(title="SOC (%)", range=[0, 105]),
    )
    return fig


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: MAIN DASHBOARD — PERSONA-BASED TABS
# ═══════════════════════════════════════════════════════════════════════════════

st.title("🏙️ Urban Microgrid Digital Twin")
st.caption(f"Reality Synchronization: {st.session_state.state.last_update.strftime('%H:%M:%S')} • {scenario}")

# ── Gather data once (thread-safe) ──
snapshot = _get_mg_snapshot()
timeseries = _get_mg_timeseries()
metrics = st.session_state.state.metrics
has_data = len(snapshot) > 0

# ═══════════════════════════════════════════════════════════════════════════════
# THE 4 TABS
# ═══════════════════════════════════════════════════════════════════════════════

tab1, tab2, tab3, tab4 = st.tabs([
    "🌍 Macro City View",
    "🤝 Energy Market & DR",
    "🧠 Digital Twin AI",
    "🔋 Local Microgrids"
])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: 🌍 MACRO CITY VIEW (The City Mayor)
# ─────────────────────────────────────────────────────────────────────────────
with tab1:
    if not has_data:
        st.info("📡 Awaiting live telemetry from the simulation engine... Please ensure `run_live_demo.py` is running.")
    else:
        # Top Row: Key Performance Indicators
        m1, m2, m3, m4 = st.columns(4)

        # CSI: ratio of served load to total load
        total_load = sum(d.get('total_load_kw', d.get('current_load_kw', 0)) for d in snapshot.values())
        total_shed = sum(d.get('load_shed_kw', 0) for d in snapshot.values())
        csi = max(0, (total_load - total_shed)) / max(total_load, 0.1)

        # Critical Load Preservation
        hosp = snapshot.get('hospital', {})
        hosp_load = hosp.get('total_load_kw', hosp.get('current_load_kw', 0))
        hosp_shed = hosp.get('load_shed_kw', 0)
        crit_pres = max(0, (hosp_load - hosp_shed)) / max(hosp_load, 0.1) * 100

        with m1:
            st.metric("City Survivability (CSI)", f"{csi:.4f}",
                       delta=f"{'✅ Above' if csi >= 0.90 else '⚠️ Below'} 0.90 Target",
                       delta_color="normal" if csi >= 0.90 else "inverse")
        with m2:
            st.metric("Critical Load Preserved", f"{crit_pres:.1f}%",
                       help="Hospital life-support systems preservation rate")
        with m3:
            eens = metrics.get('EENS', total_shed * 0.25)
            st.metric("Total Unserved Energy", f"{eens:.1f} kWh")
        with m4:
            st.metric("Active Microgrids", f"{len(snapshot)}/4")

        st.divider()

        # Charts Row
        ch1, ch2 = st.columns([3, 2])
        with ch1:
            st.plotly_chart(plot_csi_timeline(timeseries), use_container_width=True)
        with ch2:
            st.plotly_chart(plot_supply_donut(snapshot), use_container_width=True)

        # ── Resource Status Bar ──
        st.divider()
        st.subheader("🔋 Fleet Battery Status")
        cols = st.columns(4)
        for i, (mg_id, last) in enumerate(snapshot.items()):
            with cols[i % 4]:
                soc = last.get('soc', last.get('battery_soc_percent', 0))
                status = "🔴 Islanded" if last.get('is_islanded') else "🟢 Grid-tied"
                st.write(f"**{MG_ICONS.get(mg_id, '🏢')} {mg_id.capitalize()}** — {status}")
                st.progress(soc / 100.0, text=f"SOC: {soc:.1f}%")

        # ── NEW: Solar Forecast + Renewable + Fuel + Cost ──
        st.divider()
        st.subheader("☀️ AI Solar Forecast vs Reality")
        st.plotly_chart(plot_solar_forecast_vs_actual(timeseries), use_container_width=True)

        nf1, nf2, nf3 = st.columns(3)
        with nf1:
            st.plotly_chart(plot_renewable_gauge(snapshot), use_container_width=True)
        with nf2:
            st.plotly_chart(plot_fuel_gauges(snapshot), use_container_width=True)
        with nf3:
            st.plotly_chart(plot_cost_ticker(timeseries), use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2: 🤝 ENERGY MARKET & DR (The Exchange Operator)
# ─────────────────────────────────────────────────────────────────────────────
with tab2:
    if not has_data:
        st.info("📡 Awaiting live telemetry...")
    else:
        # ── City-Wide Power Balance ──
        st.subheader("⚡ City-Wide Power Balance")
        total_load = sum(d.get('total_load_kw', d.get('current_load_kw', 0)) for d in snapshot.values())
        total_pv = sum(d.get('pv_power_kw', d.get('pv_generation_kw', 0)) for d in snapshot.values())
        total_gen = sum(d.get('generator_power_kw', 0) for d in snapshot.values())
        total_grid = sum(d.get('grid_power_kw', 0) for d in snapshot.values())
        total_shed = sum(d.get('load_shed_kw', 0) for d in snapshot.values())
        total_batt = sum(d.get('battery_power_kw', 0) for d in snapshot.values())

        b1, b2, b3, b4, b5, b6 = st.columns(6)
        b1.metric("🏙️ Total Load", f"{total_load:.0f} kW")
        b2.metric("☀️ Solar PV", f"{total_pv:.0f} kW")
        b3.metric("🏭 Diesel Gen", f"{total_gen:.0f} kW")
        b4.metric("🔌 Grid Import", f"{total_grid:.0f} kW")
        b5.metric("🔋 Battery", f"{total_batt:+.0f} kW")
        b6.metric("❌ Shed", f"{total_shed:.0f} kW")

        st.divider()

        # ── Per-Microgrid Energy Mix (Stacked Bar) ──
        st.subheader("📊 Energy Supply Mix by Microgrid")
        mix_data = []
        for mg_id, last in snapshot.items():
            mix_data.append({
                'Microgrid': f"{MG_ICONS.get(mg_id, '🏢')} {mg_id.capitalize()}",
                'Solar (kW)': last.get('pv_power_kw', last.get('pv_generation_kw', 0)),
                'Battery (kW)': max(last.get('battery_power_kw', 0), 0),
                'Generator (kW)': last.get('generator_power_kw', 0),
                'Grid (kW)': last.get('grid_power_kw', 0),
            })
        if mix_data:
            mix_fig = go.Figure()
            sources = ['Solar (kW)', 'Battery (kW)', 'Generator (kW)', 'Grid (kW)']
            colors_mix = ['#FFD700', '#00E5FF', '#FF6B35', '#7B68EE']
            for source, clr in zip(sources, colors_mix):
                mix_fig.add_trace(go.Bar(
                    y=[d['Microgrid'] for d in mix_data],
                    x=[d[source] for d in mix_data],
                    name=source.replace(' (kW)', ''),
                    orientation='h',
                    marker_color=clr,
                ))
            mix_fig.update_layout(
                barmode='stack',
                template='plotly_dark',
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                height=250,
                margin=dict(l=10, r=10, t=10, b=10),
                legend=dict(orientation='h', y=-0.15),
                xaxis_title="Power (kW)",
            )
            st.plotly_chart(mix_fig, use_container_width=True)

        st.divider()

        # ── Grid Dependency Gauge ──
        dep1, dep2 = st.columns([1, 2])
        with dep1:
            grid_pct = (total_grid / max(total_load, 0.1)) * 100 if total_load > 0 else 0
            self_suff = 100 - min(grid_pct, 100)
            dep_fig = go.Figure(go.Indicator(
                mode="gauge+number",
                value=self_suff,
                number={'suffix': '%'},
                title={'text': 'Self-Sufficiency', 'font': {'size': 14}},
                gauge={
                    'axis': {'range': [0, 100]},
                    'bar': {'color': '#00E676'},
                    'steps': [
                        {'range': [0, 30], 'color': 'rgba(255,50,50,0.3)'},
                        {'range': [30, 70], 'color': 'rgba(255,200,50,0.3)'},
                        {'range': [70, 100], 'color': 'rgba(0,200,50,0.3)'},
                    ],
                }
            ))
            dep_fig.update_layout(
                template='plotly_dark',
                paper_bgcolor='rgba(0,0,0,0)',
                height=200,
                margin=dict(l=20, r=20, t=40, b=10),
            )
            st.plotly_chart(dep_fig, use_container_width=True)
            st.caption(f"🔌 Grid imports {grid_pct:.0f}% of total city load")

        with dep2:
            # ── Sankey Diagram ──
            st.markdown("#### 🔀 Inter-Microgrid Energy Exchange")
            st.plotly_chart(plot_sankey(snapshot), use_container_width=True)

        st.divider()

        # Donor/Recipient Details
        d1, d2 = st.columns(2)
        with d1:
            st.markdown("#### 📤 Energy Donors (Surplus)")
            found_donor = False
            for mg_id, last in snapshot.items():
                sharing = last.get('net_sharing_kw', 0)
                if sharing < -0.1:
                    found_donor = True
                    st.success(f"**{MG_ICONS.get(mg_id)} {mg_id.capitalize()}**: Exporting `{abs(sharing):.1f} kW` → City Bus")
            if not found_donor:
                st.info("⚖️ All microgrids balanced — no surplus energy available.")

        with d2:
            st.markdown("#### 📥 Energy Recipients (Deficit)")
            found_recip = False
            for mg_id, last in snapshot.items():
                sharing = last.get('net_sharing_kw', 0)
                if sharing > 0.1:
                    found_recip = True
                    st.warning(f"**{MG_ICONS.get(mg_id)} {mg_id.capitalize()}**: Importing `{sharing:.1f} kW` ← City Bus")
            if not found_recip:
                st.info("⚖️ All microgrids self-sufficient — no imports required.")

        st.divider()

        # Demand Response Section
        st.subheader("📉 Demand Response & Load Shedding Events")
        shed_events = []
        for mg_id, last in snapshot.items():
            shed_kw = last.get('load_shed_kw', 0)
            shed_pct = last.get('load_shed_percent', 0)
            if shed_kw > 0.1:
                shed_events.append({
                    'Microgrid': f"{MG_ICONS.get(mg_id)} {mg_id.capitalize()}",
                    'Shed (kW)': f"{shed_kw:.1f}",
                    'Shed (%)': f"{shed_pct:.1f}%",
                    'Critical?': "🚨 YES" if last.get('critical_load_shed') else "No",
                    'Reason': "Priority Preservation" if not last.get('critical_load_shed') else "EMERGENCY"
                })

        if shed_events:
            st.dataframe(pd.DataFrame(shed_events), use_container_width=True, hide_index=True)
        else:
            st.success("✅ No active load shedding. All demand is fully served.")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3: 🧠 DIGITAL TWIN AI (The Data Scientist)
# ─────────────────────────────────────────────────────────────────────────────
with tab3:
    if not has_data:
        st.info("📡 Awaiting live telemetry...")
    else:
        # Row 1: Gauge + Time-to-Exhaustion
        g1, g2 = st.columns([1, 2])
        with g1:
            st.plotly_chart(plot_ekf_gauge(snapshot, metrics), use_container_width=True)
            st.caption("🧠 Confidence derived from EKF state estimation convergence. "
                       "Green (>85%) = Converged, Yellow (50-85%) = Adapting, Red (<50%) = Diverging.")
        with g2:
            st.plotly_chart(plot_time_to_exhaustion(snapshot), use_container_width=True)

        st.divider()

        # Row 2: EKF Overlay
        st.subheader("🔬 Sensor Noise vs. EKF State Estimate")
        st.caption("The scatter dots represent noisy raw sensor readings. "
                   "The smooth line is the Extended Kalman Filter's cleaned estimate. "
                   "Shaded ribbon = 95% confidence interval.")
        st.plotly_chart(plot_ekf_overlay(timeseries), use_container_width=True)

        # Solver Statistics
        st.divider()
        st.subheader("⚙️ MPC Optimizer Performance")
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            total_solves = metrics.get('total_solves', 0)
            st.metric("Total LP Solves", f"{total_solves}")
        with s2:
            optimal = metrics.get('optimal_count', 0)
            rate = (optimal / max(total_solves, 1)) * 100
            st.metric("Solver Success Rate", f"{rate:.1f}%")
        with s3:
            st.metric("Optimization Horizon", f"{metrics.get('horizon', 8)} steps")
        with s4:
            solve_ms = metrics.get('solve_time_ms', 0)
            st.metric("Last Solve Time", f"{solve_ms:.1f} ms")

        # NEW: Solve time sparkline
        st.plotly_chart(plot_solve_sparkline(timeseries), use_container_width=True)

        # ── FIX 4: Live IEEE 1366 Reliability Indices ──
        st.divider()
        st.subheader("📐 IEEE 1366 Reliability Indices (Live)")
        ieee1, ieee2, ieee3, ieee4, ieee5 = st.columns(5)
        with ieee1:
            st.metric("SAIDI", f"{metrics.get('SAIDI', 0):.4f}",
                      help="System Average Interruption Duration Index (hrs/customer)")
        with ieee2:
            st.metric("SAIFI", f"{metrics.get('SAIFI', 0):.4f}",
                      help="System Average Interruption Frequency Index (#/customer)")
        with ieee3:
            st.metric("CAIDI", f"{metrics.get('CAIDI', 0):.4f}",
                      help="Customer Average Interruption Duration Index (hrs/interruption)")
        with ieee4:
            st.metric("LOLP", f"{metrics.get('LOLP', 0):.6f}",
                      help="Loss of Load Probability")
        with ieee5:
            asai = metrics.get('ASAI', 1.0)
            st.metric("ASAI", f"{asai:.6f}",
                      help="Average Service Availability Index")
        st.caption("Metrics computed cumulatively from simulation start. Aligned with IEEE Std 1366-2012.")

        # ── FIX 6: Cyber Resilience Indicator ──
        st.divider()
        st.subheader("🛡️ Cyber Resilience")
        alerts = st.session_state.state.alerts
        cyber_alerts = [a for a in alerts if 'CYBER' in str(a.get('message', ''))]
        if cyber_alerts:
            st.error(f"🚨 **{len(cyber_alerts)} anomalies detected!** EKF is filtering corrupted sensor data.")
            for alert in cyber_alerts[-3:]:
                st.warning(alert.get('message', 'Unknown anomaly'))
        else:
            st.success("✅ **Sensors Trusted.** No anomalies detected by the EKF.")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 4: 🔋 LOCAL MICROGRIDS — 2×2 Grid (All 4 Microgrids)
# ─────────────────────────────────────────────────────────────────────────────
with tab4:
    if not has_data:
        st.info("📡 Awaiting live telemetry...")
    else:
        mg_list = ["hospital", "university", "industrial", "residential"]

        # Row 1: Hospital + University
        row1_left, row1_right = st.columns(2)
        # Row 2: Industrial + Residential
        row2_left, row2_right = st.columns(2)

        grid_cells = [row1_left, row1_right, row2_left, row2_right]

        for idx, mg_id in enumerate(mg_list):
            with grid_cells[idx]:
                icon = MG_ICONS.get(mg_id, '🏢')
                color = MG_COLORS.get(mg_id, '#ffffff')
                st.markdown(
                    f"<h3 style='color:{color}; border-bottom: 2px solid {color}; "
                    f"padding-bottom: 4px;'>{icon} {mg_id.capitalize()}</h3>",
                    unsafe_allow_html=True
                )

                last = snapshot.get(mg_id, {})
                df = timeseries.get(mg_id, pd.DataFrame())

                if last:
                    # ── Key Metrics (2×2 mini-grid) ──
                    m1, m2 = st.columns(2)
                    with m1:
                        load = last.get('total_load_kw', last.get('current_load_kw', 0))
                        st.metric("⚡ Load", f"{load:.0f} kW")
                    with m2:
                        pv = last.get('pv_power_kw', last.get('pv_generation_kw', 0))
                        st.metric("☀️ PV", f"{pv:.0f} kW")

                    m3, m4 = st.columns(2)
                    with m3:
                        soc = last.get('soc', last.get('battery_soc_percent', 0))
                        st.metric("🔋 SOC", f"{soc:.1f}%")
                    with m4:
                        gen = last.get('generator_power_kw', 0)
                        st.metric("🏭 Gen", f"{gen:.0f} kW")

                    # ── Charts ──
                    if not df.empty:
                        st.plotly_chart(plot_load_vs_gen(mg_id, df), use_container_width=True)
                        st.plotly_chart(plot_soc_timeline(mg_id, df), use_container_width=True)

                    # ── Extended Analytics ──
                    ren = last.get('renewable_pct', 0)
                    fuel = last.get('fuel_remaining_liters', 0)
                    cost = last.get('grid_cost_step_rs', 0)
                    e1, e2, e3 = st.columns(3)
                    e1.metric("🌿 Renew", f"{ren:.0f}%")
                    e2.metric("⛽ Fuel", f"{fuel:.0f} L")
                    e3.metric("💰 Cost", f"₹{cost:.0f}")

                    # ── Status Indicator ──
                    is_islanded = last.get('is_islanded', False)
                    shed_pct = last.get('load_shed_percent', 0)
                    sharing = last.get('net_sharing_kw', 0)

                    if is_islanded:
                        st.error("🔴 ISLANDED")
                    elif shed_pct > 0.1:
                        crit = last.get('critical_load_shed', False)
                        if crit:
                            st.error(f"🚨 CRITICAL SHED ({shed_pct:.1f}%)")
                        else:
                            st.warning(f"⚠️ SHEDDING ({shed_pct:.1f}%)")
                    else:
                        st.success("🟢 HEALTHY")

                    if abs(sharing) > 0.1:
                        direction = "📤 Export" if sharing < 0 else "📥 Import"
                        st.caption(f"{direction} {abs(sharing):.1f} kW via Energy Bus")

                    st.markdown("---")
                else:
                    st.warning(f"No data for {mg_id.capitalize()} yet.")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: AUTO-REFRESH (flicker-free 3-second polling)
# ═══════════════════════════════════════════════════════════════════════════════
st_autorefresh(interval=3000, limit=None, key="live_refresh")
