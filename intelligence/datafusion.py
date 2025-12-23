import logging
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

from Core.statemodel import MicrogridState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class FusionMetrics:
    """Metrics from data fusion analysis"""
    data_quality_score: float  # 0-1
    nilm_forecast_correlation: float  # -1 to 1
    anomaly_count: int
    unexplained_load: float  # kW
    prediction_accuracy: float  # 0-1
    uncertainty_score: float = 0.0  # ADDED - overall uncertainty metric


class DataFusionEngine:
    """
    -COMPLIANT Data Fusion Engine
    Implements UT-inspired uncertainty propagation suitable for real-time Digital Twins
    Key capabilities:
    1. Anomaly detection
    2. Data quality assessment with uncertainty
    3. Predictive validation
    4. UT-based uncertainty propagation ( requirement)
    """
    
    def __init__(self, redis_client=None):
        self.redis_client = redis_client
        
        # Historical data
        self.history_window = 24  # hours
        self.nilm_history = []
        self.forecast_history = []
        
        # Thresholds
        self.anomaly_threshold_std = 2.5
        self.min_data_quality = 0.7
        self.high_uncertainty_threshold = 0.2  # ADDED - 's uncertainty handling
        
        logger.info("Data Fusion Engine initialized (-compliant with UT)")
    
    def fuse_current_state(self, state: MicrogridState) -> FusionMetrics:
        """
        Analyze current state with uncertainty propagation
        Implements 's Unscented Transform methodology
        """
        metrics = FusionMetrics(
            data_quality_score=0.0,
            nilm_forecast_correlation=0.0,
            anomaly_count=0,
            unexplained_load=0.0,
            prediction_accuracy=0.0,
            uncertainty_score=0.0
        )
        
        try:
            # 1. Check data completeness
            quality_score = self._assess_data_quality(state)
            metrics.data_quality_score = quality_score
            
            # 2. Validate appliance sum
            unexplained = self._calculate_unexplained_load(state)
            metrics.unexplained_load = unexplained
            
            # 3. Detect anomalies
            anomalies = self._detect_anomalies(state)
            metrics.anomaly_count = len(anomalies)
            
            # 4. Validate forecast accuracy
            if state.forecast:
                accuracy = self._validate_forecast_accuracy(state)
                metrics.prediction_accuracy = accuracy
            
            # 5. UT-inspired uncertainty propagation
            solar_ut_std = self._apply_unscented_transform(state, 'solar')
            wind_ut_std = self._apply_unscented_transform(state, 'wind')
            load_ut_std = self._apply_unscented_transform(state, 'load')
            
            # Check if uncertainty is too high ('s threshold)
            if solar_ut_std > self.high_uncertainty_threshold or \
               wind_ut_std > self.high_uncertainty_threshold:
                metrics.anomaly_count += 1
                logger.warning("High RER uncertainty detected via UT")
            
            # Calculate overall uncertainty score
            metrics.uncertainty_score = np.mean([solar_ut_std, wind_ut_std, load_ut_std])
            
            logger.debug(f"Fusion metrics: Quality={quality_score:.2f}, "
                        f"Anomalies={len(anomalies)}, Uncertainty={metrics.uncertainty_score:.3f}")
            
        except Exception as e:
            logger.error(f"Fusion analysis error: {e}")
        
        return metrics
    
    def _apply_unscented_transform(self, state: MicrogridState, variable: str = 'solar') -> float:
        """
        Propagate uncertainty using Unscented Transform
        This is the  METHOD for RER/market/load modeling
        
        The UT propagates uncertainty through non-linear transformations
        (e.g., generation * price = cost) more accurately than linearization
        """
        if variable == 'solar':
            mean = state.generation.solar
            std = state.generation.solar_uncertainty or (mean * 0.1)  # Default 10%
        elif variable == 'wind':
            mean = state.generation.wind
            std = state.generation.wind_uncertainty or (mean * 0.1)
        elif variable == 'load':
            mean = state.total_load
            std = state.total_load_uncertainty or (mean * 0.05)
        else:
            return 0.0
        
        if std == 0 or mean == 0:
            return 0.0
        
        # Unscented Transform: Create sigma points (simplified 3-point for 1D)
        alpha = 0.001  # UT scaling parameter
        kappa = 0  # Secondary scaling
        lambda_param = alpha**2 * (1 + kappa) - 1
        
        # Generate sigma points
        sqrt_term = np.sqrt((1 + lambda_param) * std**2)
        sigma_points = np.array([
            mean - sqrt_term,
            mean,
            mean + sqrt_term
        ])
        
        # Weights for mean and covariance
        w_m = np.array([
            lambda_param / (1 + lambda_param),
            0.5 / (1 + lambda_param),
            0.5 / (1 + lambda_param)
        ])
        
        # Non-linear transform: cost under uncertainty ('s market expenses)
        # Transform: generation/load * price
        current_price = state.grid.current_price
        price_uncertainty = state.grid.price_uncertainty or (current_price * 0.05)
        
        # Price sigma points (simplified)
        price_sigma = np.array([
            current_price - price_uncertainty,
            current_price,
            current_price + price_uncertainty
        ])
        
        # Transform through non-linear function (multiplication)
        transformed = []
        for sp, ps in zip(sigma_points, price_sigma):
            # Cost = power * price (non-linear in presence of uncertainty)
            transformed.append(sp * ps)
        
        transformed = np.array(transformed)
        
        # Calculate transformed mean and std
        mean_transformed = np.sum(w_m * transformed)
        
        # Covariance
        cov = 0
        for i in range(len(sigma_points)):
            diff = transformed[i] - mean_transformed
            cov += w_m[i] * diff**2
        
        std_transformed = np.sqrt(cov)
        
        # Return normalized uncertainty (relative to mean)
        if mean_transformed > 0:
            relative_uncertainty = std_transformed / mean_transformed
        else:
            relative_uncertainty = std_transformed
        
        logger.debug(f"UT for {variable}: Mean={mean_transformed:.2f}, "
                    f"Std={std_transformed:.3f}, Relative={relative_uncertainty:.3f}")
        
        return relative_uncertainty
    
    def _assess_data_quality(self, state: MicrogridState) -> float:
        """Assess quality of incoming data with uncertainty consideration"""
        score = 1.0
        
        # Check for missing appliances
        if len(state.appliances) == 0:
            score -= 0.3
        
        # Check NILM confidence
        if state.nilm_confidence < 0.8:
            score -= 0.2
        
        # Check for stale data
        age_seconds = (datetime.now() - state.timestamp).total_seconds()
        if age_seconds > 300:  # >5 minutes
            score -= 0.3
        
        # Check generation validity
        if state.generation.total < 0:
            score -= 0.5
        
        # ADDED: Penalize high uncertainty
        if state.total_load_uncertainty > state.total_load * 0.2:  # >20%
            score -= 0.15
        
        if state.generation.solar_uncertainty > state.generation.solar * 0.3:  # >30%
            score -= 0.15
        
        return max(0.0, score)
    
    def _calculate_unexplained_load(self, state: MicrogridState) -> float:
        """Calculate difference between total and appliance sum"""
        appliance_sum = sum(app.power for app in state.appliances.values())
        unexplained = state.total_load - appliance_sum
        
        if abs(unexplained) > 0.5:
            logger.warning(f"Unexplained load: {unexplained:.2f}kW "
                        f"(Total: {state.total_load:.2f}kW, "
                        f"Appliances: {appliance_sum:.2f}kW)")
        
        return unexplained
    
    def _detect_anomalies(self, state: MicrogridState) -> List[Dict]:
        """Detect unusual patterns"""
        anomalies = []
        
        # 1. Sudden load spike
        if len(self.nilm_history) > 10:
            recent_loads = [h['total_load'] for h in self.nilm_history[-10:]]
            avg_load = np.mean(recent_loads)
            std_load = np.std(recent_loads)
            
            if state.total_load > avg_load + (self.anomaly_threshold_std * std_load):
                anomalies.append({
                    'type': 'load_spike',
                    'severity': 'high',
                    'message': f'Load spike: {state.total_load:.2f}kW (avg: {avg_load:.2f}kW)',
                    'timestamp': state.timestamp
                })
        
        # 2. Negative generation
        if state.generation.total < 0:
            anomalies.append({
                'type': 'negative_generation',
                'severity': 'critical',
                'message': 'Negative generation detected',
                'timestamp': state.timestamp
            })
        
        # 3. Battery SOC out of bounds
        if state.battery.soc < 0 or state.battery.soc > 100:
            anomalies.append({
                'type': 'battery_soc_invalid',
                'severity': 'critical',
                'message': f'Invalid battery SOC: {state.battery.soc}%',
                'timestamp': state.timestamp
            })
        
        # 4. Simultaneous import/export
        if state.grid.import_power > 0 and state.grid.export_power > 0:
            anomalies.append({
                'type': 'grid_conflict',
                'severity': 'medium',
                'message': 'Simultaneous grid import and export',
                'timestamp': state.timestamp
            })
        
        # 5. High unexplained load
        unexplained = self._calculate_unexplained_load(state)
        if abs(unexplained) > 1.0:
            anomalies.append({
                'type': 'unexplained_load',
                'severity': 'medium',
                'message': f'High unexplained load: {unexplained:.2f}kW',
                'timestamp': state.timestamp
            })
        
        # 6. ADDED: High uncertainty anomaly ( requirement)
        total_uncertainty = (
            state.total_load_uncertainty +
            state.generation.solar_uncertainty +
            state.generation.wind_uncertainty
        )
        if total_uncertainty > 2.0:  # Threshold
            anomalies.append({
                'type': 'high_uncertainty',
                'severity': 'warning',
                'message': f'High system uncertainty: {total_uncertainty:.2f}kW',
                'timestamp': state.timestamp
            })
        
        return anomalies
    
    def _validate_forecast_accuracy(self, state: MicrogridState) -> float:
        """Compare forecasted vs actual values"""
        if not state.forecast:
            return 0.0
        
        accuracy_scores = []
        
        # Solar forecast accuracy
        if state.forecast.solar_forecast:
            predicted = state.forecast.solar_forecast[0]['value']
            actual = state.generation.solar
            
            if predicted > 0:
                error = abs(predicted - actual) / predicted
                accuracy = 1.0 - min(error, 1.0)
                accuracy_scores.append(accuracy)
        
        # Wind forecast accuracy
        if state.forecast.wind_forecast:
            predicted = state.forecast.wind_forecast[0]['value']
            actual = state.generation.wind
            
            if predicted > 0:
                error = abs(predicted - actual) / predicted
                accuracy = 1.0 - min(error, 1.0)
                accuracy_scores.append(accuracy)
        
        return np.mean(accuracy_scores) if accuracy_scores else 0.0
    
    def create_optimization_recommendations(self, state: MicrogridState) -> List[Dict]:
        """Generate efficiency improvement recommendations"""
        recommendations = []
        
        # 1. Excess solar + low battery -> charge
        if state.generation.solar > state.total_load and state.battery.soc < 80:
            excess = state.generation.solar - state.total_load
            recommendations.append({
                'type': 'battery_charge',
                'priority': 'high',
                'message': f'Charge battery with excess solar ({excess:.2f}kW)',
                'target_power': min(excess, state.battery.max_charge_rate),
                'expected_benefit': 'Store energy for evening peak'
            })
        
        # 2. Peak price + high battery -> discharge
        if state.grid.current_price > 0.040 and state.battery.soc > 30:
            recommendations.append({
                'type': 'battery_discharge',
                'priority': 'high',
                'message': 'Discharge battery during high price',
                'target_power': min(state.total_load * 0.5, state.battery.max_discharge_rate),
                'expected_benefit': f'Save ${state.grid.current_price * 1.0:.2f}/hour'
            })
        
        # 3. Low renewable penetration -> shift loads
        if state.metrics.renewable_penetration < 0.3:
            shiftable = [app for app in state.appliances.values() if app.is_shiftable and app.power > 0.5]
            
            if shiftable:
                recommendations.append({
                    'type': 'load_shift',
                    'priority': 'medium',
                    'message': f'Shift {len(shiftable)} appliances to high solar period',
                    'appliances': [app.name for app in shiftable],
                    'expected_benefit': 'Increase renewable usage'
                })
        
        return recommendations
    
    def calculate_cost_savings_potential(self, state: MicrogridState) -> Dict:
        """Calculate potential savings with uncertainty consideration"""
        savings = {
            'battery_optimization': 0.0,
            'load_shifting': 0.0,
            'peak_shaving': 0.0,
            'total_daily_potential': 0.0,
            'uncertainty_impact': 0.0  # ADDED
        }
        
        # Battery optimization
        if state.forecast and state.forecast.price_forecast:
            peak_price = state.forecast.get_peak_price_value()
            current_price = state.grid.current_price
            
            if peak_price > current_price and state.battery.soc > 30:
                discharge_energy = (state.battery.soc - 30) / 100 * state.battery.capacity
                savings['battery_optimization'] = discharge_energy * (peak_price - current_price)
        
        # Load shifting
        shiftable_power = sum(app.power for app in state.appliances.values() if app.is_shiftable)
        
        if shiftable_power > 0 and state.grid.current_price > 0.035:
            price_diff = 0.010
            savings['load_shifting'] = shiftable_power * 3 * price_diff
        
        # Calculate uncertainty impact on savings
        price_unc = state.grid.price_uncertainty
        if price_unc > 0:
            savings['uncertainty_impact'] = savings['total_daily_potential'] * (price_unc / state.grid.current_price)
        
        savings['total_daily_potential'] = sum([
            savings['battery_optimization'],
            savings['load_shifting'],
            savings['peak_shaving']
        ])
        
        return savings
    
    def add_to_history(self, state: MicrogridState):
        """Store state in history"""
        state_dict = {
            'timestamp': state.timestamp,
            'total_load': state.total_load,
            'generation': state.generation.total,
            'battery_soc': state.battery.soc,
            'uncertainty': state.total_load_uncertainty
        }
        
        self.nilm_history.append(state_dict)
        
        # Keep only last 24 hours
        cutoff = datetime.now() - timedelta(hours=self.history_window)
        self.nilm_history = [h for h in self.nilm_history if h['timestamp'] > cutoff]