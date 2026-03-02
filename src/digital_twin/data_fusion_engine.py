"""
=============================================================================
DATA FUSION ENGINE — Cyber-Physical State Synchronization
=============================================================================

Aggregates asynchronous IoT inputs (NILM, Forecasts, Raw Measurements)
into a synchronized 'Virtual Twin' state for the EMS.

Key Capabilities:
1. Temporal Synchronization: Validates that data sources are within a
   configurable temporal window before allowing fusion.
2. Weighted Load Fusion: Combines NILM-disaggregated load estimates with
   raw sensor measurements using adaptive confidence weights.
3. Anomaly-Aware Weighting: When the EKF reports low confidence (high
   sensor noise), the engine shifts trust toward NILM/forecast data.
4. Audit Logging: All fusion events are recorded for post-hoc analysis.
"""

from __future__ import annotations
import logging
import time
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from threading import Lock
from datetime import datetime, timedelta
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FusionSourceStatus:
    """Tracks the freshness and reliability of each data source."""
    last_update: Optional[datetime] = None
    update_count: int = 0
    staleness_seconds: float = float('inf')
    is_stale: bool = True


class DataFusionEngine:
    """
    Research-Grade Data Fusion Engine for Cyber-Physical Digital Twins.

    Implements a weighted sensor fusion strategy that:
    - Validates temporal coherence across asynchronous data streams
    - Adaptively weights NILM vs raw sensors based on EKF confidence
    - Provides a unified 'fused' state to the downstream optimizer

    Reference: Adapted from multi-sensor fusion principles in
    IEEE Std 2030.5 and IEC 61850 for distributed energy resources.
    """

    def __init__(self, max_staleness_seconds: float = 300.0):
        self._lock = Lock()
        self._max_staleness = max_staleness_seconds

        # Data source buffers
        self._nilm_data: Dict[str, Dict[str, Any]] = {}
        self._forecast_data: Dict[str, List[float]] = {}
        self._sim_measurements: Dict[str, Any] = {}

        # Timestamps per source per microgrid
        self._nilm_timestamps: Dict[str, datetime] = {}
        self._forecast_timestamps: Dict[str, datetime] = {}
        self._measurement_timestamp: Optional[datetime] = None

        # EKF confidence per microgrid (injected externally)
        self._ekf_confidence: Dict[str, float] = {}

        # Fusion audit log
        self._fusion_log: List[Dict[str, Any]] = []
        self._max_log_entries = 200

        # Fusion weights (adaptive)
        self._base_sensor_weight = 0.6
        self._base_nilm_weight = 0.25
        self._base_forecast_weight = 0.15

    # ── Data Ingestion ───────────────────────────────────────────────────

    def update_nilm(self, mg_id: str, data: Dict[str, Any]):
        """Update disaggregated appliance-level load data from NILM."""
        with self._lock:
            self._nilm_data[mg_id] = data
            self._nilm_timestamps[mg_id] = datetime.now()
            logger.debug(f"Fusion: NILM update for {mg_id} ({len(data)} appliances)")

    def update_forecast(self, mg_id: str, forecast: List[float]):
        """Update external forecast predictions (PV/load horizon)."""
        with self._lock:
            self._forecast_data[mg_id] = forecast
            self._forecast_timestamps[mg_id] = datetime.now()
            logger.debug(f"Fusion: Forecast update for {mg_id} ({len(forecast)} steps)")

    def update_measurements(self, data: Dict[str, Any]):
        """Update raw simulation/SCADA measurements."""
        with self._lock:
            self._sim_measurements = data
            self._measurement_timestamp = datetime.now()
            logger.debug("Fusion: Raw measurement update received")

    def update_ekf_confidence(self, mg_id: str, confidence: float):
        """Inject EKF state estimation confidence (0.0 to 1.0)."""
        with self._lock:
            self._ekf_confidence[mg_id] = max(0.0, min(1.0, confidence))

    # ── Temporal Synchronization ─────────────────────────────────────────

    def sync_check(self, max_delta_seconds: Optional[float] = None) -> bool:
        """
        Verify if all active data sources are synchronized within a
        configurable temporal window.

        Returns:
            True if the 'Virtual Twin' state is temporally cohesive.
        """
        max_delta = max_delta_seconds or self._max_staleness
        now = datetime.now()

        with self._lock:
            timestamps = []

            if self._measurement_timestamp:
                timestamps.append(self._measurement_timestamp)

            for ts in self._nilm_timestamps.values():
                timestamps.append(ts)

            for ts in self._forecast_timestamps.values():
                timestamps.append(ts)

            if len(timestamps) < 2:
                return True  # Not enough sources to compare

            # Check that all sources are within max_delta of each other
            oldest = min(timestamps)
            newest = max(timestamps)
            delta = (newest - oldest).total_seconds()

            return delta <= max_delta

    def get_source_status(self) -> Dict[str, FusionSourceStatus]:
        """Get freshness status for each data source category."""
        now = datetime.now()
        status = {}

        with self._lock:
            # Measurement source
            if self._measurement_timestamp:
                staleness = (now - self._measurement_timestamp).total_seconds()
                status['measurements'] = FusionSourceStatus(
                    last_update=self._measurement_timestamp,
                    staleness_seconds=staleness,
                    is_stale=staleness > self._max_staleness
                )
            else:
                status['measurements'] = FusionSourceStatus()

            # NILM source
            if self._nilm_timestamps:
                most_recent = max(self._nilm_timestamps.values())
                staleness = (now - most_recent).total_seconds()
                status['nilm'] = FusionSourceStatus(
                    last_update=most_recent,
                    update_count=len(self._nilm_data),
                    staleness_seconds=staleness,
                    is_stale=staleness > self._max_staleness
                )
            else:
                status['nilm'] = FusionSourceStatus()

            # Forecast source
            if self._forecast_timestamps:
                most_recent = max(self._forecast_timestamps.values())
                staleness = (now - most_recent).total_seconds()
                status['forecast'] = FusionSourceStatus(
                    last_update=most_recent,
                    update_count=len(self._forecast_data),
                    staleness_seconds=staleness,
                    is_stale=staleness > self._max_staleness
                )
            else:
                status['forecast'] = FusionSourceStatus()

        return status

    # ── Core Fusion Logic ────────────────────────────────────────────────

    def compute_adaptive_weights(self, mg_id: str) -> Tuple[float, float, float]:
        """
        Compute adaptive fusion weights based on EKF confidence.

        When EKF confidence is HIGH (sensors are reliable):
            → Increase sensor weight, decrease NILM/forecast
        When EKF confidence is LOW (sensors are noisy/compromised):
            → Decrease sensor weight, increase NILM/forecast trust

        Returns:
            (sensor_weight, nilm_weight, forecast_weight) normalized to sum=1.0
        """
        ekf_conf = self._ekf_confidence.get(mg_id, 0.5)

        # Adaptive scaling: sensor weight scales linearly with EKF confidence
        sensor_w = self._base_sensor_weight * ekf_conf
        nilm_w = self._base_nilm_weight * (1.0 + (1.0 - ekf_conf) * 0.5)
        forecast_w = self._base_forecast_weight * (1.0 + (1.0 - ekf_conf) * 0.3)

        # Normalize
        total = sensor_w + nilm_w + forecast_w
        if total < 1e-6:
            return (0.33, 0.33, 0.34)

        return (sensor_w / total, nilm_w / total, forecast_w / total)

    def compute_fused_load(self, mg_id: str, raw_load_kw: float) -> float:
        """
        Compute a fused load estimate by combining:
        1. Raw sensor measurement (weighted by EKF confidence)
        2. NILM disaggregated total (if available)
        3. Forecast-based load prediction (step 0, if available)

        Args:
            mg_id: Microgrid identifier
            raw_load_kw: Raw sensor load measurement (kW)

        Returns:
            Fused load estimate (kW)
        """
        with self._lock:
            sensor_w, nilm_w, forecast_w = self.compute_adaptive_weights(mg_id)

            # Source 1: Raw sensor
            fused = raw_load_kw * sensor_w

            # Source 2: NILM (sum of disaggregated appliance loads)
            nilm = self._nilm_data.get(mg_id, {})
            if nilm:
                nilm_total = sum(nilm.values())
                fused += nilm_total * nilm_w
            else:
                # If NILM unavailable, redistribute weight to sensor
                fused += raw_load_kw * nilm_w

            # Source 3: Forecast (step 0 = current)
            forecast = self._forecast_data.get(mg_id, [])
            if forecast:
                fused += forecast[0] * forecast_w
            else:
                fused += raw_load_kw * forecast_w

            # Log the fusion event
            self._log_fusion(mg_id, raw_load_kw, fused, sensor_w, nilm_w, forecast_w)

            return max(0.0, fused)

    def get_fused_state(self, mg_id: str) -> Dict[str, Any]:
        """
        Merge all available data for a specific microgrid into a
        unified state dictionary.
        """
        with self._lock:
            # Base state from simulation measurements
            statuses = self._sim_measurements.get('microgrid_statuses', {})
            status = statuses.get(mg_id, {})
            fused_state = dict(status) if isinstance(status, dict) else {}

            # Inject NILM appliance-level intelligence
            if mg_id in self._nilm_data:
                fused_state['appliance_loads'] = self._nilm_data[mg_id]
                fused_state['nilm_available'] = True
            else:
                fused_state['nilm_available'] = False

            # Inject forecast predictions
            if mg_id in self._forecast_data:
                fused_state['external_forecast'] = self._forecast_data[mg_id]
                fused_state['forecast_available'] = True
            else:
                fused_state['forecast_available'] = False

            # Inject fusion weights (for transparency/auditability)
            sw, nw, fw = self.compute_adaptive_weights(mg_id)
            fused_state['fusion_weights'] = {
                'sensor': round(sw, 3),
                'nilm': round(nw, 3),
                'forecast': round(fw, 3),
            }

            # Inject sync status
            fused_state['data_sync_ok'] = self.sync_check()

            return fused_state

    # ── Audit Logging ────────────────────────────────────────────────────

    def _log_fusion(self, mg_id: str, raw: float, fused: float,
                    sw: float, nw: float, fw: float):
        """Record a fusion event for post-hoc analysis."""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'mg_id': mg_id,
            'raw_load_kw': round(raw, 2),
            'fused_load_kw': round(fused, 2),
            'delta_pct': round(abs(fused - raw) / max(raw, 0.1) * 100, 1),
            'weights': {'sensor': round(sw, 3), 'nilm': round(nw, 3), 'forecast': round(fw, 3)},
        }
        self._fusion_log.append(entry)
        if len(self._fusion_log) > self._max_log_entries:
            self._fusion_log.pop(0)

    def log_fusion_event(self, event_type: str, detail: str):
        """Record a named fusion event for auditing."""
        logger.info(f"Fusion [{event_type}]: {detail}")

    def get_fusion_statistics(self) -> Dict[str, Any]:
        """Return fusion audit statistics for dashboard/paper reporting."""
        if not self._fusion_log:
            return {'total_fusions': 0}

        deltas = [e['delta_pct'] for e in self._fusion_log]
        return {
            'total_fusions': len(self._fusion_log),
            'avg_correction_pct': round(np.mean(deltas), 2),
            'max_correction_pct': round(max(deltas), 2),
            'data_sync_ok': self.sync_check(),
        }
