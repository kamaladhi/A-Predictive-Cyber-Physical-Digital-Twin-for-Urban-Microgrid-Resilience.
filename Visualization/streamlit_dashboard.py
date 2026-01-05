import json
import time
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

LOG_DIR = Path(__file__).parent.parent / "digital_twin_logs"
DATA_FUSION_PATH = LOG_DIR / "data_fusion.csv"
ALERTS_PATH = LOG_DIR / "dr_alerts.json"

st.set_page_config(page_title="Digital Twin Live Dashboard", layout="wide")
st.title("Digital Twin Live Dashboard")
st.caption("Person 3 deliverables: MQTT + data fusion + DR alerts + logging")

refresh_seconds = st.sidebar.slider("Auto-refresh (seconds)", 2, 30, 5, step=1)
st.sidebar.write(f"Log directory: {LOG_DIR}")

@st.cache_data(ttl=5)
def load_fusion():
    if not DATA_FUSION_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(DATA_FUSION_PATH, parse_dates=["timestamp"])
    df = df.sort_values("timestamp")
    return df

@st.cache_data(ttl=5)
def load_alerts():
    if not ALERTS_PATH.exists():
        return pd.DataFrame()
    with open(ALERTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    df = pd.DataFrame(data)
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")
    return df

fusion_df = load_fusion()
alerts_df = load_alerts()

if fusion_df.empty and alerts_df.empty:
    st.warning("No log data found yet. Run the demo or the live system to generate logs.")
    st.stop()

colA, colB, colC, colD = st.columns(4)
if not fusion_df.empty:
    latest = fusion_df.iloc[-1]
    colA.metric("Measured Load (kW)", f"{latest['measured_load_kw']:.1f}")
    colB.metric("Forecast 1h (kW)", f"{latest['forecasted_1h_kw']:.1f}")
    colC.metric("Critical Load (kW)", f"{latest['critical_load_kw']:.1f}")
    colD.metric("Confidence", f"{latest['overall_confidence']*100:.1f}%")
else:
    colA.metric("Measured Load (kW)", "-")
    colB.metric("Forecast 1h (kW)", "-")
    colC.metric("Critical Load (kW)", "-")
    colD.metric("Confidence", "-")

st.markdown("---")

if not fusion_df.empty:
    st.subheader("Load vs Forecast")
    plot_df = fusion_df.rename(columns={
        "measured_load_kw": "Measured Load",
        "forecasted_1h_kw": "Forecast 1h",
        "critical_load_kw": "Critical Load",
        "non_critical_load_kw": "Non Critical Load"
    })
    fig = px.line(
        plot_df,
        x="timestamp",
        y=["Measured Load", "Forecast 1h", "Critical Load"],
        markers=True,
        labels={"timestamp": "Time", "value": "kW", "variable": "Series"},
        title="Measured vs Forecast with Critical Threshold"
    )
    fig.update_layout(legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Confidence and Peak Flags")
    conf_fig = px.scatter(
        fusion_df,
        x="timestamp",
        y="overall_confidence",
        color="peak_predicted",
        labels={"overall_confidence": "Overall Confidence", "peak_predicted": "Peak Predicted"},
        title="Confidence over time (color shows peak_predicted)"
    )
    conf_fig.update_yaxes(range=[0, 1])
    st.plotly_chart(conf_fig, use_container_width=True)

st.markdown("---")

if not alerts_df.empty:
    st.subheader("Active and Recent Alerts")
    severity_order = ["EMERGENCY", "CRITICAL", "WARNING", "INFO"]
    alerts_df["severity"] = pd.Categorical(alerts_df["severity"], categories=severity_order, ordered=True)
    alerts_df = alerts_df.sort_values(["severity", "timestamp"], ascending=[True, False])

    recent_hours = st.slider("Show alerts from last N hours", 1, 48, 24)
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(hours=recent_hours)
    scoped_alerts = alerts_df[alerts_df["timestamp"] >= cutoff]

    color_map = {
        "EMERGENCY": "#d62728",
        "CRITICAL": "#ff7f0e",
        "WARNING": "#bcbd22",
        "INFO": "#1f77b4",
    }
    if not scoped_alerts.empty:
        scatter = px.scatter(
            scoped_alerts,
            x="timestamp",
            y="severity",
            color="severity",
            color_discrete_map=color_map,
            size=[12 for _ in scoped_alerts.index],
            hover_data=["title", "message", "recommended_action", "estimated_cost_usd", "potential_savings_usd"],
            title="Alert timeline (color = severity)"
        )
        st.plotly_chart(scatter, use_container_width=True)

        st.dataframe(
            scoped_alerts[
                [
                    "timestamp",
                    "microgrid_id",
                    "alert_type",
                    "severity",
                    "title",
                    "message",
                    "recommended_action",
                    "estimated_cost_usd",
                    "potential_savings_usd"
                ]
            ].sort_values("timestamp", ascending=False),
            use_container_width=True,
            height=400
        )
    else:
        st.info("No alerts in selected window.")
else:
    st.info("No alerts logged yet.")

st.markdown("---")

st.caption(
    "Live view reads from digital_twin_logs. Run the demo or live system to refresh data."
)

if st.sidebar.checkbox("Auto-refresh", value=True):
    time.sleep(refresh_seconds)
    st.experimental_rerun()
