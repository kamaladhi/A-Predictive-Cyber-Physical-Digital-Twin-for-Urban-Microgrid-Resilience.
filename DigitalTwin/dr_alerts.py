"""
Demand Response (DR) Alert Generation Module
Generates cost-based alerts and peak predictions for grid optimization
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


class AlertType(Enum):
    """Types of DR alerts"""
    PEAK_PREDICTED = "peak_predicted"
    HIGH_COST = "high_cost"
    BATTERY_LOW = "battery_low"
    LOAD_SHED_REQUIRED = "load_shed_required"
    GRID_INSTABILITY = "grid_instability"
    COST_OPPORTUNITY = "cost_opportunity"
    RESILIENCE_DEGRADED = "resilience_degraded"


@dataclass
class DRAlert:
    """Demand Response Alert"""
    alert_id: str
    timestamp: datetime
    microgrid_id: str
    alert_type: AlertType
    severity: AlertSeverity
    
    # Alert details
    title: str
    message: str
    recommended_action: str
    
    # Metrics
    current_load_kw: float
    predicted_peak_kw: Optional[float] = None
    estimated_cost_usd: Optional[float] = None
    potential_savings_usd: Optional[float] = None
    
    # Time window
    valid_until: Optional[datetime] = None
    action_required_by: Optional[datetime] = None
    
    # Additional data
    metadata: Dict = None


@dataclass
class CostProfile:
    """Electricity cost profile"""
    # Time-of-Use rates (USD/kWh)
    peak_rate: float = 0.35  # 2-8 PM weekdays
    mid_peak_rate: float = 0.22  # 7-11 AM, 11-2 PM weekdays
    off_peak_rate: float = 0.12  # Nights and weekends
    
    # Demand charges (USD/kW for monthly peak)
    demand_charge: float = 15.0
    
    # Generator costs
    diesel_cost_per_kwh: float = 0.40  # Higher than grid
    generator_maintenance_per_hour: float = 25.0


class DRAlertEngine:
    """
    Demand Response Alert Generation Engine
    
    Responsibilities:
    1. Predict peak demand events
    2. Calculate cost implications
    3. Generate cost-optimization alerts
    4. Recommend DR actions
    """
    
    def __init__(self, cost_profile: CostProfile = None):
        """Initialize DR Alert Engine"""
        self.cost_profile = cost_profile or CostProfile()
        
        # Alert history
        self.alerts_generated: List[DRAlert] = []
        self.alerts_active: List[DRAlert] = []
        
        # Thresholds
        self.peak_threshold_multiplier = 1.3  # Peak if load > 1.3x baseline
        self.high_cost_threshold_usd = 100.0  # Alert if hourly cost > $100
        self.battery_low_threshold_percent = 20.0
        
        # Statistics
        self.total_alerts = 0
        self.cost_savings_realized_usd = 0
        
        logger.info("✓ DR Alert Engine initialized")
    
    def evaluate_and_generate_alerts(self,
                                     microgrid_id: str,
                                     fused_state) -> List[DRAlert]:
        """
        Evaluate fused state and generate appropriate alerts
        
        Args:
            microgrid_id: Microgrid identifier
            fused_state: FusedMicrogridState from data fusion
        
        Returns:
            List of generated alerts
        """
        new_alerts = []
        
        # 1. Peak Prediction Alert
        if fused_state.peak_predicted:
            alert = self._generate_peak_alert(microgrid_id, fused_state)
            new_alerts.append(alert)
        
        # 2. High Cost Alert
        current_cost = self._calculate_current_cost(fused_state)
        if current_cost > self.high_cost_threshold_usd:
            alert = self._generate_high_cost_alert(microgrid_id, fused_state, current_cost)
            new_alerts.append(alert)
        
        # 3. Battery Low Alert
        if fused_state.measured_soc_percent < self.battery_low_threshold_percent:
            alert = self._generate_battery_low_alert(microgrid_id, fused_state)
            new_alerts.append(alert)
        
        # 4. Cost Optimization Opportunity
        opportunity = self._check_cost_opportunity(fused_state)
        if opportunity:
            alert = self._generate_cost_opportunity_alert(microgrid_id, fused_state, opportunity)
            new_alerts.append(alert)
        
        # 5. Resilience Degradation
        if fused_state.estimated_runtime_hours < 2.0:
            alert = self._generate_resilience_alert(microgrid_id, fused_state)
            new_alerts.append(alert)
        
        # Store alerts
        self.alerts_generated.extend(new_alerts)
        self.alerts_active.extend(new_alerts)
        self.total_alerts += len(new_alerts)
        
        # Log generated alerts
        for alert in new_alerts:
            logger.warning(f"⚠️ {alert.severity.value}: {alert.title} ({microgrid_id})")
        
        return new_alerts
    
    def _generate_peak_alert(self, microgrid_id: str, fused_state) -> DRAlert:
        """Generate peak demand prediction alert"""
        predicted_peak = max(fused_state.forecasted_load_6h)
        current_load = fused_state.measured_load_kw
        
        # Calculate potential demand charge
        demand_cost = predicted_peak * self.cost_profile.demand_charge
        
        # Recommended load reduction to avoid peak charge
        recommended_reduction = predicted_peak - current_load * 1.1
        potential_savings = recommended_reduction * self.cost_profile.demand_charge
        
        return DRAlert(
            alert_id=f"PEAK_{microgrid_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            timestamp=datetime.now(),
            microgrid_id=microgrid_id,
            alert_type=AlertType.PEAK_PREDICTED,
            severity=AlertSeverity.WARNING,
            title="Peak Demand Predicted",
            message=f"Load forecast predicts peak of {predicted_peak:.1f} kW in next 6 hours. "
                   f"Current load: {current_load:.1f} kW. "
                   f"Potential demand charge: ${demand_cost:.2f}",
            recommended_action=f"Reduce load by {recommended_reduction:.1f} kW to avoid peak charge. "
                             f"Potential savings: ${potential_savings:.2f}. "
                             f"Consider: Pre-cool buildings, shift EV charging, defer non-critical loads.",
            current_load_kw=current_load,
            predicted_peak_kw=predicted_peak,
            estimated_cost_usd=demand_cost,
            potential_savings_usd=potential_savings,
            valid_until=datetime.now() + timedelta(hours=6),
            action_required_by=datetime.now() + timedelta(hours=2)
        )
    
    def _generate_high_cost_alert(self, microgrid_id: str, fused_state, 
                                  current_cost: float) -> DRAlert:
        """Generate high electricity cost alert"""
        # Calculate if battery discharge would be cheaper
        battery_available_kwh = (fused_state.measured_soc_percent / 100) * 500  # Assume 500 kWh
        potential_savings = current_cost * 0.3  # Could save 30% by using battery
        
        return DRAlert(
            alert_id=f"COST_{microgrid_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            timestamp=datetime.now(),
            microgrid_id=microgrid_id,
            alert_type=AlertType.HIGH_COST,
            severity=AlertSeverity.WARNING,
            title="High Electricity Cost Period",
            message=f"Current hourly cost: ${current_cost:.2f}. "
                   f"Peak rate period in effect (${self.cost_profile.peak_rate:.2f}/kWh).",
            recommended_action=f"Discharge battery to offset grid usage. "
                             f"Battery available: {battery_available_kwh:.1f} kWh. "
                             f"Potential savings: ${potential_savings:.2f}/hour.",
            current_load_kw=fused_state.measured_load_kw,
            estimated_cost_usd=current_cost,
            potential_savings_usd=potential_savings,
            valid_until=datetime.now() + timedelta(hours=1)
        )
    
    def _generate_battery_low_alert(self, microgrid_id: str, fused_state) -> DRAlert:
        """Generate low battery state-of-charge alert"""
        runtime_hours = fused_state.estimated_runtime_hours
        
        return DRAlert(
            alert_id=f"BATTERY_{microgrid_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            timestamp=datetime.now(),
            microgrid_id=microgrid_id,
            alert_type=AlertType.BATTERY_LOW,
            severity=AlertSeverity.CRITICAL if fused_state.measured_soc_percent < 10 else AlertSeverity.WARNING,
            title="Low Battery State of Charge",
            message=f"Battery SOC: {fused_state.measured_soc_percent:.1f}%. "
                   f"Estimated runtime: {runtime_hours:.1f} hours at current load.",
            recommended_action="Charge battery from grid during off-peak hours. "
                             "Consider reducing non-critical loads to extend runtime. "
                             "Verify generator fuel levels for emergency backup.",
            current_load_kw=fused_state.measured_load_kw,
            metadata={'soc': fused_state.measured_soc_percent, 'runtime_hours': runtime_hours}
        )
    
    def _generate_cost_opportunity_alert(self, microgrid_id: str, fused_state,
                                        opportunity: Dict) -> DRAlert:
        """Generate cost optimization opportunity alert"""
        return DRAlert(
            alert_id=f"OPPTY_{microgrid_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            timestamp=datetime.now(),
            microgrid_id=microgrid_id,
            alert_type=AlertType.COST_OPPORTUNITY,
            severity=AlertSeverity.INFO,
            title="Cost Optimization Opportunity",
            message=opportunity['message'],
            recommended_action=opportunity['action'],
            current_load_kw=fused_state.measured_load_kw,
            potential_savings_usd=opportunity['savings'],
            valid_until=datetime.now() + timedelta(hours=opportunity.get('window_hours', 2))
        )
    
    def _generate_resilience_alert(self, microgrid_id: str, fused_state) -> DRAlert:
        """Generate resilience degradation alert"""
        return DRAlert(
            alert_id=f"RESIL_{microgrid_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            timestamp=datetime.now(),
            microgrid_id=microgrid_id,
            alert_type=AlertType.RESILIENCE_DEGRADED,
            severity=AlertSeverity.CRITICAL,
            title="Resilience Degraded - Low Runtime",
            message=f"Battery runtime: {fused_state.estimated_runtime_hours:.1f} hours. "
                   f"Microgrid may not survive extended outage.",
            recommended_action="URGENT: Charge battery immediately. "
                             "Shed non-critical loads. "
                             "Verify generator operational status. "
                             "Critical loads at risk if grid fails.",
            current_load_kw=fused_state.measured_load_kw,
            metadata={'runtime_hours': fused_state.estimated_runtime_hours}
        )
    
    def _calculate_current_cost(self, fused_state) -> float:
        """Calculate current hourly electricity cost"""
        load_kw = fused_state.measured_load_kw
        grid_power_kw = max(0, load_kw - fused_state.measured_pv_kw)  # Grid import
        
        # Get current rate based on time of day
        current_rate = self._get_current_rate()
        
        # Hourly cost
        hourly_cost = grid_power_kw * current_rate
        
        return hourly_cost
    
    def _get_current_rate(self) -> float:
        """Get current electricity rate based on time of day"""
        now = datetime.now()
        hour = now.hour
        is_weekday = now.weekday() < 5
        
        if is_weekday:
            if 14 <= hour < 20:  # 2-8 PM
                return self.cost_profile.peak_rate
            elif (7 <= hour < 11) or (11 <= hour < 14):  # 7-11 AM, 11-2 PM
                return self.cost_profile.mid_peak_rate
        
        return self.cost_profile.off_peak_rate
    
    def _check_cost_opportunity(self, fused_state) -> Optional[Dict]:
        """Check for cost optimization opportunities"""
        # Opportunity 1: Off-peak charging
        current_rate = self._get_current_rate()
        if current_rate == self.cost_profile.off_peak_rate and \
           fused_state.measured_soc_percent < 80:
            return {
                'message': 'Off-peak rate active. Opportunity to charge battery at low cost.',
                'action': f'Charge battery to 80% at ${self.cost_profile.off_peak_rate:.2f}/kWh. '
                         f'Store energy for peak periods.',
                'savings': 50.0,  # Estimated
                'window_hours': 4
            }
        
        # Opportunity 2: Load shifting
        if fused_state.peak_predicted and current_rate < self.cost_profile.peak_rate:
            return {
                'message': 'Peak predicted. Current rates are low - opportunity to shift loads.',
                'action': 'Shift discretionary loads (EV charging, HVAC pre-cooling) to current period.',
                'savings': 30.0,
                'window_hours': 2
            }
        
        return None
    
    def get_active_alerts(self, 
                         microgrid_id: Optional[str] = None,
                         severity: Optional[AlertSeverity] = None) -> List[DRAlert]:
        """Get currently active alerts"""
        alerts = self.alerts_active
        
        if microgrid_id:
            alerts = [a for a in alerts if a.microgrid_id == microgrid_id]
        
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        
        # Filter out expired alerts
        now = datetime.now()
        alerts = [a for a in alerts if not a.valid_until or a.valid_until > now]
        
        return alerts
    
    def acknowledge_alert(self, alert_id: str):
        """Acknowledge and remove alert from active list"""
        self.alerts_active = [a for a in self.alerts_active if a.alert_id != alert_id]
        logger.info(f"✓ Alert acknowledged: {alert_id}")
    
    def get_alert_stats(self) -> Dict:
        """Get alert generation statistics"""
        return {
            'total_alerts_generated': self.total_alerts,
            'active_alerts': len(self.alerts_active),
            'cost_savings_realized_usd': self.cost_savings_realized_usd,
            'alerts_by_type': self._count_alerts_by_type(),
            'alerts_by_severity': self._count_alerts_by_severity()
        }
    
    def _count_alerts_by_type(self) -> Dict[str, int]:
        """Count alerts by type"""
        counts = {}
        for alert in self.alerts_generated:
            alert_type = alert.alert_type.value
            counts[alert_type] = counts.get(alert_type, 0) + 1
        return counts
    
    def _count_alerts_by_severity(self) -> Dict[str, int]:
        """Count alerts by severity"""
        counts = {}
        for alert in self.alerts_generated:
            severity = alert.severity.value
            counts[severity] = counts.get(severity, 0) + 1
        return counts
