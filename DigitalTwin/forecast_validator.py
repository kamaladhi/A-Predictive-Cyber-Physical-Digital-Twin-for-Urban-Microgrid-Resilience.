"""
Forecast Validator
Computes accuracy metrics and confidence intervals for load forecasts.
"""
from typing import Dict, List, Tuple
import numpy as np
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ForecastValidator:
    """
    Validates load forecasts against actual measured loads.
    Computes MAE, RMSE, MAPE, and confidence intervals.
    """
    
    def __init__(self):
        """Initialize validator."""
        self.forecast_history: Dict[str, List[Dict]] = {}
        self.metrics: Dict[str, Dict] = {}
        logger.info("✓ Forecast Validator initialized")
    
    def record_forecast(self, microgrid_id: str, forecast_kw: float, 
                       actual_kw: float, horizon_hours: int = 1):
        """
        Record a forecast vs actual pair.
        
        Args:
            microgrid_id: Microgrid identifier
            forecast_kw: Forecasted load (kW)
            actual_kw: Actual measured load (kW)
            horizon_hours: Forecast horizon (1h, 6h, etc.)
        """
        if microgrid_id not in self.forecast_history:
            self.forecast_history[microgrid_id] = []
        
        entry = {
            'timestamp': datetime.now().isoformat(),
            'forecast_kw': forecast_kw,
            'actual_kw': actual_kw,
            'error_kw': forecast_kw - actual_kw,
            'error_percent': ((forecast_kw - actual_kw) / actual_kw * 100) if actual_kw > 0 else 0,
            'horizon_hours': horizon_hours
        }
        
        self.forecast_history[microgrid_id].append(entry)
    
    def compute_mae(self, microgrid_id: str) -> float:
        """Mean Absolute Error (kW)."""
        if microgrid_id not in self.forecast_history:
            return 0
        
        history = self.forecast_history[microgrid_id]
        if not history:
            return 0
        
        errors = [abs(h['error_kw']) for h in history]
        return np.mean(errors)
    
    def compute_rmse(self, microgrid_id: str) -> float:
        """Root Mean Square Error (kW)."""
        if microgrid_id not in self.forecast_history:
            return 0
        
        history = self.forecast_history[microgrid_id]
        if not history:
            return 0
        
        errors = [h['error_kw'] ** 2 for h in history]
        return np.sqrt(np.mean(errors))
    
    def compute_mape(self, microgrid_id: str) -> float:
        """Mean Absolute Percentage Error (%)."""
        if microgrid_id not in self.forecast_history:
            return 0
        
        history = self.forecast_history[microgrid_id]
        if not history:
            return 0
        
        errors = [abs(h['error_percent']) for h in history if h['actual_kw'] > 0]
        return np.mean(errors) if errors else 0
    
    def compute_confidence_interval(self, microgrid_id: str, 
                                   confidence_level: float = 0.95) -> Tuple[float, float]:
        """
        Compute confidence interval for forecast error.
        
        Args:
            microgrid_id: Microgrid identifier
            confidence_level: Confidence level (0.95 = 95%)
        
        Returns:
            (lower_bound, upper_bound) in kW
        """
        if microgrid_id not in self.forecast_history:
            return (0, 0)
        
        history = self.forecast_history[microgrid_id]
        if len(history) < 2:
            return (0, 0)
        
        errors = [h['error_kw'] for h in history]
        mean_error = np.mean(errors)
        std_error = np.std(errors)
        
        # Z-score for 95% confidence: 1.96
        z_score = 1.96 if confidence_level == 0.95 else 1.645
        
        margin = z_score * std_error / np.sqrt(len(errors))
        return (mean_error - margin, mean_error + margin)
    
    def get_forecast_metrics(self, microgrid_id: str) -> Dict:
        """Get all metrics for a microgrid."""
        if microgrid_id not in self.forecast_history:
            return {}
        
        history = self.forecast_history[microgrid_id]
        if not history:
            return {}
        
        mae = self.compute_mae(microgrid_id)
        rmse = self.compute_rmse(microgrid_id)
        mape = self.compute_mape(microgrid_id)
        ci_lower, ci_upper = self.compute_confidence_interval(microgrid_id)
        
        avg_actual = np.mean([h['actual_kw'] for h in history])
        bias = np.mean([h['error_kw'] for h in history])
        
        return {
            'microgrid_id': microgrid_id,
            'samples': len(history),
            'mae_kw': mae,
            'rmse_kw': rmse,
            'mape_percent': mape,
            'bias_kw': bias,
            'confidence_interval_95': (ci_lower, ci_upper),
            'avg_actual_load_kw': avg_actual,
            'forecast_quality': self._rate_quality(mae, avg_actual)
        }
    
    def _rate_quality(self, mae: float, avg_load: float) -> str:
        """Rate forecast quality based on MAPE."""
        if avg_load == 0:
            return "UNKNOWN"
        
        mape = (mae / avg_load) * 100
        
        if mape < 5:
            return "EXCELLENT"
        elif mape < 10:
            return "GOOD"
        elif mape < 20:
            return "FAIR"
        else:
            return "POOR"
    
    def get_all_metrics(self) -> Dict[str, Dict]:
        """Get metrics for all microgrids."""
        return {mg_id: self.get_forecast_metrics(mg_id) 
                for mg_id in self.forecast_history.keys()}
    
    def report(self) -> str:
        """Generate a text report of all metrics."""
        report_lines = ["Forecast Validation Report", "=" * 60]
        
        for mg_id, metrics in self.get_all_metrics().items():
            if not metrics:
                continue
            
            report_lines.append(f"\nMicrogrid: {mg_id}")
            report_lines.append(f"  Samples: {metrics['samples']}")
            report_lines.append(f"  MAE: {metrics['mae_kw']:.2f} kW")
            report_lines.append(f"  RMSE: {metrics['rmse_kw']:.2f} kW")
            report_lines.append(f"  MAPE: {metrics['mape_percent']:.2f}%")
            report_lines.append(f"  Bias: {metrics['bias_kw']:.2f} kW")
            ci_lower, ci_upper = metrics['confidence_interval_95']
            report_lines.append(f"  95% CI: [{ci_lower:.2f}, {ci_upper:.2f}] kW")
            report_lines.append(f"  Quality: {metrics['forecast_quality']}")
        
        return "\n".join(report_lines)
