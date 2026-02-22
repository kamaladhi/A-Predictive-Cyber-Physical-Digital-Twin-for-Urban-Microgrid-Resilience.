from __future__ import annotations
"""
================================================================================
INTER-MICROGRID RESOURCE SHARING MODULE
================================================================================

Implements a virtual energy exchange bus for coordinated power sharing between
heterogeneous microgrids in a digital twin simulation.

Architecture:
    - EnergyExchangeBus: Central coordinator that collects surplus/deficit reports
      and allocates transfers using priority-weighted algorithm.
    - Integrates with CityEMS via SupervisoryCommand.export_power_kw / import_power_kw
    - Activated only during outage/emergency modes (not in normal grid-connected).

Key Design Decisions:
    - Single-bus topology: all microgrids equidistant (no distance-based loss).
    - Fixed transfer efficiency (default 95%) models transformer + line losses.
    - Priority-weighted allocation: higher-priority MGs served first.
    - No market/bidding — purely cooperative, priority-driven.

Reference: Methodology Section 6 (EMS Coordination Framework)
================================================================================
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set, TYPE_CHECKING
from datetime import datetime
from enum import Enum
import logging
import uuid

from src.ems.common import MicrogridPriority

if TYPE_CHECKING:
    try:
        from src.ems.city_ems import SupervisoryCommand
    except ImportError:
        from src.ems.city_ems import SupervisoryCommand

logger = logging.getLogger(__name__)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class SurplusReport:
    """Report from a microgrid that has exportable energy."""
    microgrid_id: str
    timestamp: datetime
    available_kw: float          # Maximum exportable power (kW)
    duration_hours: float        # Estimated duration of surplus
    battery_soc_percent: float   # Current SOC (higher = more willing to share)
    source: str                  # "pv", "battery", "generator"


@dataclass
class EnergyRequest:
    """Request from a microgrid that needs imported energy."""
    microgrid_id: str
    timestamp: datetime
    deficit_kw: float            # Power shortfall (kW)
    priority: MicrogridPriority  # Microgrid priority level
    critical_load_at_risk: bool  # True if critical loads are at risk
    current_soc: float           # Current battery SOC (%)


@dataclass
class TransferAllocation:
    """Allocated energy transfer between two microgrids."""
    transfer_id: str
    timestamp: datetime
    from_mg: str                 # Source microgrid ID
    to_mg: str                   # Destination microgrid ID
    power_kw: float              # Gross power at source (kW)
    delivered_kw: float          # Net power at destination (kW, after loss)
    duration_hours: float        # Expected duration
    reason: str                  # Human-readable reason


@dataclass
class ResourceSharingMetrics:
    """Accumulated metrics for resource sharing performance."""
    total_energy_exchanged_kwh: float = 0.0
    total_energy_lost_kwh: float = 0.0
    total_transfers: int = 0
    transfers_by_recipient: Dict[str, float] = field(default_factory=dict)
    transfers_by_donor: Dict[str, float] = field(default_factory=dict)
    active_transfer_steps: int = 0
    total_steps: int = 0

    @property
    def transfer_efficiency(self) -> float:
        """Ratio of delivered energy to sourced energy."""
        total_sourced = self.total_energy_exchanged_kwh + self.total_energy_lost_kwh
        if total_sourced <= 0:
            return 1.0
        return self.total_energy_exchanged_kwh / total_sourced

    @property
    def utilization_rate(self) -> float:
        """Fraction of steps with active transfers."""
        if self.total_steps <= 0:
            return 0.0
        return self.active_transfer_steps / self.total_steps


# =============================================================================
# ENERGY EXCHANGE BUS
# =============================================================================

class EnergyExchangeBus:
    """
    Virtual energy exchange bus for inter-microgrid power sharing.

    Collects surplus reports and energy requests from all microgrids,
    then allocates transfers using a priority-weighted algorithm.
    Higher-priority microgrids (Hospital > University > Industrial > Residential)
    receive energy first when multiple deficits exist.

    Parameters:
        bus_capacity_kw: Maximum total power transferable through the bus (kW).
        transfer_efficiency: Fraction of source power delivered (0-1).
        min_transfer_kw: Minimum transfer size to avoid trivial exchanges.
        max_simultaneous: Maximum number of concurrent transfers.
        min_donor_soc: Minimum SOC a donor must retain after sharing.
    """

    # Priority weights for allocation (higher = served first)
    PRIORITY_WEIGHTS = {
        MicrogridPriority.CRITICAL: 4.0,   # Hospital
        MicrogridPriority.HIGH: 3.0,       # University
        MicrogridPriority.MEDIUM: 2.0,     # Industrial
        MicrogridPriority.LOW: 1.0,        # Residential
    }

    def __init__(
        self,
        bus_capacity_kw: float = 200.0,
        transfer_efficiency: float = 0.95,
        min_transfer_kw: float = 5.0,
        max_simultaneous: int = 3,
        min_donor_soc: float = 30.0,
    ):
        self.bus_capacity_kw = bus_capacity_kw
        self.transfer_efficiency = transfer_efficiency
        self.min_transfer_kw = min_transfer_kw
        self.max_simultaneous = max_simultaneous
        self.min_donor_soc = min_donor_soc
        self.failed_links: Set[str] = set()

        # Per-timestep buffers (cleared each step)
        self._surplus_reports: List[SurplusReport] = []
        self._energy_requests: List[EnergyRequest] = []
        self._active_transfers: List[TransferAllocation] = []

        # Accumulated metrics
        self.metrics = ResourceSharingMetrics()

        # Transfer log (full history)
        self.transfer_log: List[TransferAllocation] = []

        logger.info(
            f"EnergyExchangeBus initialized: capacity={bus_capacity_kw} kW, "
            f"efficiency={transfer_efficiency:.0%}, min_donor_soc={min_donor_soc}%"
        )

    # ─────────────────────────────────────────────────────────────
    # PUBLIC API: Report / Request / Allocate
    # ─────────────────────────────────────────────────────────────

    def report_surplus(self, report: SurplusReport) -> None:
        """Register a surplus report from a microgrid."""
        if report.available_kw >= self.min_transfer_kw:
            self._surplus_reports.append(report)

    def request_energy(self, request: EnergyRequest) -> None:
        """Register an energy request from a deficit microgrid."""
        if request.deficit_kw >= self.min_transfer_kw:
            self._energy_requests.append(request)

    def set_failed_links(self, failed_ids: Set[str]) -> None:
        """Update the set of microgrids with cyber-link failures."""
        self.failed_links = failed_ids
        if failed_ids:
            logger.warning(f"Cyber-Link Failures detected for: {failed_ids}. Resource sharing suspended for these nodes.")

    def allocate_transfers(self) -> List[TransferAllocation]:
        """
        Core allocation algorithm: match surplus to deficit using priority weights.

        Algorithm:
        1. Sort requests by priority (highest first), then by deficit size.
        2. Sort surplus reports by available power (largest first).
        3. For each request (in priority order), allocate from available surplus
           up to the deficit amount, respecting bus capacity and max simultaneous.
        4. Record transfers and update metrics.

        Returns:
            List of TransferAllocation objects for this timestep.
        """
        self._active_transfers = []

        # ── Cyber-Resilience Filter ──
        # Ignore microgrids with failed communication links
        active_requests = [r for r in self._energy_requests if r.microgrid_id not in self.failed_links]
        active_surplus = [s for s in self._surplus_reports if s.microgrid_id not in self.failed_links]

        if not active_surplus or not active_requests:
            self.metrics.total_steps += 1
            return []

        # Sort requests: critical-load-at-risk first, then priority, then deficit size
        sorted_requests = sorted(
            active_requests,
            key=lambda r: (
                0 if r.critical_load_at_risk else 1,
                r.priority.value,          # Lower value = higher priority
                -r.deficit_kw,             # Larger deficit first
            )
        )

        # Sort surplus: largest available first
        sorted_surplus = sorted(
            active_surplus,
            key=lambda s: -s.available_kw
        )

        # Track remaining capacity
        remaining_bus_capacity = self.bus_capacity_kw
        remaining_surplus = {s.microgrid_id: s.available_kw for s in sorted_surplus}
        remaining_deficit = {r.microgrid_id: r.deficit_kw for r in sorted_requests}
        transfer_count = 0

        for request in sorted_requests:
            if transfer_count >= self.max_simultaneous:
                break
            if remaining_bus_capacity <= self.min_transfer_kw:
                break
            if remaining_deficit[request.microgrid_id] < self.min_transfer_kw:
                continue

            # Try to fulfill this request from available surplus
            for surplus in sorted_surplus:
                if surplus.microgrid_id == request.microgrid_id:
                    continue  # Can't self-supply via bus
                if remaining_surplus.get(surplus.microgrid_id, 0) < self.min_transfer_kw:
                    continue
                if remaining_deficit[request.microgrid_id] < self.min_transfer_kw:
                    break

                # Calculate transfer amount
                available = remaining_surplus[surplus.microgrid_id]
                needed = remaining_deficit[request.microgrid_id]
                max_by_bus = remaining_bus_capacity

                gross_kw = min(available, needed / self.transfer_efficiency, max_by_bus)
                delivered_kw = gross_kw * self.transfer_efficiency

                if delivered_kw < self.min_transfer_kw:
                    continue

                # Create transfer
                transfer = TransferAllocation(
                    transfer_id=str(uuid.uuid4())[:8],
                    timestamp=request.timestamp,
                    from_mg=surplus.microgrid_id,
                    to_mg=request.microgrid_id,
                    power_kw=round(gross_kw, 2),
                    delivered_kw=round(delivered_kw, 2),
                    duration_hours=min(surplus.duration_hours, 0.25),  # 15-min step
                    reason=f"Priority {request.priority.name}: "
                           f"{surplus.microgrid_id}→{request.microgrid_id}"
                )

                self._active_transfers.append(transfer)
                self.transfer_log.append(transfer)
                transfer_count += 1

                # Update remaining capacity
                remaining_surplus[surplus.microgrid_id] -= gross_kw
                remaining_deficit[request.microgrid_id] -= delivered_kw
                remaining_bus_capacity -= gross_kw

                logger.info(
                    f"TRANSFER: {transfer.from_mg} → {transfer.to_mg} "
                    f"{transfer.power_kw:.1f}kW (delivered {transfer.delivered_kw:.1f}kW) "
                    f"reason={transfer.reason}"
                )

                if transfer_count >= self.max_simultaneous:
                    break

        # Update metrics
        self._update_metrics()
        self.metrics.total_steps += 1

        return self._active_transfers

    def apply_to_commands(
        self, commands: Dict[str, SupervisoryCommand]
    ) -> Dict[str, SupervisoryCommand]:
        """
        Write export/import power setpoints into existing SupervisoryCommands.

        This method ONLY touches export_power_kw and import_power_kw fields,
        leaving all other command fields untouched.

        Args:
            commands: Existing supervisory commands keyed by microgrid_id.

        Returns:
            Updated commands with export/import power set.
        """
        # Aggregate transfers per microgrid
        exports: Dict[str, float] = {}
        imports: Dict[str, float] = {}

        for transfer in self._active_transfers:
            exports[transfer.from_mg] = (
                exports.get(transfer.from_mg, 0.0) + transfer.power_kw
            )
            imports[transfer.to_mg] = (
                imports.get(transfer.to_mg, 0.0) + transfer.delivered_kw
            )

        # Apply to commands
        for mg_id, cmd in commands.items():
            if mg_id in exports:
                cmd.export_power_kw = exports[mg_id]
            if mg_id in imports:
                cmd.import_power_kw = imports[mg_id]

        return commands

    def clear_step(self) -> None:
        """Clear per-timestep buffers. Call at the start of each simulation step."""
        self._surplus_reports.clear()
        self._energy_requests.clear()
        self._active_transfers.clear()

    def get_active_transfers(self) -> List[TransferAllocation]:
        """Return the current timestep's active transfers."""
        return list(self._active_transfers)

    def get_metrics(self) -> Dict:
        """Return current metrics as a dictionary."""
        m = self.metrics
        return {
            'total_energy_exchanged_kwh': round(m.total_energy_exchanged_kwh, 2),
            'total_energy_lost_kwh': round(m.total_energy_lost_kwh, 2),
            'total_transfers': m.total_transfers,
            'transfer_efficiency': round(m.transfer_efficiency, 4),
            'utilization_rate': round(m.utilization_rate, 4),
            'transfers_by_recipient': dict(m.transfers_by_recipient),
            'transfers_by_donor': dict(m.transfers_by_donor),
        }

    # ─────────────────────────────────────────────────────────────
    # HELPER: Surplus/Deficit Detection from microgridStatus
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def detect_surplus(status, mg_info) -> Optional[SurplusReport]:
        """
        Detect if a microgrid has exportable surplus.

        Surplus = generation exceeds load AND battery is reasonably charged.

        Args:
            status: MicrogridStatus from city_ems
            mg_info: MicrogridInfo from src.ems.city_ems registry

        Returns:
            SurplusReport if surplus exists, else None.
        """
        if not mg_info.can_share_power:
            return None

        net_generation = (
            status.pv_generation_kw + status.generator_power_kw - status.total_load_kw
        )

        # Only share if net surplus AND battery is healthy
        if net_generation > 5.0 and status.battery_soc_percent > 60.0:
            # Don't export more than half the surplus (keep local buffer)
            exportable = net_generation * 0.5
            source = "pv" if status.pv_generation_kw > status.generator_power_kw else "generator"
            return SurplusReport(
                microgrid_id=status.microgrid_id,
                timestamp=status.timestamp,
                available_kw=exportable,
                duration_hours=0.5,  # Conservative estimate
                battery_soc_percent=status.battery_soc_percent,
                source=source,
            )

        # Battery-based surplus: SOC very high, can discharge to share
        if status.battery_soc_percent > 85.0 and not status.is_islanded:
            # Share up to 20% of battery discharge rate
            batt_share = min(
                mg_info.battery_capacity_kwh * 0.10,  # 10% of capacity per hour
                50.0,  # Cap at 50 kW
            )
            if batt_share >= 5.0:
                return SurplusReport(
                    microgrid_id=status.microgrid_id,
                    timestamp=status.timestamp,
                    available_kw=batt_share,
                    duration_hours=0.25,
                    battery_soc_percent=status.battery_soc_percent,
                    source="battery",
                )

        return None

    @staticmethod
    def detect_deficit(status, mg_info) -> Optional[EnergyRequest]:
        """
        Detect if a microgrid needs imported energy.

        Deficit = load exceeds generation AND battery is depleting.

        Args:
            status: MicrogridStatus from city_ems
            mg_info: MicrogridInfo from src.ems.city_ems registry

        Returns:
            EnergyRequest if deficit exists, else None.
        """
        if not mg_info.can_share_power:
            return None

        net_deficit = (
            status.total_load_kw
            - status.pv_generation_kw
            - status.generator_power_kw
            - max(0, status.battery_power_kw)  # Positive = discharge
        )

        # Request if: shedding active OR (deficit > 0 AND SOC low)
        needs_help = (
            status.load_shed_kw > 5.0
            or (net_deficit > 5.0 and status.battery_soc_percent < 40.0)
        )

        if needs_help:
            deficit_amount = max(status.load_shed_kw, net_deficit, 5.0)
            return EnergyRequest(
                microgrid_id=status.microgrid_id,
                timestamp=status.timestamp,
                deficit_kw=deficit_amount,
                priority=mg_info.priority,
                critical_load_at_risk=status.critical_load_shed,
                current_soc=status.battery_soc_percent,
            )

        return None

    # ─────────────────────────────────────────────────────────────
    # INTERNAL
    # ─────────────────────────────────────────────────────────────

    def _update_metrics(self) -> None:
        """Update accumulated metrics from this timestep's transfers."""
        if self._active_transfers:
            self.metrics.active_transfer_steps += 1

        for t in self._active_transfers:
            energy_kwh = t.delivered_kw * t.duration_hours
            loss_kwh = (t.power_kw - t.delivered_kw) * t.duration_hours

            self.metrics.total_energy_exchanged_kwh += energy_kwh
            self.metrics.total_energy_lost_kwh += loss_kwh
            self.metrics.total_transfers += 1

            self.metrics.transfers_by_recipient[t.to_mg] = (
                self.metrics.transfers_by_recipient.get(t.to_mg, 0.0) + energy_kwh
            )
            self.metrics.transfers_by_donor[t.from_mg] = (
                self.metrics.transfers_by_donor.get(t.from_mg, 0.0) + energy_kwh
            )
