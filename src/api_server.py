"""
=============================================================================
DIGITAL TWIN REST API SERVER (FastAPI)
=============================================================================

Lightweight REST layer for programmatic access to the Digital Twin.
Runs alongside the MQTT broker and Streamlit dashboard.

Endpoints:
    GET  /api/v1/state      → Current microgrid snapshot (JSON)
    GET  /api/v1/metrics     → City-level metrics (ASAI, IEEE 1366, EKF)
    POST /api/v1/scenario    → Inject a disaster scenario
    GET  /api/v1/health      → Service health check

Launch:
    uvicorn src.api_server:app --host 0.0.0.0 --port 8000 --reload
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ─── App Instance ────────────────────────────────────────────────────────────
app = FastAPI(
    title="Digital Twin Microgrid API",
    description="REST interface for the Predictive Cyber-Physical Digital Twin",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Shared State (populated by MQTT subscriber) ────────────────────────────
# This dict is updated by a background MQTT listener thread.
_api_state: Dict[str, Any] = {
    "microgrids": {},
    "city_metrics": {},
    "alerts": [],
    "last_update": None,
}

_mqtt_client = None


def _get_mqtt_client():
    """Lazy-initialize a shared MQTT client for publishing overrides."""
    global _mqtt_client
    if _mqtt_client is None:
        try:
            import paho.mqtt.client as mqtt
            _mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            _mqtt_client.connect("localhost", 1883, 60)
            _mqtt_client.loop_start()

            # Subscribe to state updates to populate _api_state
            def on_message(client, userdata, msg):
                try:
                    payload = json.loads(msg.payload.decode())
                    topic = msg.topic
                    if "state" in topic:
                        mg_id = topic.split('/')[1]
                        _api_state["microgrids"][mg_id] = payload
                        _api_state["last_update"] = datetime.now().isoformat()
                    elif "metrics" in topic:
                        _api_state["city_metrics"].update(payload)
                    elif "alerts" in topic:
                        _api_state["alerts"].insert(0, payload)
                        if len(_api_state["alerts"]) > 20:
                            _api_state["alerts"].pop()
                except Exception:
                    pass

            _mqtt_client.on_message = on_message
            _mqtt_client.subscribe("microgrid/+/state")
            _mqtt_client.subscribe("city/metrics")
            _mqtt_client.subscribe("city/alerts")
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
    return _mqtt_client


# ─── Pydantic Models ────────────────────────────────────────────────────────

class ScenarioRequest(BaseModel):
    action: str  # "set_outage", "set_shortage", "set_cyber_attack"
    value: bool = True


class HealthResponse(BaseModel):
    status: str
    mqtt_connected: bool
    last_update: Optional[str]
    active_microgrids: int


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """Initialize MQTT listener on server startup."""
    _get_mqtt_client()
    logger.info("Digital Twin API started. MQTT listener active.")


@app.get("/api/v1/health", response_model=HealthResponse)
async def health():
    """Service health check."""
    client = _get_mqtt_client()
    return HealthResponse(
        status="healthy",
        mqtt_connected=client is not None,
        last_update=_api_state.get("last_update"),
        active_microgrids=len(_api_state.get("microgrids", {})),
    )


@app.get("/api/v1/state")
async def get_state():
    """
    Returns the latest snapshot of all microgrid states.

    Response includes per-microgrid telemetry: SOC, load, PV, generator,
    grid power, shedding status, fuel levels, and more.
    """
    if not _api_state["microgrids"]:
        raise HTTPException(status_code=503, detail="No telemetry data available yet")
    return {
        "timestamp": _api_state.get("last_update"),
        "microgrids": _api_state["microgrids"],
    }


@app.get("/api/v1/metrics")
async def get_metrics():
    """
    Returns city-level metrics including:
    - ASAI, EENS (reliability)
    - SAIDI, SAIFI, CAIDI, LOLP (IEEE 1366)
    - EKF confidence score
    - Solver statistics
    """
    if not _api_state["city_metrics"]:
        raise HTTPException(status_code=503, detail="No metrics available yet")
    return _api_state["city_metrics"]


@app.get("/api/v1/alerts")
async def get_alerts():
    """Returns the latest system alerts (max 20)."""
    return {"alerts": _api_state.get("alerts", [])}


@app.post("/api/v1/scenario")
async def inject_scenario(req: ScenarioRequest):
    """
    Inject a disaster scenario into the running simulation.

    Valid actions:
    - `set_outage`: Simulate grid blackout (true/false)
    - `set_shortage`: Simulate cloud cover / capacity shortage (true/false)
    - `set_cyber_attack`: Inject cyber-attack on sensor readings (true/false)
    """
    valid_actions = {"set_outage", "set_shortage", "set_cyber_attack"}
    if req.action not in valid_actions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action '{req.action}'. Valid: {valid_actions}"
        )

    client = _get_mqtt_client()
    if client is None:
        raise HTTPException(status_code=503, detail="MQTT not connected")

    payload = json.dumps({"action": req.action, "value": req.value})
    client.publish("city/override", payload)

    return {
        "status": "ok",
        "message": f"Scenario '{req.action}' set to {req.value}",
    }
