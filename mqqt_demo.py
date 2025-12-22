"""
Mock MQTT Publishers for Testing Digital Twin
Simulates Person 1 (NILM) and Person 2 (Forecast) modules
"""

import paho.mqtt.client as mqtt
import json
import time
import random
from datetime import datetime, timedelta
import math

class MockNILMPublisher:
    """Simulates Person 1's NILM module"""
    
    def __init__(self, broker_host="localhost", broker_port=1883):
        self.client = mqtt.Client(client_id="mock_nilm_publisher")
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.running = False
    
    def generate_nilm_data(self):
        """Generate realistic NILM data"""
        # Base load varies throughout day
        hour = datetime.now().hour
        
        # Simulate daily load pattern
        base_load = 2.0 + 2.0 * math.sin((hour - 6) * math.pi / 12)
        base_load = max(1.0, base_load)
        
        # Appliances with realistic patterns
        appliances = {
            "refrigerator": 0.15 + random.uniform(-0.02, 0.02),
            "lighting": 0.3 if 6 <= hour <= 22 else 0.05,
            "air_conditioner": 2.0 if 14 <= hour <= 20 else 0.5,
            "tv": 0.15 if 18 <= hour <= 23 else 0.0,
            "computer": 0.2 if 9 <= hour <= 23 else 0.0,
        }
        
        # Add washing machine randomly (shiftable load)
        if random.random() > 0.7:
            appliances["washing_machine"] = 1.2
        
        # Add dishwasher randomly (shiftable load)
        if random.random() > 0.8:
            appliances["dishwasher"] = 1.0
        
        total_load = sum(appliances.values())
        
        return {
            "timestamp": datetime.now().isoformat(),
            "total_load": round(total_load, 2),
            "appliances": {k: round(v, 2) for k, v in appliances.items()},
            "confidence": round(random.uniform(0.85, 0.95), 2)
        }
    
    def start_publishing(self, interval=5):
        """Start publishing NILM data"""
        self.client.connect(self.broker_host, self.broker_port)
        self.client.loop_start()
        self.running = True
        
        print("🔌 Mock NILM Publisher started")
        print(f"   Publishing to: /microgrid/nilm")
        print(f"   Interval: {interval}s\n")
        
        try:
            while self.running:
                data = self.generate_nilm_data()
                payload = json.dumps(data)
                
                self.client.publish("/microgrid/nilm", payload, qos=1)
                print(f"📤 NILM: Load={data['total_load']}kW, "
                      f"Appliances={len(data['appliances'])}")
                
                time.sleep(interval)
        
        except KeyboardInterrupt:
            print("\n🛑 Stopping NILM publisher...")
            self.stop()
    
    def stop(self):
        """Stop publishing"""
        self.running = False
        self.client.loop_stop()
        self.client.disconnect()
        print("✅ NILM publisher stopped")


class MockForecastPublisher:
    """Simulates Person 2's Forecast module"""
    
    def __init__(self, broker_host="localhost", broker_port=1883):
        self.client = mqtt.Client(client_id="mock_forecast_publisher")
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.running = False
    
    def generate_solar_forecast(self):
        """Generate realistic solar forecast"""
        now = datetime.now()
        hour = now.hour
        
        predictions = []
        for i in range(24):  # 24-hour forecast
            future_time = now + timedelta(hours=i)
            future_hour = future_time.hour
            
            # Solar only during day (6 AM - 6 PM)
            if 6 <= future_hour <= 18:
                # Peak at noon
                solar = 4.0 * math.sin((future_hour - 6) * math.pi / 12)
                solar = max(0, solar + random.uniform(-0.5, 0.5))
            else:
                solar = 0.0
            
            predictions.append({
                "time": future_time.isoformat(),
                "value": round(solar, 2),
                "confidence": round(random.uniform(0.80, 0.90), 2)
            })
        
        return {
            "timestamp": now.isoformat(),
            "forecast_type": "solar",
            "horizon": "24h",
            "predictions": predictions,
            "model": "transformer"
        }
    
    def generate_wind_forecast(self):
        """Generate realistic wind forecast"""
        now = datetime.now()
        
        predictions = []
        base_wind = random.uniform(0.5, 1.5)
        
        for i in range(24):
            future_time = now + timedelta(hours=i)
            
            # Wind varies randomly
            wind = base_wind + random.uniform(-0.3, 0.3)
            wind = max(0, wind)
            
            predictions.append({
                "time": future_time.isoformat(),
                "value": round(wind, 2),
                "confidence": round(random.uniform(0.75, 0.85), 2)
            })
        
        return {
            "timestamp": now.isoformat(),
            "forecast_type": "wind",
            "horizon": "24h",
            "predictions": predictions,
            "model": "lstm"
        }
    
    def generate_price_forecast(self):
        """Generate realistic electricity price forecast"""
        now = datetime.now()
        
        predictions = []
        for i in range(24):
            future_time = now + timedelta(hours=i)
            future_hour = future_time.hour
            
            # Peak pricing during evening (5 PM - 9 PM)
            if 17 <= future_hour <= 21:
                price = random.uniform(0.040, 0.055)
            # Mid pricing during day
            elif 9 <= future_hour <= 17:
                price = random.uniform(0.030, 0.040)
            # Off-peak at night
            else:
                price = random.uniform(0.020, 0.030)
            
            predictions.append({
                "time": future_time.isoformat(),
                "value": round(price, 4),
                "confidence": round(random.uniform(0.85, 0.95), 2)
            })
        
        return {
            "timestamp": now.isoformat(),
            "forecast_type": "price",
            "horizon": "24h",
            "predictions": predictions,
            "model": "transformer"
        }
    
    def start_publishing(self, interval=10):
        """Start publishing forecast data"""
        self.client.connect(self.broker_host, self.broker_port)
        self.client.loop_start()
        self.running = True
        
        print("🌤️  Mock Forecast Publisher started")
        print(f"   Publishing to: /microgrid/forecast")
        print(f"   Interval: {interval}s\n")
        
        try:
            cycle = 0
            while self.running:
                # Rotate through forecast types
                if cycle % 3 == 0:
                    data = self.generate_solar_forecast()
                elif cycle % 3 == 1:
                    data = self.generate_wind_forecast()
                else:
                    data = self.generate_price_forecast()
                
                payload = json.dumps(data)
                self.client.publish("/microgrid/forecast", payload, qos=1)
                
                print(f"📤 Forecast: Type={data['forecast_type']}, "
                      f"Points={len(data['predictions'])}")
                
                cycle += 1
                time.sleep(interval)
        
        except KeyboardInterrupt:
            print("\n🛑 Stopping Forecast publisher...")
            self.stop()
    
    def stop(self):
        """Stop publishing"""
        self.running = False
        self.client.loop_stop()
        self.client.disconnect()
        print("✅ Forecast publisher stopped")


def run_both_publishers():
    """Run both mock publishers simultaneously"""
    import threading
    
    print("\n" + "="*60)
    print("🚀 Starting Mock MQTT Publishers for Digital Twin Testing")
    print("="*60 + "\n")
    
    nilm = MockNILMPublisher()
    forecast = MockForecastPublisher()
    
    # Run in separate threads
    nilm_thread = threading.Thread(target=nilm.start_publishing, args=(5,))
    forecast_thread = threading.Thread(target=forecast.start_publishing, args=(10,))
    
    nilm_thread.start()
    time.sleep(1)  # Stagger startup
    forecast_thread.start()
    
    try:
        nilm_thread.join()
        forecast_thread.join()
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down all publishers...")
        nilm.stop()
        forecast.stop()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "nilm":
            publisher = MockNILMPublisher()
            publisher.start_publishing()
        elif sys.argv[1] == "forecast":
            publisher = MockForecastPublisher()
            publisher.start_publishing()
        else:
            print("Usage: python mock_publishers.py [nilm|forecast]")
    else:
        # Run both
        run_both_publishers()