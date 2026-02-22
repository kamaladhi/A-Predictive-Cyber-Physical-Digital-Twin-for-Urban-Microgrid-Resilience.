"""
Demand Response (DR) System for City-Level Microgrid Coordination

This module implements a comprehensive DR framework that coordinates
voluntary load reduction across heterogeneous microgrids.

Key Features:
- DR event modeling (start, duration, target reduction, incentives)
- City-level DR event detection and command generation
- Local EMS participation tracking
- Performance metrics and incentive calculation
- Integration with priority-aware resilience policies
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# DR EVENT TYPES AND PRIORITIES
# =============================================================================

class DREventType(Enum):
    """Types of demand response events"""
    ECONOMIC = "economic"           # Cost-based DR (reduce during high prices)
    EMERGENCY = "emergency"         # Grid emergency (prevent blackout)
    ANCILLARY_SERVICE = "ancillary" # Frequency regulation, voltage support
    PEAK_SHAVING = "peak_shaving"   # Reduce peak demand charges
    RENEWABLE_CURTAILMENT = "renewable_curtailment"  # Too much renewable generation


class DREventPriority(Enum):
    """Priority levels for DR events"""
    VOLUNTARY = 1      # Optional participation, good incentives
    RECOMMENDED = 2    # Strongly recommended, better incentives
    MANDATORY = 3      # Required participation, penalties for non-compliance
    EMERGENCY = 4      # Critical event, mandatory for all capable microgrids


class DRParticipationStatus(Enum):
    """Microgrid participation status in DR event"""
    NOT_ELIGIBLE = "not_eligible"       # Cannot participate (critical loads only)
    OPTED_OUT = "opted_out"             # Chose not to participate
    PARTICIPATING = "participating"     # Actively participating
    COMPLETED = "completed"             # Successfully completed DR event
    FAILED = "failed"                   # Failed to meet commitment


# =============================================================================
# DR EVENT MODEL
# =============================================================================

@dataclass
class DREvent:
    """Demand Response Event Model"""
    event_id: str
    event_type: DREventType
    priority: DREventPriority
    
    # Timing
    start_time: datetime
    duration_minutes: int
    notification_time: datetime  # When participants were notified
    
    # Target reduction
    target_mw_reduction: float  # City-wide target
    allocated_reductions: Dict[str, float] = field(default_factory=dict)  # Per microgrid targets (kW)
    
    # Incentives ($/kWh reduced)
    base_incentive_rate: float = 0.50  # Base payment
    performance_bonus_rate: float = 0.20  # Bonus for exceeding target
    penalty_rate: float = 0.30  # Penalty for mandatory events if fail
    
    # Participation
    eligible_microgrids: List[str] = field(default_factory=list)
    participating_microgrids: List[str] = field(default_factory=list)
    
    # Status
    is_active: bool = False
    is_completed: bool = False
    
    # Results (populated after event)
    actual_reduction_kw: float = 0.0
    achievement_percent: float = 0.0
    total_incentives_paid: float = 0.0
    
    def end_time(self) -> datetime:
        """Calculate event end time"""
        return self.start_time + timedelta(minutes=self.duration_minutes)
    
    def is_in_progress(self, current_time: datetime) -> bool:
        """Check if event is currently active"""
        return self.start_time <= current_time < self.end_time()
    
    def notification_lead_time_minutes(self) -> int:
        """Calculate how much advance notice was given"""
        return int((self.start_time - self.notification_time).total_seconds() / 60)


# =============================================================================
# DR PARTICIPATION TRACKING
# =============================================================================

@dataclass
class DRParticipationRecord:
    """Track a single microgrid's participation in a DR event"""
    microgrid_id: str
    event_id: str
    
    # Commitment
    committed_reduction_kw: float
    status: DRParticipationStatus
    
    # Performance
    actual_reduction_kw: float = 0.0
    achievement_percent: float = 0.0
    baseline_load_kw: float = 0.0  # Load without DR
    actual_load_kw: float = 0.0     # Load during DR
    
    # Incentives
    incentive_earned: float = 0.0
    penalty_assessed: float = 0.0
    
    # Tracking
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    samples_collected: int = 0
    
    def calculate_performance(self):
        """Calculate achievement percentage"""
        if self.committed_reduction_kw > 0:
            self.achievement_percent = (self.actual_reduction_kw / self.committed_reduction_kw) * 100.0
        else:
            self.achievement_percent = 0.0


# =============================================================================
# DR PERFORMANCE METRICS
# =============================================================================

@dataclass
class DREventMetrics:
    """Comprehensive metrics for a DR event"""
    event_id: str
    event_type: str
    priority: str
    
    # Participation
    eligible_count: int
    participating_count: int
    participation_rate_percent: float
    
    # Performance
    target_reduction_kw: float
    actual_reduction_kw: float
    achievement_percent: float
    
    # By microgrid type
    performance_by_type: Dict[str, Dict] = field(default_factory=dict)
    
    # Financial
    total_incentives_paid: float = 0.0
    total_penalties_assessed: float = 0.0
    net_financial_impact: float = 0.0
    
    # Time
    duration_minutes: int = 0
    notification_lead_time_minutes: int = 0
    
    # Quality
    avg_achievement_percent: float = 0.0
    max_achievement_percent: float = 0.0
    min_achievement_percent: float = 0.0
    participants_above_target: int = 0


# =============================================================================
# DR COMMAND INTERFACE
# =============================================================================

@dataclass
class DRCommand:
    """DR-specific command to local EMS"""
    microgrid_id: str
    event_id: str
    timestamp: datetime
    
    # DR request
    requested_reduction_kw: float
    duration_minutes: int
    is_mandatory: bool
    
    # Incentive information
    incentive_rate: float
    performance_bonus_rate: float
    penalty_rate: float
    
    # Guidance
    suggested_actions: List[str] = field(default_factory=list)
    priority_loads_to_shed: List[str] = field(default_factory=list)
    
    def __str__(self):
        mandatory_str = "MANDATORY" if self.is_mandatory else "VOLUNTARY"
        return (f"DR Command [{mandatory_str}]: {self.microgrid_id} - "
                f"Reduce {self.requested_reduction_kw:.0f} kW for {self.duration_minutes} min "
                f"@ ${self.incentive_rate:.2f}/kWh")


# =============================================================================
# DEMAND RESPONSE COORDINATOR
# =============================================================================

class DemandResponseCoordinator:
    """
    Coordinates DR events across city-level microgrids
    
    Responsibilities:
    - Create and schedule DR events
    - Allocate reduction targets to microgrids
    - Track participation and performance
    - Calculate incentives and penalties
    - Generate DR metrics and reports
    """
    
    def __init__(self):
        """Initialize DR Coordinator"""
        self.active_events: Dict[str, DREvent] = {}
        self.completed_events: Dict[str, DREvent] = {}
        self.participation_records: Dict[str, List[DRParticipationRecord]] = {}
        
        # DR capability by microgrid type (typical percentages of non-critical load)
        self.dr_capability = {
            "hospital": 0.10,      # 10% - Very limited (comfort systems only)
            "university": 0.40,    # 40% - Moderate (HVAC, non-critical buildings)
            "industrial": 0.50,    # 50% - High (production can be curtailed)
            "residential": 0.60    # 60% - Highest (AC, EV charging, appliances)
        }
        
        logger.info("Demand Response Coordinator initialized")
    
    # =========================================================================
    # DR EVENT CREATION AND SCHEDULING
    # =========================================================================
    
    def create_dr_event(self, event_type: DREventType, priority: DREventPriority,
                       start_time: datetime, duration_minutes: int,
                       target_mw_reduction: float, notification_time: datetime = None,
                       base_incentive_rate: float = 0.50) -> DREvent:
        """
        Create a new DR event
        
        Args:
            event_type: Type of DR event
            priority: Priority level
            start_time: When event starts
            duration_minutes: How long event lasts
            target_mw_reduction: City-wide MW reduction target
            notification_time: When participants notified (defaults to now)
            base_incentive_rate: $/kWh payment rate
            
        Returns:
            DREvent object
        """
        event_id = f"DR_{event_type.value}_{start_time.strftime('%Y%m%d_%H%M')}"
        
        if notification_time is None:
            notification_time = datetime.now()
        
        event = DREvent(
            event_id=event_id,
            event_type=event_type,
            priority=priority,
            start_time=start_time,
            duration_minutes=duration_minutes,
            notification_time=notification_time,
            target_mw_reduction=target_mw_reduction,
            base_incentive_rate=base_incentive_rate,
            performance_bonus_rate=base_incentive_rate * 0.4,  # 40% bonus for over-performance
            penalty_rate=base_incentive_rate * 0.6 if priority in [DREventPriority.MANDATORY, DREventPriority.EMERGENCY] else 0.0
        )
        
        self.active_events[event_id] = event
        
        logger.info(f"Created DR Event: {event_id}")
        logger.info(f"  Type: {event_type.value}, Priority: {priority.value}")
        logger.info(f"  Target: {target_mw_reduction:.2f} MW")
        logger.info(f"  Duration: {duration_minutes} min")
        logger.info(f"  Incentive: ${base_incentive_rate:.2f}/kWh")
        
        return event
    
    def allocate_dr_targets(self, event: DREvent, microgrids: Dict) -> Dict[str, float]:
        """
        Allocate DR reduction targets to individual microgrids
        
        Allocation strategy:
        - Consider microgrid priority (lower priority gets more allocation)
        - Consider DR capability (residential > industrial > university > hospital)
        - Consider current load (bigger loads can reduce more)
        - Respect mandatory vs voluntary participation
        
        Args:
            event: DR event to allocate
            microgrids: Dictionary of microgrid info
            
        Returns:
            Dictionary mapping microgrid_id to allocated reduction (kW)
        """
        target_kw = event.target_mw_reduction * 1000.0  # Convert MW to kW
        allocations = {}
        
        # Calculate allocation weights based on priority and capability
        weights = {}
        eligible_microgrids = []
        
        for mg_id, mg_info in microgrids.items():
            # Check eligibility
            if event.priority == DREventPriority.EMERGENCY:
                # Emergency: all non-critical microgrids participate
                if mg_info.priority.value > 1:  # Not CRITICAL priority
                    eligible = True
                else:
                    eligible = False
            elif event.priority == DREventPriority.MANDATORY:
                # Mandatory: all except critical facilities
                eligible = mg_info.priority.value > 1
            else:
                # Voluntary: all can participate
                eligible = True
            
            if not eligible:
                continue
            
            eligible_microgrids.append(mg_id)
            
            # Calculate weight based on:
            # 1. Priority (lower priority = higher allocation)
            # 2. DR capability
            # 3. Current non-critical load
            
            priority_weight = 1.0 / mg_info.priority.value  # Inverse priority
            capability_weight = self.dr_capability.get(mg_info.microgrid_type, 0.3)
            load_weight = mg_info.total_capacity_kw - mg_info.critical_load_kw
            
            combined_weight = priority_weight * capability_weight * load_weight
            weights[mg_id] = combined_weight
        
        # Normalize and allocate
        total_weight = sum(weights.values())
        if total_weight > 0:
            for mg_id, weight in weights.items():
                allocation = (weight / total_weight) * target_kw
                allocations[mg_id] = allocation
        
        event.eligible_microgrids = eligible_microgrids
        event.allocated_reductions = allocations
        
        logger.info(f"DR allocation for {event.event_id}:")
        for mg_id, reduction in allocations.items():
            mg_info = microgrids[mg_id]
            logger.info(f"  {mg_info.microgrid_type}: {reduction:.1f} kW "
                       f"({reduction/target_kw*100:.1f}% of total)")
        
        return allocations
    
    # =========================================================================
    # DR EVENT MANAGEMENT
    # =========================================================================
    
    def update_dr_events(self, current_time: datetime, microgrid_statuses: Dict) -> List[DRCommand]:
        """
        Update all active DR events and generate commands
        
        Args:
            current_time: Current simulation time
            microgrid_statuses: Current status of all microgrids
            
        Returns:
            List of DR commands to send to local EMSs
        """
        dr_commands = []
        
        # Check for events starting
        for event_id, event in list(self.active_events.items()):
            if event.start_time <= current_time < event.end_time():
                if not event.is_active:
                    # Event just started
                    event.is_active = True
                    logger.info(f"DR Event STARTED: {event_id}")
                    
                    # Generate DR commands for participants
                    dr_commands.extend(self._generate_dr_commands(event, microgrid_statuses))
                
                # Update participation tracking
                self._track_dr_performance(event, current_time, microgrid_statuses)
                
            elif current_time >= event.end_time():
                if event.is_active and not event.is_completed:
                    # Event just ended
                    event.is_active = False
                    event.is_completed = True
                    logger.info(f"DR Event COMPLETED: {event_id}")
                    
                    # Finalize event
                    self._finalize_dr_event(event, microgrid_statuses)
                    
                    # Move to completed events
                    self.completed_events[event_id] = event
                    del self.active_events[event_id]
        
        return dr_commands
    
    def _generate_dr_commands(self, event: DREvent, microgrid_statuses: Dict) -> List[DRCommand]:
        """Generate DR commands for event start"""
        commands = []
        
        for mg_id in event.eligible_microgrids:
            if mg_id not in event.allocated_reductions:
                continue
            
            # Create participation record
            record = DRParticipationRecord(
                microgrid_id=mg_id,
                event_id=event.event_id,
                committed_reduction_kw=event.allocated_reductions[mg_id],
                status=DRParticipationStatus.PARTICIPATING,
                start_time=event.start_time,
                baseline_load_kw=microgrid_statuses[mg_id].total_load_kw if mg_id in microgrid_statuses else 0
            )
            
            if event.event_id not in self.participation_records:
                self.participation_records[event.event_id] = []
            self.participation_records[event.event_id].append(record)
            
            # Create DR command
            cmd = DRCommand(
                microgrid_id=mg_id,
                event_id=event.event_id,
                timestamp=event.start_time,
                requested_reduction_kw=event.allocated_reductions[mg_id],
                duration_minutes=event.duration_minutes,
                is_mandatory=event.priority in [DREventPriority.MANDATORY, DREventPriority.EMERGENCY],
                incentive_rate=event.base_incentive_rate,
                performance_bonus_rate=event.performance_bonus_rate,
                penalty_rate=event.penalty_rate,
                suggested_actions=self._get_dr_suggestions(mg_id, microgrid_statuses),
                priority_loads_to_shed=self._get_priority_shed_loads(mg_id)
            )
            
            commands.append(cmd)
            event.participating_microgrids.append(mg_id)
            
            logger.info(f"  DR Command issued to {mg_id}: {cmd}")
        
        return commands
    
    def _get_dr_suggestions(self, mg_id: str, microgrid_statuses: Dict) -> List[str]:
        """Get suggested DR actions based on microgrid type"""
        if mg_id not in microgrid_statuses:
            return []
        
        status = microgrid_statuses[mg_id]
        suggestions = []
        
        # Type-specific suggestions
        if "hospital" in mg_id:
            suggestions = [
                "Reduce HVAC in administrative areas",
                "Defer non-urgent equipment usage",
                "Dim non-critical lighting"
            ]
        elif "university" in mg_id:
            suggestions = [
                "Reduce HVAC in non-occupied buildings",
                "Defer lab equipment startup",
                "Reduce common area lighting"
            ]
        elif "industrial" in mg_id:
            suggestions = [
                "Defer non-critical production lines",
                "Reduce HVAC in non-production areas",
                "Optimize compressor schedules"
            ]
        elif "residential" in mg_id:
            suggestions = [
                "Defer EV charging",
                "Reduce air conditioning setpoint",
                "Delay washing machine/dryer usage"
            ]
        
        return suggestions
    
    def _get_priority_shed_loads(self, mg_id: str) -> List[str]:
        """Get priority load categories to shed for DR"""
        if "hospital" in mg_id:
            return ["admin_misc"]
        elif "university" in mg_id:
            return ["athletic_facilities", "dormitory_common"]
        elif "industrial" in mg_id:
            return ["canteen_welfare", "office_hvac", "assembly_lines"]
        elif "residential" in mg_id:
            return ["ev_charging", "air_conditioning", "washing_machines"]
        else:
            return []
    
    # =========================================================================
    # PERFORMANCE TRACKING
    # =========================================================================
    
    def _track_dr_performance(self, event: DREvent, current_time: datetime,
                              microgrid_statuses: Dict):
        """Track DR performance during event"""
        if event.event_id not in self.participation_records:
            return
        
        for record in self.participation_records[event.event_id]:
            if record.microgrid_id not in microgrid_statuses:
                continue
            
            status = microgrid_statuses[record.microgrid_id]
            
            # Calculate actual reduction
            # Reduction = Baseline - Actual Load
            actual_reduction = record.baseline_load_kw - status.total_load_kw
            
            # Update running average
            record.samples_collected += 1
            record.actual_reduction_kw = (
                (record.actual_reduction_kw * (record.samples_collected - 1) + actual_reduction) /
                record.samples_collected
            )
            record.actual_load_kw = status.total_load_kw
    
    def _finalize_dr_event(self, event: DREvent, microgrid_statuses: Dict):
        """Finalize DR event and calculate incentives"""
        if event.event_id not in self.participation_records:
            return
        
        total_actual_reduction = 0.0
        total_incentives = 0.0
        total_penalties = 0.0
        
        logger.info(f"\nFinalizing DR Event: {event.event_id}")
        logger.info("="*80)
        
        for record in self.participation_records[event.event_id]:
            record.end_time = event.end_time()
            record.calculate_performance()
            
            # Calculate incentives
            duration_hours = event.duration_minutes / 60.0
            energy_reduced_kwh = record.actual_reduction_kw * duration_hours
            
            if record.achievement_percent >= 100:
                # Met or exceeded target
                base_payment = record.committed_reduction_kw * duration_hours * event.base_incentive_rate
                
                # Performance bonus for exceeding
                if record.achievement_percent > 100:
                    excess_reduction = record.actual_reduction_kw - record.committed_reduction_kw
                    bonus = excess_reduction * duration_hours * event.performance_bonus_rate
                else:
                    bonus = 0.0
                
                record.incentive_earned = base_payment + bonus
                record.status = DRParticipationStatus.COMPLETED
                
            elif record.achievement_percent >= 80:
                # Partial credit (80-100%)
                record.incentive_earned = energy_reduced_kwh * event.base_incentive_rate
                record.status = DRParticipationStatus.COMPLETED
                
            elif event.priority in [DREventPriority.MANDATORY, DREventPriority.EMERGENCY]:
                # Failed mandatory event - assess penalty
                shortfall_kwh = (record.committed_reduction_kw - record.actual_reduction_kw) * duration_hours
                record.penalty_assessed = shortfall_kwh * event.penalty_rate
                record.status = DRParticipationStatus.FAILED
                
            else:
                # Voluntary event, poor performance, no penalty but no incentive
                record.incentive_earned = 0.0
                record.status = DRParticipationStatus.FAILED
            
            total_actual_reduction += record.actual_reduction_kw
            total_incentives += record.incentive_earned
            total_penalties += record.penalty_assessed
            
            logger.info(f"\n{record.microgrid_id}:")
            logger.info(f"  Target: {record.committed_reduction_kw:.1f} kW")
            logger.info(f"  Actual: {record.actual_reduction_kw:.1f} kW")
            logger.info(f"  Achievement: {record.achievement_percent:.1f}%")
            logger.info(f"  Incentive: ${record.incentive_earned:.2f}")
            if record.penalty_assessed > 0:
                logger.info(f"  Penalty: ${record.penalty_assessed:.2f}")
        
        # Update event results
        target_kw = event.target_mw_reduction * 1000.0
        event.actual_reduction_kw = total_actual_reduction
        event.achievement_percent = (total_actual_reduction / target_kw * 100.0) if target_kw > 0 else 0
        event.total_incentives_paid = total_incentives - total_penalties
        
        logger.info(f"\n{'='*80}")
        logger.info(f"Event Summary:")
        logger.info(f"  Target: {target_kw:.1f} kW")
        logger.info(f"  Actual: {total_actual_reduction:.1f} kW")
        logger.info(f"  Achievement: {event.achievement_percent:.1f}%")
        logger.info(f"  Participants: {len(event.participating_microgrids)}/{len(event.eligible_microgrids)}")
        logger.info(f"  Total Incentives: ${total_incentives:.2f}")
        logger.info(f"  Total Penalties: ${total_penalties:.2f}")
        logger.info(f"  Net Payment: ${event.total_incentives_paid:.2f}")
        logger.info("="*80)
    
    # =========================================================================
    # METRICS AND REPORTING
    # =========================================================================
    
    def calculate_event_metrics(self, event_id: str) -> Optional[DREventMetrics]:
        """
        Calculate comprehensive metrics for a completed DR event
        
        Args:
            event_id: Event to analyze
            
        Returns:
            DREventMetrics or None if event not found
        """
        event = self.completed_events.get(event_id)
        if not event:
            return None
        
        records = self.participation_records.get(event_id, [])
        if not records:
            return None
        
        # Calculate participation
        participation_rate = (len(event.participating_microgrids) / 
                            len(event.eligible_microgrids) * 100.0) if event.eligible_microgrids else 0
        
        # Calculate performance by type
        performance_by_type = {}
        for record in records:
            mg_type = record.microgrid_id.split('_')[0]
            if mg_type not in performance_by_type:
                performance_by_type[mg_type] = {
                    'count': 0,
                    'total_target': 0,
                    'total_actual': 0,
                    'total_incentives': 0
                }
            
            performance_by_type[mg_type]['count'] += 1
            performance_by_type[mg_type]['total_target'] += record.committed_reduction_kw
            performance_by_type[mg_type]['total_actual'] += record.actual_reduction_kw
            performance_by_type[mg_type]['total_incentives'] += record.incentive_earned
        
        # Calculate aggregate statistics
        achievements = [r.achievement_percent for r in records]
        avg_achievement = sum(achievements) / len(achievements) if achievements else 0
        participants_above_target = sum(1 for a in achievements if a >= 100)
        
        metrics = DREventMetrics(
            event_id=event_id,
            event_type=event.event_type.value,
            priority=event.priority.value,
            eligible_count=len(event.eligible_microgrids),
            participating_count=len(event.participating_microgrids),
            participation_rate_percent=participation_rate,
            target_reduction_kw=event.target_mw_reduction * 1000.0,
            actual_reduction_kw=event.actual_reduction_kw,
            achievement_percent=event.achievement_percent,
            performance_by_type=performance_by_type,
            total_incentives_paid=sum(r.incentive_earned for r in records),
            total_penalties_assessed=sum(r.penalty_assessed for r in records),
            net_financial_impact=event.total_incentives_paid,
            duration_minutes=event.duration_minutes,
            notification_lead_time_minutes=event.notification_lead_time_minutes(),
            avg_achievement_percent=avg_achievement,
            max_achievement_percent=max(achievements) if achievements else 0,
            min_achievement_percent=min(achievements) if achievements else 0,
            participants_above_target=participants_above_target
        )
        
        return metrics
    
    def get_summary_report(self) -> Dict:
        """Get summary report of all DR events"""
        total_events = len(self.completed_events)
        if total_events == 0:
            return {
                'total_events': 0,
                'message': 'No completed DR events'
            }
        
        total_target = sum(e.target_mw_reduction for e in self.completed_events.values())
        total_actual = sum(e.actual_reduction_kw / 1000.0 for e in self.completed_events.values())
        total_incentives = sum(e.total_incentives_paid for e in self.completed_events.values())
        
        avg_achievement = sum(e.achievement_percent for e in self.completed_events.values()) / total_events
        
        return {
            'total_events': total_events,
            'total_target_mw': total_target,
            'total_actual_mw': total_actual,
            'overall_achievement_percent': (total_actual / total_target * 100.0) if total_target > 0 else 0,
            'avg_event_achievement_percent': avg_achievement,
            'total_incentives_paid': total_incentives,
            'events_successful': sum(1 for e in self.completed_events.values() if e.achievement_percent >= 80),
            'events_failed': sum(1 for e in self.completed_events.values() if e.achievement_percent < 80)
        }
