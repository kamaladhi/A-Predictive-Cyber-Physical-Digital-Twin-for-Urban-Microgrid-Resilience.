"""
State Estimator - Handles Uncertainty and Sensor Fusion for Digital Twin

Implements Kalman filtering and confidence tracking to maintain robust
state estimates even with noisy measurements.
"""

import numpy as np
from typing import Dict, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class StateEstimate:
    """State variable with uncertainty quantification"""
    value: float
    variance: float  # Uncertainty (sigma^2)
    confidence: float  # 0-1 confidence score
    last_updated: datetime
    
    @property
    def std_dev(self) -> float:
        return np.sqrt(self.variance)
    
    @property
    def confidence_interval_95(self) -> Tuple[float, float]:
        """Return 95% confidence interval (mean +/- 1.96*sigma)"""
        margin = 1.96 * self.std_dev
        return (self.value - margin, self.value + margin)


class MicrogridStateEstimator:
    """
    Extended Kalman Filter for battery SoC and power flow estimation.
    
    State Vector: [SoC, battery_power, generator_power, load]
    Measurement Vector: [measured_SoC, measured_power_flow, measured_load]
    """
    
    def __init__(self, microgrid_id: str, config):
        self.microgrid_id = microgrid_id
        self.config = config
        
        # State dimension
        self.n = 4  # SoC, battery_power, gen_power, load
        
        # Initialize state estimate
        self.state = np.array([
            50.0,  # Initial SoC guess (%)
            0.0,   # Battery power
            0.0,   # Generator power
            config.load_profile.total_critical_load  # Initial load guess
        ])
        
        # State covariance matrix (uncertainty)
        self.P = np.diag([
            100.0,  # High initial uncertainty in SoC
            10.0,   # Power uncertainties
            10.0,
            5.0
        ])
        
        # Process noise covariance (model uncertainty)
        self.Q = np.diag([
            0.2,   # SoC evolves slowly
            3.0,   # Power can change rapidly
            3.0,
            1.0
        ])
        
        # Measurement noise covariance (sensor accuracy)
        # INCREASED to account for real-world measurement variability
        self.R = np.diag([
            15.0,   # SoC sensor +/-15% (accounts for battery estimation error)
            30.0,   # Power meters +/-30 kW (load dynamics)
            50.0    # Load meters +/-50 kW (transient load changes)
        ])
        
        # Model-measurement discrepancy tracking
        self.innovation_history = []
        self.max_innovation_threshold = 25.0  # Trigger recalibration if exceeded
        self._consecutive_large_innovations = 0
        # Dynamic innovation gating: gates scale with innovation history
        self.innovation_norm_gate = float('inf')  # Disabled - use dynamic gating instead
        self.mahalanobis_gate = float('inf')      # Disabled - use dynamic gating instead
        self._warning_cooldown = 0
        self._warning_cooldown_period = 50  # Longer cooldown between warnings
        self._last_innovation_magnitude = 0.0
        
    def predict(self, dt_seconds: float, control_input: Dict) -> None:
        """
        Prediction step: Propagate state forward using physics model.
        
        Args:
            dt_seconds: Time step
            control_input: Expected control actions (generator on/off, etc.)
        """
        dt_hours = dt_seconds / 3600.0
        
        # Simple battery SoC dynamics: dSoC/dt = -(battery_power) / capacity
        battery_capacity_kwh = self.config.battery.nominal_capacity_kwh
        
        # State transition function f(x, u)
        # x_{k+1} = f(x_k, u_k)
        soc = self.state[0]
        battery_power = self.state[1]
        
        # Update SoC based on battery power (negative = discharging)
        dsoc = -(battery_power * dt_hours / battery_capacity_kwh) * 100.0  # Convert to %
        new_soc = np.clip(soc + dsoc, 0.0, 100.0)
        
        # Other states remain approximately constant between measurements
        self.state[0] = new_soc
        
        # Compute Jacobian of state transition (linearization for EKF)
        F = np.eye(self.n)
        F[0, 1] = -(dt_hours / battery_capacity_kwh) * 100.0  # dSoC/d(battery_power)
        
        # Propagate covariance: P = F * P * F^T + Q
        self.P = F @ self.P @ F.T + self.Q * dt_hours
        
    def update(self, measurements: Dict) -> StateEstimate:
        """
        Update step: Correct prediction using actual measurements.
        
        Args:
            measurements: Dict with keys 'soc', 'battery_power', 'load'
            
        Returns:
            Updated SoC estimate with confidence
        """
        # Measurement vector z
        z = np.array([
            measurements.get('battery_soc_percent', self.state[0]),
            measurements.get('battery_power_kw', self.state[1]),
            measurements.get('total_load_kw', self.state[3])
        ])
        
        # Measurement function h(x): Maps state to measurements
        # For this simple case, it's a direct observation
        H = np.array([
            [1, 0, 0, 0],  # SoC measurement
            [0, 1, 0, 0],  # Battery power measurement
            [0, 0, 0, 1]   # Load measurement
        ])
        
        # Predicted measurement: h(x)
        z_pred = H @ self.state
        
        # Innovation (measurement residual)
        y = z - z_pred
        
        # Innovation covariance
        S = H @ self.P @ H.T + self.R

        # Kalman gain
        K = self.P @ H.T @ np.linalg.inv(S)

        # Update state estimate
        self.state = self.state + K @ y

        # Update covariance
        self.P = (np.eye(self.n) - K @ H) @ self.P
        
        # Track innovation for anomaly detection
        innovation_magnitude = float(np.linalg.norm(y))
        self.innovation_history.append(innovation_magnitude)
        if len(self.innovation_history) > 100:
            self.innovation_history.pop(0)
        
        self._last_innovation_magnitude = innovation_magnitude

        # Mahalanobis distance for robust gating (monitoring only)
        try:
            S_inv = np.linalg.inv(S)
            mahalanobis = float(np.sqrt(y.T @ S_inv @ y))
        except np.linalg.LinAlgError:
            mahalanobis = innovation_magnitude  # Fallback

        # ADAPTIVE WARNING: Only warn if innovation is an outlier relative to recent history
        # This prevents spurious warnings during normal load transients
        if len(self.innovation_history) >= 10:
            recent_mean = np.mean(self.innovation_history[-10:])
            recent_std = np.std(self.innovation_history[-10:])
            
            # Only warn if current innovation is >3 sigma above mean
            if recent_std > 0 and (innovation_magnitude - recent_mean) > 3 * recent_std:
                if self._warning_cooldown == 0:
                    logger.warning(
                        f"{self.microgrid_id}: Innovation spike detected (|y|={innovation_magnitude:.2f} "
                        f"vs baseline {recent_mean:.2f}±{recent_std:.2f}, mahal={mahalanobis:.2f}). "
                        f"This may indicate sensor drift or system disturbance."
                    )
                    self._warning_cooldown = self._warning_cooldown_period
        
        if self._warning_cooldown > 0:
            self._warning_cooldown -= 1
        
        # Compute confidence based on uncertainty
        soc_variance = self.P[0, 0]
        confidence = self._compute_confidence(soc_variance)
        
        return StateEstimate(
            value=self.state[0],
            variance=soc_variance,
            confidence=confidence,
            last_updated=datetime.now()
        )
    
    def _compute_confidence(self, variance: float) -> float:
        """
        Map variance to confidence score (0-1).
        High variance -> Low confidence
        """
        # Exponential decay: confidence = exp(-variance / scale)
        scale = 10.0  # Tuning parameter
        return np.exp(-variance / scale)
    
    def detect_anomaly(self, threshold_sigma: float = 3.0) -> Optional[str]:
        """
        Detect if recent measurements are inconsistent with model.
        
        Returns:
            Anomaly description if detected, None otherwise
        """
        if len(self.innovation_history) < 10:
            return None
        
        recent_innovations = self.innovation_history[-10:]
        mean_innovation = np.mean(recent_innovations)
        std_innovation = np.std(recent_innovations)
        
        # Check if latest innovation is outlier
        latest = self.innovation_history[-1]
        if abs(latest - mean_innovation) > threshold_sigma * std_innovation:
            return (
                f"Anomaly detected: Measurement deviates {abs(latest - mean_innovation):.2f} sigma "
                f"from expected. Possible sensor fault or model drift."
            )
        
        return None
    
    def get_time_to_exhaustion(self, current_load_kw: float) -> Optional[float]:
        """
        Predict time until battery exhaustion given current load.
        
        Returns:
            Hours until SoC reaches 0%, or None if charging
        """
        if current_load_kw <= 0:
            return None  # Charging, not depleting
        
        soc = self.state[0]
        capacity_kwh = self.config.battery.nominal_capacity_kwh
        
        energy_remaining_kwh = (soc / 100.0) * capacity_kwh
        hours_remaining = energy_remaining_kwh / current_load_kw
        
        return hours_remaining


class CityStateEstimator:
    """
    Aggregates state estimates from all microgrid estimators.
    Provides city-level situational awareness.
    """
    
    def __init__(self, microgrid_configs: Dict):
        self.mg_estimators = {}
        
        for mg_id, config in microgrid_configs.items():
            self.mg_estimators[mg_id] = MicrogridStateEstimator(mg_id, config)
    
    def update_all(
        self, 
        dt_seconds: float,
        measurements: Dict[str, Dict],
        control_inputs: Dict[str, Dict]
    ) -> Dict[str, StateEstimate]:
        """
        Update all microgrid state estimates.
        
        Args:
            dt_seconds: Time step
            measurements: Dict mapping microgrid_id to measurement dict
            control_inputs: Dict mapping microgrid_id to control actions
            
        Returns:
            Dict mapping microgrid_id to StateEstimate
        """
        estimates = {}
        
        for mg_id, estimator in self.mg_estimators.items():
            # Predict
            estimator.predict(dt_seconds, control_inputs.get(mg_id, {}))
            
            # Update with measurements
            mg_measurements = measurements.get(mg_id, {})
            estimate = estimator.update(mg_measurements)
            
            estimates[mg_id] = estimate
            
            # Check for anomalies
            anomaly = estimator.detect_anomaly()
            if anomaly:
                logger.warning(f"{mg_id}: {anomaly}")
        
        return estimates
    
    def get_city_confidence_score(self, estimates: Dict[str, StateEstimate]) -> float:
        """
        Compute overall confidence in city-level state estimate.
        
        Returns:
            Weighted average confidence (0-1)
        """
        if not estimates:
            return 0.0
        
        total_confidence = sum(est.confidence for est in estimates.values())
        return total_confidence / len(estimates)
    
    def get_critical_predictions(self, estimates: Dict[str, StateEstimate]) -> Dict[str, float]:
        """
        For each microgrid, predict time to critical failure.
        
        Returns:
            Dict mapping microgrid_id to hours until SoC < 20%
        """
        predictions = {}
        
        for mg_id, estimate in estimates.items():
            estimator = self.mg_estimators[mg_id]
            
            # Get current load estimate
            current_load = estimator.state[3]
            
            # Predict exhaustion
            hours = estimator.get_time_to_exhaustion(current_load)
            
            if hours is not None:
                # Adjust for critical threshold (20% SoC)
                critical_hours = hours * (estimate.value - 20.0) / estimate.value
                predictions[mg_id] = max(0.0, critical_hours)
        
        return predictions


# Example usage
if __name__ == "__main__":
    from Microgrid.Hospital.parameters import create_default_config
    
    # Create estimator
    config = create_default_config()
    estimator = MicrogridStateEstimator("hospital", config)
    
    # Simulate measurements with noise
    for t in range(100):
        dt = 900  # 15 minutes
        
        # Predict
        estimator.predict(dt, {})
        
        # Simulate noisy measurement
        true_soc = 80 - t * 0.1  # Slowly discharging
        measured_soc = true_soc + np.random.normal(0, 2.0)  # +/-2% noise
        
        measurements = {
            'battery_soc_percent': measured_soc,
            'battery_power_kw': -10.0,
            'total_load_kw': 50.0
        }
        
        # Update
        estimate = estimator.update(measurements)
        
        if t % 10 == 0:
            ci_low, ci_high = estimate.confidence_interval_95
            print(f"t={t}: SoC = {estimate.value:.1f}% "
                  f"(95% CI: [{ci_low:.1f}, {ci_high:.1f}]), "
                  f"Confidence = {estimate.confidence:.3f}")
    
    # Check anomaly detection
    anomaly = estimator.detect_anomaly()
    if anomaly:
        print(f"\nAnomaly: {anomaly}")
    else:
        print("\nNo anomalies detected. State estimates are consistent with measurements.")