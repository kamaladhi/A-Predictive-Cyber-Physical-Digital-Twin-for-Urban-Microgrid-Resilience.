import asyncio
import logging
from datetime import datetime
import json

from ingestion.mqtt_subscribe import MQTTSubscriber
from Core.state_sync import StateSynchronizer
from intelligence.datafusion import DataFusionEngine
from intelligence.dr_logic import DemandResponseEngine
from api import create_api_server

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DigitalTwinCore:
    """
    Main Digital Twin orchestrator
    Coordinates all components
    """
    
    def __init__(self, config: dict):
        logger.info("Initializing Digital Twin Core...")
        
        # Initialize components
        self.state_sync = StateSynchronizer(
            redis_host=config.get('redis_host', 'localhost')
        )
        
        self.mqtt_sub = MQTTSubscriber(
            broker_host=config.get('mqtt_host', 'localhost'),
            broker_port=config.get('mqtt_port', 1883)
        )
        
        self.fusion = DataFusionEngine(
            redis_client=self.state_sync.redis_client
        )
        
        self.dr_engine = DemandResponseEngine(
            fusion_engine=self.fusion
        )
        
        # Register MQTT callback
        self.mqtt_sub.set_state_update_callback(self.on_data_received)
        
        # Stats
        self.update_count = 0
        self.alert_count = 0
        
        logger.info("✓ Digital Twin Core initialized")
    
    def on_data_received(self, source: str, payload: dict):
        """Callback when new data arrives from MQTT"""
        try:
            # Update state based on source
            if source == 'nilm':
                self.state_sync.update_from_nilm(payload)
            elif source == 'forecast':
                self.state_sync.update_from_forecast(payload)
            
            # Get updated state
            state = self.state_sync.get_current_state()
            
            # Run fusion analysis
            fusion_metrics = self.fusion.fuse_current_state(state)
            self.fusion.add_to_history(state)
            
            # Run DR logic every 5 updates (don't spam)
            self.update_count += 1
            if self.update_count % 5 == 0:
                dr_results = self.dr_engine.analyze_and_respond(state)
                
                # Publish alerts if any
                if dr_results['alerts']:
                    self._publish_alerts(dr_results['alerts'])
                    self.alert_count += len(dr_results['alerts'])
            
            logger.info(f"State updated from {source}: "
                       f"Load={state.total_load:.2f}kW, "
                       f"Quality={fusion_metrics.data_quality_score:.2f}")
        
        except Exception as e:
            logger.error(f"Error processing {source} data: {e}")
    
    def _publish_alerts(self, alerts: list):
        """Publish DR alerts to MQTT"""
        try:
            for alert in alerts:
                # Publish to /microgrid/alerts topic
                payload = json.dumps(alert)
                self.mqtt_sub.client.publish(
                    "/microgrid/alerts",
                    payload,
                    qos=1
                )
            
            logger.info(f"Published {len(alerts)} alerts")
        
        except Exception as e:
            logger.error(f"Error publishing alerts: {e}")
    
    async def periodic_analysis(self):
        """Run periodic DR analysis"""
        while True:
            try:
                await asyncio.sleep(300)  # Every 5 minutes
                
                state = self.state_sync.get_current_state()
                
                # Run comprehensive DR analysis
                dr_results = self.dr_engine.analyze_and_respond(state)
                
                if dr_results['alerts']:
                    self._publish_alerts(dr_results['alerts'])
                
                logger.info("Periodic DR analysis complete")
            
            except Exception as e:
                logger.error(f"Periodic analysis error: {e}")
    
    def start(self):
        """Start Digital Twin"""
        logger.info("Starting Digital Twin Core...")
        
        # Connect MQTT
        self.mqtt_sub.connect()
        self.mqtt_sub.start_async()
        
        # Start API server in background
        api_server = create_api_server(self.state_sync, self.dr_engine)
        
        # Start event loop
        loop = asyncio.get_event_loop()
        loop.create_task(self.periodic_analysis())
        
        logger.info("✓ Digital Twin Core running")
        
        try:
            # Run forever
            loop.run_forever()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.stop()
    
    def stop(self):
        """Stop Digital Twin"""
        self.mqtt_sub.stop()
        logger.info("✓ Digital Twin Core stopped")
        
        # Print statistics
        logger.info(f"Total updates: {self.update_count}")
        logger.info(f"Total alerts: {self.alert_count}")


if __name__ == "__main__":
    config = {
        'mqtt_host': 'localhost',
        'mqtt_port': 1883,
        'redis_host': 'localhost'
    }
    
    dt_core = DigitalTwinCore(config)
    dt_core.start()