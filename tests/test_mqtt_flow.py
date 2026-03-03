import pytest
import json
import time
from EMS.mqtt_manager import MqttPublisher, MqttSubscriber

def test_mqtt_pub_sub_local():
    """
    Integration test for MQTT middleware.
    Requires a local broker running on localhost:1883.
    """
    pub = MqttPublisher("test_pub")
    sub = MqttSubscriber("test_sub")
    
    received_payloads = []
    def on_msg(payload):
        received_payloads.append(payload)
        
    if not pub.connect(timeout=2):
        pytest.skip("Local MQTT broker not detected. Skipping integration test.")
        
    sub.connect()
    sub.subscribe("test/topic", on_msg)
    
    # Allow some time for subscription to propagate
    time.sleep(0.5)
    
    test_data = {"key": "value", "status": "ok"}
    pub.publish("test/topic", test_data)
    
    # Wait for message to arrive
    time.sleep(1.0)
    
    assert len(received_payloads) > 0
    assert received_payloads[0]["key"] == "value"
    
    pub.disconnect()
    sub.disconnect()
