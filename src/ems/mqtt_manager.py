from __future__ import annotations
import json
import logging
import time
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Callable
import paho.mqtt.client as mqtt
import numpy as np

logger = logging.getLogger(__name__)

class MqttNode:
    """
    Base MQTT node for the Digital Twin microgrid system.
    Handles connection lifecycle and common messaging patterns.
    """
    def __init__(self, client_id: str, broker: str = "localhost", port: int = 1883):
        self.client_id = client_id
        self.broker = broker
        self.port = port
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        
        # Callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        
        self.is_connected = False
        self.subscriptions: Dict[str, List[Callable]] = {}

    def connect(self, timeout: int = 10):
        """Connect to the broker and start the loop."""
        try:
            logger.info(f"MQTT: Connecting to {self.broker}:{self.port} as {self.client_id}...")
            self.client.connect(self.broker, self.port, 60)
            self.client.loop_start()
            
            # Wait for connection
            start_time = time.time()
            while not self.is_connected and (time.time() - start_time < timeout):
                time.sleep(0.1)
                
            if not self.is_connected:
                logger.warning("MQTT: Connection timed out.")
            return self.is_connected
        except Exception as e:
            logger.error(f"MQTT: Connection failed: {e}")
            return False

    def disconnect(self):
        """Stop the loop and disconnect."""
        self.client.loop_stop()
        self.client.disconnect()
        logger.info(f"MQTT: Disconnected {self.client_id}")

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.is_connected = True
            logger.info(f"MQTT: Connected successfully (rc={rc})")
            # Resubscribe to topics on reconnect
            for topic in self.subscriptions:
                self.client.subscribe(topic)
        else:
            logger.error(f"MQTT: Connection failed with result code {rc}")

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self.is_connected = False
        logger.warning(f"MQTT: Disconnected (rc={rc})")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode())
            if topic in self.subscriptions:
                for callback in self.subscriptions[topic]:
                    callback(payload)
        except Exception as e:
            logger.error(f"MQTT: Error parsing message on {topic}: {e}")

    def subscribe(self, topic: str, callback: Callable):
        """Subscribe to a topic and register a callback."""
        if topic not in self.subscriptions:
            self.subscriptions[topic] = []
            if self.is_connected:
                self.client.subscribe(topic)
        self.subscriptions[topic].append(callback)
        logger.debug(f"MQTT: Subscribed to {topic}")

    def publish(self, topic: str, payload: Any):
        """Publish a JSON-serializable payload."""
        if not self.is_connected:
            return False
            
        class CustomEncoder(json.JSONEncoder):
            def default(self, obj):
                if hasattr(obj, 'isoformat'):
                    return obj.isoformat()
                if isinstance(obj, np.integer):
                    return int(obj)
                if isinstance(obj, np.floating):
                    return float(obj)
                if isinstance(obj, np.ndarray):
                    return obj.tolist()
                try:
                    return str(obj)
                except:
                    return super().default(obj)

        try:
            msg = json.dumps(payload, cls=CustomEncoder, default=str)
            self.client.publish(topic, msg)
            return True
        except Exception as e:
            logger.error(f"MQTT: Publish error on {topic}: {e}")
            return False

class MqttPublisher(MqttNode):
    """
    Dedicated publisher for broadcasting microgrid states and commands.
    """
    def broadcast_state(self, mg_id: str, state: Dict[str, Any]):
        """Publish filtered microgrid state (e.g., from EKF)."""
        return self.publish(f"microgrid/{mg_id}/state", state)

    def broadcast_command(self, mg_id: str, command: Dict[str, Any]):
        """Publish supervisory commands (from ems)."""
        return self.publish(f"microgrid/{mg_id}/command", command)

    def broadcast_city_metrics(self, metrics: Dict[str, Any]):
        """Publish city-wide resilience metrics."""
        return self.publish("city/metrics", metrics)

    def broadcast_alert(self, severity: str, message: str, mg_id: Optional[str] = None):
        """Publish alerts to the dashboard."""
        payload = {
            "timestamp": time.time(),
            "severity": severity,
            "message": message,
            "mg_id": mg_id
        }
        return self.publish("city/alerts", payload)

class MqttSubscriber(MqttNode):
    """
    Dedicated subscriber for receiving external inputs (NILM, Forecasts).
    """
    def on_nilm_update(self, callback: Callable):
        """Subscribe to appliance-level disaggregation data."""
        self.subscribe("microgrid/+/nilm", callback)

    def on_forecast_update(self, callback: Callable):
        """Subscribe to external forecasting engine updates."""
        self.subscribe("microgrid/+/forecast", callback)

    def on_control_override(self, callback: Callable):
        """Subscribe to manual dashboard overrides."""
        self.subscribe("city/override", callback)
