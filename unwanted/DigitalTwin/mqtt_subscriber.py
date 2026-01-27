"""
MQTT Subscriber Layer for Digital Twin
Subscribes to real-time microgrid data streams and feeds into Digital Twin
"""
import json
import logging
from typing import Dict, Callable, Optional
from datetime import datetime
from dataclasses import dataclass
import threading
import queue

logger = logging.getLogger(__name__)

# Try to import paho-mqtt, fall back to simulation mode if not available
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    logger.warning("paho-mqtt not installed. Running in simulation mode.")


@dataclass
class MQTTConfig:
    """MQTT Broker Configuration"""
    broker_address: str = "localhost"
    broker_port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    keepalive: int = 60
    qos: int = 1  # 0=at most once, 1=at least once, 2=exactly once


@dataclass
class MicrogridTelemetry:
    """Real-time telemetry from physical microgrid"""
    timestamp: datetime
    microgrid_id: str
    
    # Real-time measurements (from SCADA/sensors)
    pv_power_kw: float
    battery_power_kw: float
    battery_soc_percent: float
    grid_power_kw: float
    total_load_kw: float
    
    # Status flags
    grid_available: bool
    is_islanded: bool
    
    # Optional NILM data (from Person 1)
    nilm_appliances: Optional[Dict] = None
    
    # Data quality
    data_quality: float = 1.0  # 0-1, 1=perfect


class MQTTSubscriber:
    """
    MQTT Subscriber for Digital Twin
    
    Subscribes to real-time microgrid data and feeds into Digital Twin.
    Supports both real MQTT brokers and simulation mode.
    
    Topics:
    - microgrid/{id}/telemetry - Real-time power data
    - microgrid/{id}/nilm - NILM appliance breakdown (from Person 1)
    - microgrid/{id}/status - System status updates
    - microgrid/{id}/alerts - Critical alerts
    """
    
    def __init__(self, config: MQTTConfig = None):
        """Initialize MQTT subscriber"""
        self.config = config or MQTTConfig()
        self.client = None
        self.connected = False
        self.simulation_mode = not MQTT_AVAILABLE
        
        # Data queues for thread-safe communication
        self.telemetry_queue = queue.Queue(maxsize=1000)
        self.nilm_queue = queue.Queue(maxsize=100)
        self.alert_queue = queue.Queue(maxsize=50)
        
        # Callbacks
        self.telemetry_callbacks: Dict[str, Callable] = {}
        self.nilm_callbacks: Dict[str, Callable] = {}
        
        # Statistics
        self.messages_received = 0
        self.messages_processed = 0
        self.connection_errors = 0
        
        if not self.simulation_mode:
            self._setup_mqtt_client()
        else:
            logger.info("✓ MQTT Subscriber initialized (SIMULATION MODE)")
    
    def _setup_mqtt_client(self):
        """Setup MQTT client with callbacks"""
        self.client = mqtt.Client(client_id=f"digital_twin_{id(self)}")
        
        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        
        # Authentication
        if self.config.username and self.config.password:
            self.client.username_pw_set(self.config.username, self.config.password)
        
        logger.info("✓ MQTT Client configured")
    
    def connect(self):
        """Connect to MQTT broker"""
        if self.simulation_mode:
            logger.info("✓ MQTT running in simulation mode (no broker needed)")
            self.connected = True
            return True
        
        try:
            self.client.connect(
                self.config.broker_address,
                self.config.broker_port,
                self.config.keepalive
            )
            self.client.loop_start()
            logger.info(f"✓ Connecting to MQTT broker {self.config.broker_address}:{self.config.broker_port}")
            return True
        except Exception as e:
            logger.error(f"❌ MQTT connection failed: {e}")
            self.connection_errors += 1
            return False
    
    def disconnect(self):
        """Disconnect from MQTT broker"""
        if not self.simulation_mode and self.client:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("✓ MQTT disconnected")
        self.connected = False
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to broker"""
        if rc == 0:
            self.connected = True
            logger.info("✓ MQTT connected successfully")
            
            # Subscribe to all microgrid topics
            topics = [
                ("microgrid/+/telemetry", self.config.qos),
                ("microgrid/+/nilm", self.config.qos),
                ("microgrid/+/status", self.config.qos),
                ("microgrid/+/alerts", self.config.qos)
            ]
            self.client.subscribe(topics)
            logger.info(f"✓ Subscribed to {len(topics)} topic patterns")
        else:
            logger.error(f"❌ MQTT connection failed with code {rc}")
            self.connection_errors += 1
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from broker"""
        self.connected = False
        if rc != 0:
            logger.warning(f"⚠️ Unexpected MQTT disconnect (code {rc})")
    
    def _on_message(self, client, userdata, msg):
        """Callback when message received"""
        try:
            self.messages_received += 1
            
            # Parse topic
            topic_parts = msg.topic.split('/')
            if len(topic_parts) < 3:
                return
            
            microgrid_id = topic_parts[1]
            message_type = topic_parts[2]
            
            # Parse payload
            payload = json.loads(msg.payload.decode())
            
            # Route to appropriate handler
            if message_type == 'telemetry':
                self._handle_telemetry(microgrid_id, payload)
            elif message_type == 'nilm':
                self._handle_nilm(microgrid_id, payload)
            elif message_type == 'status':
                self._handle_status(microgrid_id, payload)
            elif message_type == 'alerts':
                self._handle_alert(microgrid_id, payload)
            
            self.messages_processed += 1
            
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
    
    def _handle_telemetry(self, microgrid_id: str, payload: Dict):
        """Handle real-time telemetry data"""
        try:
            telemetry = MicrogridTelemetry(
                timestamp=datetime.fromisoformat(payload.get('timestamp', datetime.now().isoformat())),
                microgrid_id=microgrid_id,
                pv_power_kw=payload.get('pv_power_kw', 0),
                battery_power_kw=payload.get('battery_power_kw', 0),
                battery_soc_percent=payload.get('battery_soc_percent', 50),
                grid_power_kw=payload.get('grid_power_kw', 0),
                total_load_kw=payload.get('total_load_kw', 0),
                grid_available=payload.get('grid_available', True),
                is_islanded=payload.get('is_islanded', False),
                data_quality=payload.get('data_quality', 1.0)
            )
            
            # Queue for processing
            self.telemetry_queue.put(telemetry)
            
            # Trigger callback if registered
            if microgrid_id in self.telemetry_callbacks:
                self.telemetry_callbacks[microgrid_id](telemetry)
                
        except Exception as e:
            logger.error(f"❌ Error parsing telemetry: {e}")
    
    def _handle_nilm(self, microgrid_id: str, payload: Dict):
        """Handle NILM appliance breakdown data (from Person 1)"""
        try:
            self.nilm_queue.put({
                'microgrid_id': microgrid_id,
                'timestamp': datetime.fromisoformat(payload.get('timestamp', datetime.now().isoformat())),
                'appliances': payload.get('appliances', {}),
                'confidence': payload.get('confidence', 0.8)
            })
            
            # Trigger callback if registered
            if microgrid_id in self.nilm_callbacks:
                self.nilm_callbacks[microgrid_id](payload)
                
        except Exception as e:
            logger.error(f"❌ Error parsing NILM data: {e}")
    
    def _handle_status(self, microgrid_id: str, payload: Dict):
        """Handle status updates"""
        logger.info(f"📊 Status update from {microgrid_id}: {payload.get('message', 'N/A')}")
    
    def _handle_alert(self, microgrid_id: str, payload: Dict):
        """Handle critical alerts"""
        self.alert_queue.put({
            'microgrid_id': microgrid_id,
            'timestamp': datetime.now(),
            'severity': payload.get('severity', 'INFO'),
            'message': payload.get('message', ''),
            'data': payload
        })
        logger.warning(f"⚠️ Alert from {microgrid_id}: {payload.get('message', 'N/A')}")
    
    def register_telemetry_callback(self, microgrid_id: str, callback: Callable):
        """Register callback for telemetry updates"""
        self.telemetry_callbacks[microgrid_id] = callback
        logger.info(f"✓ Registered telemetry callback for {microgrid_id}")
    
    def register_nilm_callback(self, microgrid_id: str, callback: Callable):
        """Register callback for NILM updates"""
        self.nilm_callbacks[microgrid_id] = callback
        logger.info(f"✓ Registered NILM callback for {microgrid_id}")
    
    def get_telemetry(self, timeout: float = 1.0) -> Optional[MicrogridTelemetry]:
        """Get next telemetry message from queue"""
        try:
            return self.telemetry_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_nilm_data(self, timeout: float = 1.0) -> Optional[Dict]:
        """Get next NILM data from queue"""
        try:
            return self.nilm_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_alert(self, timeout: float = 1.0) -> Optional[Dict]:
        """Get next alert from queue"""
        try:
            return self.alert_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def publish_simulation_data(self, microgrid_id: str, data: Dict):
        """
        Simulate MQTT publish for testing (simulation mode)
        
        Use this to inject simulated sensor data into the Digital Twin
        """
        if self.simulation_mode:
            # Simulate receiving the data
            self._handle_telemetry(microgrid_id, data)
        else:
            # Actually publish to broker
            topic = f"microgrid/{microgrid_id}/telemetry"
            self.client.publish(topic, json.dumps(data), qos=self.config.qos)
    
    def get_stats(self) -> Dict:
        """Get subscriber statistics"""
        return {
            'connected': self.connected,
            'simulation_mode': self.simulation_mode,
            'messages_received': self.messages_received,
            'messages_processed': self.messages_processed,
            'connection_errors': self.connection_errors,
            'telemetry_queue_size': self.telemetry_queue.qsize(),
            'nilm_queue_size': self.nilm_queue.qsize(),
            'alert_queue_size': self.alert_queue.qsize()
        }
