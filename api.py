from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import json
import asyncio
import logging

logger = logging.getLogger(__name__)

# Store active WebSocket connections
active_connections: List[WebSocket] = []

def create_api_server(state_sync, dr_engine):
    """
    Create FastAPI server with state_sync and dr_engine access
    """
    app = FastAPI(
        title="Digital Twin Core API",
        description="Real-time microgrid state and demand response API",
        version="1.0.0"
    )
    
    # Enable CORS for dashboard access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, specify exact origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    @app.get("/")
    async def root():
        """Health check endpoint"""
        return {
            "status": "running",
            "service": "Digital Twin Core API",
            "version": "1.0.0"
        }
    
    @app.get("/api/v1/state")
    async def get_current_state():
        """Get current microgrid state"""
        try:
            state = state_sync.get_current_state()
            return state.to_dict()
        except Exception as e:
            logger.error(f"Error getting state: {e}")
            return {"error": str(e)}, 500
    
    @app.get("/api/v1/state/summary")
    async def get_state_summary():
        """Get simplified state summary"""
        try:
            return state_sync.get_state_summary()
        except Exception as e:
            logger.error(f"Error getting summary: {e}")
            return {"error": str(e)}, 500
    
    @app.get("/api/v1/metrics")
    async def get_metrics():
        """Get performance metrics"""
        try:
            state = state_sync.get_current_state()
            return {
                "timestamp": state.timestamp.isoformat(),
                "self_consumption_ratio": state.metrics.self_consumption_ratio,
                "renewable_penetration": state.metrics.renewable_penetration,
                "grid_independence": state.metrics.grid_independence,
                "cost_per_kwh": state.metrics.cost_per_kwh,
                "data_quality": state.is_healthy
            }
        except Exception as e:
            logger.error(f"Error getting metrics: {e}")
            return {"error": str(e)}, 500
    
    @app.get("/api/v1/alerts")
    async def get_active_alerts():
        """Get active DR alerts"""
        try:
            return {
                "alerts": [alert.to_dict() for alert in dr_engine.active_alerts],
                "count": len(dr_engine.active_alerts)
            }
        except Exception as e:
            logger.error(f"Error getting alerts: {e}")
            return {"error": str(e)}, 500
    
    @app.get("/api/v1/appliances")
    async def get_appliances():
        """Get all appliance states"""
        try:
            state = state_sync.get_current_state()
            return {
                "timestamp": state.timestamp.isoformat(),
                "total_load": state.total_load,
                "appliances": {
                    name: app.to_dict()
                    for name, app in state.appliances.items()
                }
            }
        except Exception as e:
            logger.error(f"Error getting appliances: {e}")
            return {"error": str(e)}, 500
    
    @app.get("/api/v1/generation")
    async def get_generation():
        """Get generation data"""
        try:
            state = state_sync.get_current_state()
            return {
                "timestamp": state.timestamp.isoformat(),
                "solar": state.generation.solar,
                "wind": state.generation.wind,
                "fuel_cell": state.generation.fuel_cell,
                "total": state.generation.total
            }
        except Exception as e:
            logger.error(f"Error getting generation: {e}")
            return {"error": str(e)}, 500
    
    @app.get("/api/v1/battery")
    async def get_battery():
        """Get battery state"""
        try:
            state = state_sync.get_current_state()
            return {
                "timestamp": state.timestamp.isoformat(),
                "soc": state.battery.soc,
                "energy_stored": state.battery.energy_stored,
                "charging_power": state.battery.charging_power,
                "discharging_power": state.battery.discharging_power,
                "is_charging": state.battery.is_charging,
                "is_discharging": state.battery.is_discharging
            }
        except Exception as e:
            logger.error(f"Error getting battery: {e}")
            return {"error": str(e)}, 500
    
    @app.get("/api/v1/forecast")
    async def get_forecast():
        """Get forecast data"""
        try:
            state = state_sync.get_current_state()
            if not state.forecast:
                return {"message": "No forecast data available"}
            
            return {
                "timestamp": state.forecast.timestamp.isoformat(),
                "solar_forecast": state.forecast.solar_forecast,
                "wind_forecast": state.forecast.wind_forecast,
                "price_forecast": state.forecast.price_forecast,
                "peak_price_time": state.forecast.get_peak_price_time().isoformat() if state.forecast.get_peak_price_time() else None,
                "peak_price_value": state.forecast.get_peak_price_value()
            }
        except Exception as e:
            logger.error(f"Error getting forecast: {e}")
            return {"error": str(e)}, 500
    
    @app.websocket("/ws/realtime")
    async def websocket_endpoint(websocket: WebSocket):
        """
        WebSocket for real-time state streaming
        Person 4 can connect here for live updates
        """
        await websocket.accept()
        active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Total: {len(active_connections)}")
        
        try:
            while True:
                # Send current state every 2 seconds
                state = state_sync.get_current_state()
                await websocket.send_json(state.to_dict())
                await asyncio.sleep(2)
        
        except WebSocketDisconnect:
            active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Total: {len(active_connections)}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            if websocket in active_connections:
                active_connections.remove(websocket)
    
    @app.post("/api/v1/battery/update")
    async def update_battery_manual(soc: float, charging_power: float = 0.0, discharging_power: float = 0.0):
        """Manual battery update (for testing)"""
        try:
            state_sync.update_battery(soc, charging_power, discharging_power)
            return {"message": "Battery updated successfully"}
        except Exception as e:
            logger.error(f"Error updating battery: {e}")
            return {"error": str(e)}, 500
    
    return app


# Utility function to broadcast state updates via WebSocket
async def broadcast_state_update(state_dict: dict):
    """Broadcast state update to all connected WebSocket clients"""
    if active_connections:
        disconnected = []
        for connection in active_connections:
            try:
                await connection.send_json(state_dict)
            except Exception as e:
                logger.error(f"Error broadcasting to WebSocket: {e}")
                disconnected.append(connection)
        
        # Remove disconnected clients
        for conn in disconnected:
            active_connections.remove(conn)

