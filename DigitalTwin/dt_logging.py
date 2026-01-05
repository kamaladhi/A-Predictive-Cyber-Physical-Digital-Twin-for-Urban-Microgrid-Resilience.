"""
System Logging and Export Module for Digital Twin
Comprehensive logging for all DT operations, alerts, and state changes
"""
import logging
import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import csv
from dataclasses import asdict

logger = logging.getLogger(__name__)


class DigitalTwinLogger:
    """
    Comprehensive logging system for Digital Twin operations
    
    Logs:
    - State updates
    - Data fusion events
    - MQTT telemetry
    - NILM updates
    - Forecasts
    - DR alerts
    - Coordination decisions
    - System events
    """
    
    def __init__(self, log_dir: str = "digital_twin_logs"):
        """Initialize logging system"""
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True, parents=True)
        
        # Separate log files for different categories
        self.state_log_path = self.log_dir / "state_updates.csv"
        self.fusion_log_path = self.log_dir / "data_fusion.csv"
        self.alerts_log_path = self.log_dir / "dr_alerts.json"
        self.mqtt_log_path = self.log_dir / "mqtt_telemetry.csv"
        self.events_log_path = self.log_dir / "system_events.log"
        self.commands_log_path = self.log_dir / "iems_commands.csv"
        
        # Initialize log files
        self._initialize_log_files()
        
        # Statistics
        self.states_logged = 0
        self.fusion_events_logged = 0
        self.alerts_logged = 0
        self.mqtt_messages_logged = 0
        self.commands_logged = 0
        
        logger.info(f"✓ Digital Twin Logger initialized: {self.log_dir}")
    
    def _initialize_log_files(self):
        """Create log files with headers"""
        # State updates CSV
        if not self.state_log_path.exists():
            with open(self.state_log_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'microgrid_id', 'load_kw', 'pv_kw', 
                    'battery_kw', 'soc_percent', 'grid_power_kw',
                    'is_islanded', 'power_balance_kw'
                ])
        
        # Data fusion CSV
        if not self.fusion_log_path.exists():
            with open(self.fusion_log_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'microgrid_id', 'measured_load_kw',
                    'critical_load_kw', 'non_critical_load_kw',
                    'forecasted_1h_kw', 'peak_predicted',
                    'nilm_confidence', 'forecast_confidence', 'overall_confidence'
                ])
        
        # MQTT telemetry CSV
        if not self.mqtt_log_path.exists():
            with open(self.mqtt_log_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'microgrid_id', 'pv_power_kw',
                    'battery_power_kw', 'battery_soc_percent', 'total_load_kw',
                    'grid_available', 'data_quality'
                ])
        
        # Alerts JSON (append mode)
        if not self.alerts_log_path.exists():
            with open(self.alerts_log_path, 'w') as f:
                json.dump([], f)

        # IEMS commands CSV
        if not self.commands_log_path.exists():
            with open(self.commands_log_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'microgrid_id', 'command_type', 'value_kw',
                    'reason', 'alert_ids', 'notes'
                ])
    
    def log_state_update(self, state):
        """
        Log Digital Twin state update
        
        Args:
            state: MicrogridState object
        """
        try:
            with open(self.state_log_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    state.timestamp.isoformat(),
                    state.microgrid_id,
                    state.total_load_kw,
                    state.pv_power_kw,
                    state.battery_power_kw,
                    state.battery_soc_percent,
                    state.grid_power_kw,
                    state.is_islanded,
                    state.power_balance_kw
                ])
            
            self.states_logged += 1
            
        except Exception as e:
            logger.error(f"❌ Error logging state: {e}")
    
    def log_fusion_event(self, fused_state):
        """
        Log data fusion event
        
        Args:
            fused_state: FusedMicrogridState object
        """
        try:
            with open(self.fusion_log_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    fused_state.timestamp.isoformat(),
                    fused_state.microgrid_id,
                    fused_state.measured_load_kw,
                    fused_state.critical_load_kw,
                    fused_state.non_critical_load_kw,
                    fused_state.forecasted_load_1h,
                    fused_state.peak_predicted,
                    fused_state.nilm_confidence,
                    fused_state.forecast_confidence,
                    fused_state.overall_confidence
                ])
            
            self.fusion_events_logged += 1
            
        except Exception as e:
            logger.error(f"❌ Error logging fusion event: {e}")
    
    def log_mqtt_telemetry(self, telemetry):
        """
        Log MQTT telemetry message
        
        Args:
            telemetry: MicrogridTelemetry object
        """
        try:
            with open(self.mqtt_log_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    telemetry.timestamp.isoformat(),
                    telemetry.microgrid_id,
                    telemetry.pv_power_kw,
                    telemetry.battery_power_kw,
                    telemetry.battery_soc_percent,
                    telemetry.total_load_kw,
                    telemetry.grid_available,
                    telemetry.data_quality
                ])
            
            self.mqtt_messages_logged += 1
            
        except Exception as e:
            logger.error(f"❌ Error logging MQTT telemetry: {e}")
    
    def log_dr_alert(self, alert):
        """
        Log DR alert
        
        Args:
            alert: DRAlert object
        """
        try:
            # Read existing alerts
            with open(self.alerts_log_path, 'r') as f:
                alerts = json.load(f)
            
            # Append new alert
            alert_dict = {
                'alert_id': alert.alert_id,
                'timestamp': alert.timestamp.isoformat(),
                'microgrid_id': alert.microgrid_id,
                'alert_type': alert.alert_type.value,
                'severity': alert.severity.value,
                'title': alert.title,
                'message': alert.message,
                'recommended_action': alert.recommended_action,
                'current_load_kw': alert.current_load_kw,
                'predicted_peak_kw': alert.predicted_peak_kw,
                'estimated_cost_usd': alert.estimated_cost_usd,
                'potential_savings_usd': alert.potential_savings_usd,
                'valid_until': alert.valid_until.isoformat() if alert.valid_until else None
            }
            
            alerts.append(alert_dict)
            
            # Write back
            with open(self.alerts_log_path, 'w') as f:
                json.dump(alerts, f, indent=2)
            
            self.alerts_logged += 1
            
        except Exception as e:
            logger.error(f"❌ Error logging DR alert: {e}")

    def log_iems_command(self, command):
        """
        Log IEMS command

        Args:
            command: IEMSCommand object
        """
        try:
            with open(self.commands_log_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    command.timestamp.isoformat(),
                    command.microgrid_id,
                    command.command_type.value,
                    command.value_kw,
                    command.reason,
                    ','.join(command.alert_ids) if command.alert_ids else '',
                    command.notes or ''
                ])

            self.commands_logged += 1
        except Exception as e:
            logger.error(f"❌ Error logging IEMS command: {e}")
    
    def log_system_event(self, event_type: str, message: str, 
                        microgrid_id: Optional[str] = None,
                        metadata: Optional[Dict] = None):
        """
        Log system event
        
        Args:
            event_type: Type of event (connection, error, etc.)
            message: Event message
            microgrid_id: Optional microgrid ID
            metadata: Optional additional data
        """
        try:
            timestamp = datetime.now().isoformat()
            
            with open(self.events_log_path, 'a') as f:
                f.write(f"[{timestamp}] {event_type.upper()}")
                if microgrid_id:
                    f.write(f" [{microgrid_id}]")
                f.write(f": {message}")
                if metadata:
                    f.write(f" | {json.dumps(metadata)}")
                f.write("\n")
                
        except Exception as e:
            logger.error(f"❌ Error logging system event: {e}")
    
    def export_summary_report(self, output_path: Optional[str] = None) -> Dict:
        """
        Export comprehensive summary report
        
        Returns:
            Dictionary with summary statistics
        """
        if output_path is None:
            output_path = self.log_dir / f"summary_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        summary = {
            'report_timestamp': datetime.now().isoformat(),
            'statistics': {
                'states_logged': self.states_logged,
                'fusion_events_logged': self.fusion_events_logged,
                'alerts_logged': self.alerts_logged,
                'mqtt_messages_logged': self.mqtt_messages_logged,
                'commands_logged': self.commands_logged
            },
            'log_files': {
                'state_updates': str(self.state_log_path),
                'data_fusion': str(self.fusion_log_path),
                'dr_alerts': str(self.alerts_log_path),
                'mqtt_telemetry': str(self.mqtt_log_path),
                'system_events': str(self.events_log_path),
                'iems_commands': str(self.commands_log_path)
            }
        }
        
        # Write summary
        with open(output_path, 'w') as f:
            json.dump(summary, f, indent=2)
        
        logger.info(f"✓ Summary report exported: {output_path}")
        
        return summary
    
    def get_recent_alerts(self, hours: int = 24) -> List[Dict]:
        """Get recent alerts from last N hours"""
        try:
            with open(self.alerts_log_path, 'r') as f:
                all_alerts = json.load(f)
            
            cutoff_time = datetime.now().timestamp() - (hours * 3600)
            
            recent = [
                alert for alert in all_alerts
                if datetime.fromisoformat(alert['timestamp']).timestamp() > cutoff_time
            ]
            
            return recent
            
        except Exception as e:
            logger.error(f"❌ Error reading recent alerts: {e}")
            return []
    
    def cleanup_old_logs(self, days_to_keep: int = 30):
        """
        Clean up logs older than specified days
        
        Args:
            days_to_keep: Number of days to retain logs
        """
        logger.info(f"Cleaning up logs older than {days_to_keep} days...")
        
        # This is a placeholder - actual implementation would:
        # 1. Read CSV files
        # 2. Filter rows by timestamp
        # 3. Write back only recent data
        
        # For now, just log the action
        self.log_system_event('cleanup', f'Log cleanup triggered ({days_to_keep} days)')
    
    def get_logging_stats(self) -> Dict:
        """Get logging statistics"""
        return {
            'states_logged': self.states_logged,
            'fusion_events_logged': self.fusion_events_logged,
            'alerts_logged': self.alerts_logged,
            'mqtt_messages_logged': self.mqtt_messages_logged,
            'commands_logged': self.commands_logged,
            'log_directory': str(self.log_dir),
            'log_files_exist': {
                'state_updates': self.state_log_path.exists(),
                'data_fusion': self.fusion_log_path.exists(),
                'dr_alerts': self.alerts_log_path.exists(),
                'mqtt_telemetry': self.mqtt_log_path.exists(),
                'system_events': self.events_log_path.exists(),
                'iems_commands': self.commands_log_path.exists()
            }
        }
    
    def generate_pdf_report(self, output_path: Optional[str] = None, 
                           title: str = "Digital Twin Report") -> str:
        """
        Generate a comprehensive PDF report with charts, metrics, and alerts.
        
        Args:
            output_path: Path for PDF file (auto-generated if None)
            title: Report title
        
        Returns:
            Path to generated PDF
        """
        try:
            from matplotlib.backends.backend_pdf import PdfPages
            import matplotlib.pyplot as plt
            import pandas as pd
        except ImportError:
            logger.error("❌ matplotlib or pandas not available for PDF generation")
            return ""
        
        if output_path is None:
            output_path = self.log_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        output_path = Path(output_path)
        
        try:
            with PdfPages(output_path) as pdf:
                # Page 1: Summary
                fig = plt.figure(figsize=(11, 8.5))
                fig.suptitle(title, fontsize=20, fontweight='bold')
                
                ax = fig.add_subplot(111)
                ax.axis('off')
                
                summary_text = f"""
DIGITAL TWIN EXECUTION SUMMARY
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

STATISTICS:
  • State Updates Logged: {self.states_logged}
  • Data Fusion Events: {self.fusion_events_logged}
  • DR Alerts Generated: {self.alerts_logged}
  • EMS Commands Issued: {self.commands_logged}
  • MQTT Messages: {self.mqtt_messages_logged}

LOG FILES:
  • State Updates: {self.state_log_path.name}
  • Data Fusion: {self.fusion_log_path.name}
  • DR Alerts: {self.alerts_log_path.name}
  • EMS Commands: {self.commands_log_path.name}
  • MQTT Telemetry: {self.mqtt_log_path.name}
  • System Events: {self.events_log_path.name}
                """
                
                ax.text(0.1, 0.5, summary_text, fontsize=11, family='monospace',
                       verticalalignment='center')
                
                pdf.savefig(fig, bbox_inches='tight')
                plt.close()
                
                # Page 2: Fusion Events (if available)
                if self.fusion_log_path.exists() and self.fusion_events_logged > 0:
                    try:
                        fusion_df = pd.read_csv(self.fusion_log_path)
                        
                        fig, axes = plt.subplots(2, 2, figsize=(11, 8.5))
                        fig.suptitle('Data Fusion Metrics', fontsize=16, fontweight='bold')
                        
                        # Load trend
                        axes[0, 0].plot(fusion_df.index, fusion_df['measured_load_kw'], label='Measured')
                        axes[0, 0].plot(fusion_df.index, fusion_df['forecasted_1h_kw'], label='Forecast 1h')
                        axes[0, 0].set_ylabel('Load (kW)')
                        axes[0, 0].set_title('Load vs Forecast')
                        axes[0, 0].legend()
                        axes[0, 0].grid(True, alpha=0.3)
                        
                        # Critical vs non-critical
                        axes[0, 1].bar(fusion_df.index, fusion_df['critical_load_kw'], label='Critical', alpha=0.7)
                        axes[0, 1].bar(fusion_df.index, fusion_df['non_critical_load_kw'], 
                                      bottom=fusion_df['critical_load_kw'], label='Non-Critical', alpha=0.7)
                        axes[0, 1].set_ylabel('Load (kW)')
                        axes[0, 1].set_title('Load Breakdown')
                        axes[0, 1].legend()
                        axes[0, 1].grid(True, alpha=0.3)
                        
                        # Overall confidence
                        axes[1, 0].plot(fusion_df.index, fusion_df['overall_confidence'], 'g-', linewidth=2)
                        axes[1, 0].set_ylabel('Confidence')
                        axes[1, 0].set_ylim([0, 1])
                        axes[1, 0].set_title('Fusion Confidence')
                        axes[1, 0].grid(True, alpha=0.3)
                        
                        # Peak prediction flag
                        peak_indices = fusion_df[fusion_df['peak_predicted'] == True].index
                        axes[1, 1].scatter(peak_indices, [1]*len(peak_indices), color='red', s=100, label='Peak Predicted')
                        axes[1, 1].set_ylim([0, 2])
                        axes[1, 1].set_ylabel('Peak Predicted')
                        axes[1, 1].set_title('Peak Detection')
                        axes[1, 1].legend()
                        axes[1, 1].grid(True, alpha=0.3)
                        
                        plt.tight_layout()
                        pdf.savefig(fig, bbox_inches='tight')
                        plt.close()
                    except Exception as e:
                        logger.warning(f"Could not plot fusion data: {e}")
                
                # Page 3: Alerts (if available)
                if self.alerts_log_path.exists() and self.alerts_logged > 0:
                    try:
                        with open(self.alerts_log_path, 'r') as f:
                            alerts = json.load(f)
                        
                        fig, axes = plt.subplots(1, 2, figsize=(11, 8.5))
                        fig.suptitle('DR Alerts Summary', fontsize=16, fontweight='bold')
                        
                        # Alert counts by type
                        alert_types = {}
                        alert_severities = {}
                        for alert in alerts:
                            atype = alert.get('alert_type', 'unknown')
                            asev = alert.get('severity', 'unknown')
                            alert_types[atype] = alert_types.get(atype, 0) + 1
                            alert_severities[asev] = alert_severities.get(asev, 0) + 1
                        
                        axes[0].barh(list(alert_types.keys()), list(alert_types.values()), color='steelblue')
                        axes[0].set_xlabel('Count')
                        axes[0].set_title('Alerts by Type')
                        axes[0].grid(True, alpha=0.3, axis='x')
                        
                        colors_sev = {'INFO': 'green', 'WARNING': 'orange', 'CRITICAL': 'red', 'EMERGENCY': 'darkred'}
                        colors = [colors_sev.get(k, 'gray') for k in alert_severities.keys()]
                        axes[1].bar(list(alert_severities.keys()), list(alert_severities.values()), color=colors)
                        axes[1].set_ylabel('Count')
                        axes[1].set_title('Alerts by Severity')
                        axes[1].grid(True, alpha=0.3, axis='y')
                        
                        plt.tight_layout()
                        pdf.savefig(fig, bbox_inches='tight')
                        plt.close()
                    except Exception as e:
                        logger.warning(f"Could not plot alerts: {e}")
            
            logger.info(f"✓ PDF report generated: {output_path}")
            return str(output_path)
        
        except Exception as e:
            logger.error(f"❌ Error generating PDF report: {e}")
            return ""
