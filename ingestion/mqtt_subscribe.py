import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional
from dataclasses import dataclass
import paho.mqtt.client as mqtt
from collections import deque

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class MessageBuffer:
    """Buffer to handle async message arrivals"""
    nilm_messages: deque
    forecast_messages: deque
    max_size: int = 100
    max_age_seconds: int = 10
    
    def __init__(self, max_size=100, max_age_seconds=10):
        self.nilm_messages = deque(maxlen=max_size)
        self.forecast_messages = deque(maxlen=max_size)
        self.max_size = max_size
        self.max_age_seconds = max_age_seconds
    
    def add_nilm(self, message: dict):
        """Add NILM message to buffer"""
        message['buffer_timestamp'] = datetime.now()
        self.nilm_messages.append(message)
        self._cleanup_old_messages()
    
    def add_forecast(self, message: dict):
        """Add forecast message to buffer"""
        message['buffer_timestamp'] = datetime.now()
        self.forecast_messages.append(message)
        self._cleanup_old_messages()
    
    def _cleanup_old_messages(self):
        """Remove messages older than max_age"""
        cutoff = datetime.now() - timedelta(seconds=self.max_age_seconds)
        
        # Clean NILM buffer
        while (self.nilm_messages and 
               self.nilm_messages[0]['buffer_timestamp'] < cutoff):
            self.nilm_messages.popleft()
        
        # Clean forecast buffer
        while (self.forecast_messages and 
               self.forecast_messages[0]['buffer_timestamp'] < cutoff):
            self.forecast_messages.popleft()
    
    def get_latest_nilm(self) -> Optional[dict]:
        """Get most recent NILM message"""
        return self.nilm_messages[-1] if self.nilm_messages else None
    
    def get_latest_forecast(self) -> Optional[dict]:
        """Get most recent forecast message"""
        return self.forecast_messages[-1] if self.forecast_messages else None
    
    def get_synced_messages(self, timestamp: datetime, 
                           tolerance_seconds: int = 5) -> Dict[str, Optional[dict]]:
        """
        Get NILM and forecast messages that align with given timestamp
        Uses nearest-neighbor matching within tolerance
        """
        result = {
            'nilm': None,
            'forecast': None
        }
        
        # Find closest NILM message
        min_diff = float('inf')
        for msg in self.nilm_messages:
            msg_time = datetime.fromisoformat(msg['timestamp'])
            diff = abs((msg_time - timestamp).total_seconds())
            if diff < min_diff and diff <= tolerance_seconds:
                min_diff = diff
                result['nilm'] = msg
        
        # Find closest forecast message
        min_diff = float('inf')
        for msg in self.forecast_messages:
            msg_time = datetime.fromisoformat(msg['timestamp'])
            diff = abs((msg_time - timestamp).total_seconds())
            if diff < min_diff and diff <= tolerance_seconds:
                min_diff = diff
                result['forecast'] = msg
        
        return result


class MQTTSubscriber:
    """
    MQTT Subscriber for Digital Twin
    Subscribes to data streams from Person 1 (NILM) and Person 2 (Forecast)
    """
    
    def __init__(self, 
                 broker_host: str = "localhost",
                 broker_port: int = 1883,
                 username: Optional[str] = None,
                 password: Optional[str] = None):
        
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client = mqtt.Client(client_id="dt_core_subscriber")
        
        # Set credentials if provided
        if username and password:
            self.client.username_pw_set(username, password)
        
        # Message buffer
        self.buffer = MessageBuffer()
        
        # Callback handlers
        self.state_update_callback: Optional[Callable] = None
        
        # Statistics
        self.stats = {
            'nilm_received': 0,
            'forecast_received': 0,
            'parse_errors': 0,
            'validation_errors': 0
        }
        
        # Setup callbacks
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        
        logger.info(f"MQTT Subscriber initialized for {broker_host}:{broker_port}")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to broker"""
        if rc == 0:
            logger.info("✓ Connected to MQTT broker")
            
            # Subscribe to topics
            self.client.subscribe("/microgrid/nilm", qos=1)
            self.client.subscribe("/microgrid/forecast", qos=1)
            
            logger.info("✓ Subscribed to /microgrid/nilm")
            logger.info("✓ Subscribed to /microgrid/forecast")
        else:
            logger.error(f"✗ Connection failed with code {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected"""
        if rc != 0:
            logger.warning(f"Unexpected disconnection (code {rc}). Reconnecting...")
        else:
            logger.info("Disconnected from MQTT broker")
    
    def _on_message(self, client, userdata, msg):
        """Callback when message received"""
        try:
            # Parse JSON payload
            payload = json.loads(msg.payload.decode())
            
            # Route to appropriate handler
            if msg.topic == "/microgrid/nilm":
                self._handle_nilm_message(payload)
            elif msg.topic == "/microgrid/forecast":
                self._handle_forecast_message(payload)
            else:
                logger.warning(f"Unknown topic: {msg.topic}")
        
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            self.stats['parse_errors'] += 1
        except Exception as e:
            logger.error(f"Message handling error: {e}")
    
    def _handle_nilm_message(self, payload: dict):
        """Process NILM data from Person 1"""
        try:
            # Validate required fields
            required_fields = ['timestamp', 'total_load', 'appliances']
            if not all(field in payload for field in required_fields):
                raise ValueError(f"Missing required fields: {required_fields}")
            
            # Validate timestamp format
            datetime.fromisoformat(payload['timestamp'])
            
            # Add to buffer
            self.buffer.add_nilm(payload)
            self.stats['nilm_received'] += 1
            
            logger.debug(f"NILM: Load={payload['total_load']}kW, "
                        f"Appliances={len(payload['appliances'])}")
            
            # Trigger state update
            if self.state_update_callback:
                self.state_update_callback('nilm', payload)
        
        except (ValueError, KeyError) as e:
            logger.error(f"NILM validation error: {e}")
            self.stats['validation_errors'] += 1
    
    def _handle_forecast_message(self, payload: dict):
        """Process forecast data from Person 2"""
        try:
            # Validate required fields
            required_fields = ['timestamp', 'forecast_type', 'predictions']
            if not all(field in payload for field in required_fields):
                raise ValueError(f"Missing required fields: {required_fields}")
            
            # Validate timestamp
            datetime.fromisoformat(payload['timestamp'])
            
            # Add to buffer
            self.buffer.add_forecast(payload)
            self.stats['forecast_received'] += 1
            
            logger.debug(f"Forecast: Type={payload['forecast_type']}, "
                        f"Horizon={payload.get('horizon', 'N/A')}")
            
            # Trigger state update
            if self.state_update_callback:
                self.state_update_callback('forecast', payload)
        
        except (ValueError, KeyError) as e:
            logger.error(f"Forecast validation error: {e}")
            self.stats['validation_errors'] += 1
    
    def set_state_update_callback(self, callback: Callable):
        """Register callback for state updates"""
        self.state_update_callback = callback
        logger.info("State update callback registered")
    
    def connect(self):
        """Connect to MQTT broker"""
        try:
            self.client.connect(self.broker_host, self.broker_port, keepalive=60)
            logger.info(f"Connecting to {self.broker_host}:{self.broker_port}...")
        except Exception as e:
            logger.error(f"Connection error: {e}")
            raise
    
    def start(self):
        """Start MQTT client loop"""
        logger.info("Starting MQTT subscriber loop...")
        self.client.loop_forever()
    
    def start_async(self):
        """Start MQTT client in background thread"""
        logger.info("Starting MQTT subscriber in async mode...")
        self.client.loop_start()
    
    def stop(self):
        """Stop MQTT client"""
        logger.info("Stopping MQTT subscriber...")
        self.client.loop_stop()
        self.client.disconnect()
    
    def get_statistics(self) -> dict:
        """Get subscriber statistics"""
        return {
            **self.stats,
            'buffer_nilm_size': len(self.buffer.nilm_messages),
            'buffer_forecast_size': len(self.buffer.forecast_messages)
        }


# Example usage for testing
if __name__ == "__main__":
    # Initialize subscriber
    subscriber = MQTTSubscriber(
        broker_host="localhost",
        broker_port=1883
    )
    
    # Define callback for state updates
    def on_state_update(source: str, payload: dict):
        print(f"\n[{source.upper()}] New data received:")
        print(f"  Timestamp: {payload['timestamp']}")
        if source == 'nilm':
            print(f"  Load: {payload['total_load']}kW")
            print(f"  Appliances: {list(payload['appliances'].keys())}")
        elif source == 'forecast':
            print(f"  Type: {payload['forecast_type']}")
            print(f"  Predictions: {len(payload['predictions'])} points")
    
    # Register callback
    subscriber.set_state_update_callback(on_state_update)
    
    # Connect and start
    try:
        subscriber.connect()
        print("\n✓ Subscriber ready. Waiting for messages...")
        print("  Press Ctrl+C to stop\n")
        subscriber.start()  # Blocking call
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        subscriber.stop()
        print("✓ Subscriber stopped")
        
        # Print statistics
        stats = subscriber.get_statistics()
        print("\nStatistics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")