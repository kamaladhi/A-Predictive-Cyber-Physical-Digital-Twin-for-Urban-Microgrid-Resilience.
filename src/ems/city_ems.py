from __future__ import annotations
"""
City-Level Centralized Energy Management System (City-EMS)

This module implements a SUPERVISORY EMS that coordinates MULTIPLE heterogeneous microgrids
to enforce priority-aware resilience policies and improve city-level survivability.

Key Responsibilities:
- Coordinate 4 heterogeneous microgrids (Hospital, University, Industrial, Residential)
- Enforce priority-aware load shedding across city
- Manage inter-microgrid power sharing (if connected)
- Optimize city-wide resource allocation during outages
- Monitor and maintain critical infrastructure resilience
- Provide supervisory control signals to local EMSs

Architecture:
- City-EMS receives state from all local EMSs
- City-EMS sends supervisory commands to local EMSs
- City-EMS does NOT bypass local EMS safety logic
- City-EMS optimizes city-level objectives while respecting local constraints

Digital Twin Framework:
- Real-time coordination of heterogeneous microgrids
- Priority-based resource allocation
- Demonstrable improvement in city-level survivability
- Policy-driven resilience enforcement
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Set
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Import DR coordinator (if available)
try:
    from src.ems.demand_response import (
        DemandResponseCoordinator, DREvent, DRCommand,
        DREventType, DREventPriority
    )
    DR_AVAILABLE = True
except ImportError:
    try:
        from src.ems.demand_response import (
            DemandResponseCoordinator, DREvent, DRCommand,
            DREventType, DREventPriority
        )
        DR_AVAILABLE = True
    except ImportError:
        DR_AVAILABLE = False
        logger.warning("Demand Response module not available")

# Import Resource Sharing module
try:
    from src.ems.resource_sharing import EnergyExchangeBus
    SHARING_AVAILABLE = True
except ImportError:
    try:
        from src.ems.resource_sharing import EnergyExchangeBus
        SHARING_AVAILABLE = True
    except ImportError:
        SHARING_AVAILABLE = False
        logger.warning("Resource Sharing module not available")

# Import Coordinated Outage module
try:
    from src.ems.coordinated_outage import (
        SharedGenScheduler, DemandReductionPropagator,
        RecoveryController, RecoveryPhase, StabilityMonitor
    )
    OUTAGE_COORD_AVAILABLE = True
except ImportError:
    try:
        from src.ems.coordinated_outage import (
            SharedGenScheduler, DemandReductionPropagator,
            RecoveryController, RecoveryPhase, StabilityMonitor
        )
        OUTAGE_COORD_AVAILABLE = True
    except ImportError:
        OUTAGE_COORD_AVAILABLE = False
        logger.warning("Coordinated Outage module not available")

# Import Optimization EMS module (legacy single-step LP)
try:
    from src.ems.optimization_ems import OptimizationDispatcher, OptimizationCostConfig
    OPTIMIZATION_AVAILABLE = True
except ImportError:
    OPTIMIZATION_AVAILABLE = False
    logger.info("Legacy optimization EMS module not available")

# Import Predictive Optimization EMS (rolling-horizon MPC — preferred)
try:
    from src.ems.predictive_optimizer import PredictiveDispatcher, PredictiveCostConfig
    PREDICTIVE_AVAILABLE = True
    logger.info("Predictive MPC dispatcher available")
except ImportError:
    PREDICTIVE_AVAILABLE = False
    logger.info("Predictive optimizer not available — will use legacy or rule-based")


# =============================================================================
# CITY-LEVEL PRIORITY SYSTEM
# =============================================================================

from src.ems.common import (
    MicrogridPriority, CityOperationMode, ResiliencePolicy,
    MicrogridInfo, MicrogridStatus, CityWideMeasurements,
    SupervisoryCommand, CityControlOutputs
)


# =============================================================================
# CITY-LEVEL EMS STATE
# =============================================================================

@dataclass
class CityEMSState:
    """Persistent state for city-level EMS"""
    city_mode: CityOperationMode = CityOperationMode.NORMAL
    mode_entry_time: Optional[datetime] = None
    active_policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST
    
    # Outage tracking
    outage_detected: bool = False
    outage_start_time: Optional[datetime] = None
    
    # Resource tracking
    total_city_battery_kwh: float = 0.0
    total_city_fuel_liters: float = 0.0
    
    # Priority-based allocation history
    last_allocation_time: Optional[datetime] = None
    allocation_history: List[Dict[str, float]] = field(default_factory=list)
    
    def reset_mode(self, new_mode: CityOperationMode, timestamp: datetime):
        """Reset city mode and entry time"""
        self.city_mode = new_mode
        self.mode_entry_time = timestamp


# =============================================================================
# CITY-LEVEL CENTRALIZED EMS
# =============================================================================

class CityEMS:
    """
    City-Level Centralized Energy Management System
    
    Coordinates multiple heterogeneous microgrids to enforce priority-aware
    resilience policies and improve city-level survivability during grid outages.
    
    Key Features:
    - Priority-aware load shedding allocation
    - Resource optimization across microgrids
    - Critical infrastructure protection
    - City-wide survivability maximization
    - Policy-driven coordination
    """
    
    def __init__(self, resilience_policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST,
                 use_optimizer: bool = False, dispatch_mode: str = "balanced"):
        """
        Initialize City-Level EMS
        
        Args:
            resilience_policy: Default resilience policy to enforce
        """
        self.state = CityEMSState(active_policy=resilience_policy)
        self.microgrids: Dict[str, MicrogridInfo] = {}
        
        # Initialize Demand Response Coordinator
        if DR_AVAILABLE:
            self.dr_coordinator = DemandResponseCoordinator()
            logger.info("Demand Response Coordinator enabled")
        else:
            self.dr_coordinator = None
            logger.info("Demand Response Coordinator not available")
        
        # Initialize Resource Sharing Bus
        if SHARING_AVAILABLE:
            # We initialize with empty IDs; they will be updated during registration
            self.exchange_bus = EnergyExchangeBus(
                mg_ids=[], 
                bus_capacity_kw=200.0,
                transfer_efficiency=0.95,
                min_transfer_kw=5.0,
                max_simultaneous=3,
                min_donor_soc=30.0,
            )
            logger.info("Energy Exchange Bus initialized with Markov failure model")
        else:
            self.exchange_bus = None
            logger.info("Energy Exchange Bus not available")
        
        # Initialize Coordinated Outage components
        if OUTAGE_COORD_AVAILABLE:
            self.gen_scheduler = SharedGenScheduler()
            self.demand_propagator = DemandReductionPropagator()
            self.recovery_controller = RecoveryController()
            self.stability_monitor = StabilityMonitor()
            logger.info("Coordinated Outage Handling enabled")
        else:
            self.gen_scheduler = None
            self.demand_propagator = None
            self.recovery_controller = None
            self.stability_monitor = None
        
        # Priority mapping
        self.priority_map = {
            "hospital": MicrogridPriority.CRITICAL,
            "university": MicrogridPriority.HIGH,
            "industrial": MicrogridPriority.MEDIUM,
            "residential": MicrogridPriority.LOW
        }
        
        # Policy-specific parameters
        self.policy_parameters = {
            ResiliencePolicy.CRITICAL_FIRST: {
                "priority_weights": {
                    MicrogridPriority.CRITICAL: 1.0,
                    MicrogridPriority.HIGH: 0.7,
                    MicrogridPriority.MEDIUM: 0.4,
                    MicrogridPriority.LOW: 0.2
                },
                "shed_order": [
                    MicrogridPriority.LOW,
                    MicrogridPriority.MEDIUM,
                    MicrogridPriority.HIGH,
                    MicrogridPriority.CRITICAL
                ]
            },
            ResiliencePolicy.BALANCED: {
                "priority_weights": {
                    MicrogridPriority.CRITICAL: 1.0,
                    MicrogridPriority.HIGH: 0.85,
                    MicrogridPriority.MEDIUM: 0.70,
                    MicrogridPriority.LOW: 0.55
                },
                "shed_order": [
                    MicrogridPriority.LOW,
                    MicrogridPriority.MEDIUM,
                    MicrogridPriority.HIGH,
                    MicrogridPriority.CRITICAL
                ]
            },
            ResiliencePolicy.EQUITABLE: {
                "priority_weights": {
                    MicrogridPriority.CRITICAL: 1.0,
                    MicrogridPriority.HIGH: 1.0,
                    MicrogridPriority.MEDIUM: 1.0,
                    MicrogridPriority.LOW: 1.0
                },
                "shed_order": [
                    MicrogridPriority.LOW,
                    MicrogridPriority.MEDIUM,
                    MicrogridPriority.HIGH,
                    MicrogridPriority.CRITICAL
                ]
            },
            ResiliencePolicy.ECONOMIC: {
                "priority_weights": {
                    MicrogridPriority.CRITICAL: 1.0,
                    MicrogridPriority.MEDIUM: 0.9,  # Industrial prioritized
                    MicrogridPriority.HIGH: 0.7,
                    MicrogridPriority.LOW: 0.3
                },
                "shed_order": [
                    MicrogridPriority.LOW,
                    MicrogridPriority.HIGH,
                    MicrogridPriority.MEDIUM,
                    MicrogridPriority.CRITICAL
                ]
            }
        }
        
        # Initialize Optimization Dispatcher
        # Priority: PredictiveDispatcher (MPC) > OptimizationDispatcher (single-LP) > rule-based
        self.use_optimizer = use_optimizer
        self.optimizer = None
        self.predictive_dispatcher = None
        self._optimizer_type = "none"

        if use_optimizer:
            if PREDICTIVE_AVAILABLE:
                try:
                    self.predictive_dispatcher = PredictiveDispatcher(
                        mg_registry=self.microgrids,
                        policy=resilience_policy,
                        dispatch_mode=dispatch_mode,
                    )
                    self.use_optimizer = True
                    self._optimizer_type = "predictive_mpc"
                    logger.info(
                        "PREDICTIVE MPC dispatch ENABLED — "
                        f"horizon={self.predictive_dispatcher.horizon} steps"
                    )
                except RuntimeError as e:
                    logger.error(f"PredictiveDispatcher init failed: {e}")
                    raise RuntimeError(
                        f"Optimizer requested but PredictiveDispatcher failed: {e}. "
                        "Install scipy (pip install scipy) or fix the error. "
                        "Silent fallback to rule-based is DISABLED."
                    )
            elif OPTIMIZATION_AVAILABLE:
                self.optimizer = OptimizationDispatcher(
                    mg_registry=self.microgrids,
                    policy=resilience_policy,
                    dispatch_mode=dispatch_mode,
                )
                self.use_optimizer = True
                self._optimizer_type = "legacy_lp"
                logger.info("Legacy LP optimization dispatch ENABLED")
            else:
                raise RuntimeError(
                    "Optimizer requested but NEITHER predictive_optimizer nor "
                    "optimization_ems modules are available. Install scipy "
                    "(pip install scipy>=1.9) and ensure EMS modules are on PYTHONPATH. "
                    "Silent fallback to rule-based is DISABLED."
                )
        
        logger.info(f"City-Level EMS initialized with policy: {resilience_policy.value}")
    
    # =========================================================================
    # MICROGRID REGISTRATION
    # =========================================================================
    
    def register_microgrid(self, microgrid_info: MicrogridInfo):
        """
        Register a microgrid with the city-level EMS
        
        Args:
            microgrid_info: Information about the microgrid to register
        """
        self.microgrids[microgrid_info.microgrid_id] = microgrid_info
        
        # Update sharing bus with new MG ID
        if self.exchange_bus:
            if microgrid_info.microgrid_id not in self.exchange_bus.mg_ids:
                self.exchange_bus.mg_ids.append(microgrid_info.microgrid_id)
                from src.ems.resource_sharing import CyberLinkManager
                # Re-initialize manager with updated ID list
                self.exchange_bus.link_manager = CyberLinkManager(self.exchange_bus.mg_ids)

        logger.info(f"Registered microgrid: {microgrid_info.microgrid_id} "
                   f"(Type: {microgrid_info.microgrid_type}, "
                   f"Priority: {microgrid_info.priority.name})")
    
    # =========================================================================
    # MAIN UPDATE LOOP
    # =========================================================================
    
    def update(self, measurements: CityWideMeasurements, 
               failed_links: Optional[Set[str]] = None) -> CityControlOutputs:
        """
        Main city-level EMS update - called every timestep
        
        Args:
            measurements: Aggregated measurements from all microgrids
            
        Returns:
            CityControlOutputs with supervisory commands for all microgrids
        """
        # Update internal state
        self._update_city_state(measurements)
        
        # Initialize outputs
        outputs = CityControlOutputs(
            timestamp=measurements.timestamp,
            city_mode=self.state.city_mode,
            active_policy=self.state.active_policy,
            supervisory_commands={},
            load_shedding_allocation={},
            resource_prioritization=[]
        )
        
        # Update DR events (if DR coordinator available)
        if self.dr_coordinator:
            dr_commands = self.dr_coordinator.update_dr_events(
                measurements.timestamp,
                measurements.microgrid_statuses
            )
            outputs.dr_commands = dr_commands
            if dr_commands:
                outputs.info.append(f"Active DR events: {len(dr_commands)} commands issued")
        
        # Update sharing bus link status (Cyber-Physical Coordination)
        effective_failed_links = failed_links
        if self.exchange_bus:
            if effective_failed_links is None:
                # Use internal Markov model to generate link status
                effective_failed_links = self.exchange_bus.link_manager.update_states()
            
            self.exchange_bus.set_failed_links(effective_failed_links)
            
            # Record telemetry
            self.exchange_bus.metrics.total_link_samples += len(self.microgrids)
            self.exchange_bus.metrics.failed_link_samples += len(effective_failed_links)
        
        # Run city-level state machine
        outputs = self._run_city_state_machine(measurements, outputs)
        
        # Execute mode-specific coordination
        if self.use_optimizer and self.predictive_dispatcher is not None:
            # ── Predictive MPC dispatch (preferred) ───────────────────
            outputs = self._coordinate_predictive(measurements, outputs, failed_links=failed_links)
        elif self.use_optimizer and self.optimizer is not None:
            # ── Legacy single-step LP dispatch ────────────────────────
            outputs = self._coordinate_optimized(measurements, outputs)
        else:
            # ── Rule-based dispatch (original logic) ─────────────────
            if self.state.city_mode == CityOperationMode.NORMAL:
                outputs = self._coordinate_normal(measurements, outputs)
                
            elif self.state.city_mode == CityOperationMode.PARTIAL_OUTAGE:
                outputs = self._coordinate_partial_outage(measurements, outputs)
                
            elif self.state.city_mode == CityOperationMode.WIDESPREAD_OUTAGE:
                outputs = self._coordinate_widespread_outage(measurements, outputs)
                
            elif self.state.city_mode == CityOperationMode.EMERGENCY:
                outputs = self._coordinate_emergency(measurements, outputs)
                
            elif self.state.city_mode == CityOperationMode.RECOVERY:
                outputs = self._coordinate_recovery(measurements, outputs)
        
        # Calculate city-level metrics
        outputs.metrics = self._calculate_city_metrics(measurements, outputs)
        
        return outputs
    
    # =========================================================================
    # COORDINATION: PREDICTIVE MPC DISPATCH
    # =========================================================================

    def _coordinate_predictive(self, meas: CityWideMeasurements,
                               outputs: CityControlOutputs,
                               failed_links: Optional[Set[str]] = None) -> CityControlOutputs:
        """
        Predictive MPC-based coordination for ALL operation modes.

        Uses rolling-horizon LP with multi-step lookahead, forecast integration,
        and uncertainty-aware SOC margins. Produces SupervisoryCommands from
        the t=0 solution of the receding-horizon optimisation.

        DOES NOT silently fall back to rule-based. If the MPC solver fails,
        the infeasibility is logged and reported in outputs.warnings, but
        the EMS still applies the best available (zero-shed) commands.
        """
        outage_prep = self.state.city_mode in (
            CityOperationMode.PARTIAL_OUTAGE,
            CityOperationMode.WIDESPREAD_OUTAGE,
            CityOperationMode.EMERGENCY,
        )

        # Extract active DR targets
        dr_targets = {}
        if hasattr(outputs, 'dr_commands') and outputs.dr_commands:
            for dr_cmd in outputs.dr_commands:
                dr_targets[dr_cmd.microgrid_id] = dr_cmd.requested_reduction_kw

        commands, solution = self.predictive_dispatcher.solve(
            measurements=meas,
            city_mode=self.state.city_mode,
            outage_preparation=outage_prep,
            dr_targets=dr_targets,
            failed_links=failed_links,
        )

        if "infeasible" in solution.solver_status:
            outputs.warnings.append(
                f"MPC infeasible ({solution.solver_status}) — "
                f"applying zero-shed commands (NO rule-based fallback)"
            )
            logger.warning(
                f"MPC infeasible at {meas.timestamp}: {solution.solver_status}"
            )
        else:
            outputs.info.append(
                f"MPC dispatch: obj={solution.objective_value:.4f}, "
                f"shed={solution.total_shed_kw:.1f}kW, "
                f"gen={solution.total_gen_kw:.1f}kW, "
                f"xfer={solution.total_export_kw:.1f}kW, "
                f"H={solution.horizon_steps}, "
                f"vars={solution.n_variables}, "
                f"cons={solution.n_constraints}, "
                f"{solution.solve_time_ms:.1f}ms"
            )

        # Apply commands unconditionally (optimal or zero-shed)
        outputs.supervisory_commands = commands

        for mg_id, dispatch in solution.dispatches.items():
            status = meas.microgrid_statuses.get(mg_id)
            if status and status.total_load_kw > 0:
                outputs.load_shedding_allocation[mg_id] = (
                    dispatch.shed_kw / status.total_load_kw * 100.0
                )

        outputs.metrics['optimizer_type'] = 'predictive_mpc'
        outputs.metrics['optimizer_objective'] = float(solution.objective_value)
        outputs.metrics['optimizer_solve_ms'] = float(solution.solve_time_ms)
        
        # Flatten cost breakdown to avoid dict-in-metrics issues
        for cost_name, cost_val in solution.cost_breakdown.items():
            outputs.metrics[f'optimizer_cost_{cost_name}'] = float(cost_val)

        outputs.metrics['optimizer_total_shed_kw'] = float(solution.total_shed_kw)
        outputs.metrics['optimizer_total_gen_kw'] = float(solution.total_gen_kw)
        outputs.metrics['optimizer_horizon'] = int(solution.horizon_steps)
        outputs.metrics['optimizer_n_variables'] = int(solution.n_variables)
        outputs.metrics['optimizer_n_constraints'] = int(solution.n_constraints)
        outputs.metrics['optimizer_iterations'] = int(solution.solver_iterations)

        return outputs

    # =========================================================================
    # COORDINATION: LEGACY OPTIMIZATION-DRIVEN
    # =========================================================================
    
    def _coordinate_optimized(self, meas: CityWideMeasurements,
                              outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Optimization-driven coordination for ALL operation modes.
        
        Replaces mode-specific rule-based methods with a single LP solve
        that produces optimal SupervisoryCommands.  The optimizer handles
        priority-aware shedding, generator dispatch, battery scheduling,
        and exchange bus allocation in one unified formulation.
        
        Falls back to rule-based coordination if the optimizer returns
        an infeasible solution.
        """
        # Detect if outage preparation is needed (cascade probability)
        outage_prep = (
            self.state.city_mode in (
                CityOperationMode.PARTIAL_OUTAGE,
                CityOperationMode.WIDESPREAD_OUTAGE,
                CityOperationMode.EMERGENCY,
            )
        )
        
        commands, solution = self.optimizer.solve(
            measurements=meas,
            city_mode=self.state.city_mode,
            outage_preparation=outage_prep,
        )
        
        if solution.solver_status != "optimal":
            # Fallback to rule-based for this timestep
            outputs.warnings.append(
                f"Optimizer infeasible ({solution.solver_status}) — "
                f"falling back to rule-based coordination"
            )
            if self.state.city_mode == CityOperationMode.NORMAL:
                return self._coordinate_normal(meas, outputs)
            elif self.state.city_mode == CityOperationMode.PARTIAL_OUTAGE:
                return self._coordinate_partial_outage(meas, outputs)
            elif self.state.city_mode == CityOperationMode.WIDESPREAD_OUTAGE:
                return self._coordinate_widespread_outage(meas, outputs)
            elif self.state.city_mode == CityOperationMode.EMERGENCY:
                return self._coordinate_emergency(meas, outputs)
            else:
                return self._coordinate_recovery(meas, outputs)
        
        # Apply optimized commands
        outputs.supervisory_commands = commands
        
        # Build shedding allocation from optimizer solution
        for mg_id, dispatch in solution.dispatches.items():
            status = meas.microgrid_statuses.get(mg_id)
            if status and status.total_load_kw > 0:
                outputs.load_shedding_allocation[mg_id] = (
                    dispatch.shed_kw / status.total_load_kw * 100.0
                )
        
        # Add optimizer metrics
        outputs.metrics['optimizer_objective'] = solution.objective_value
        outputs.metrics['optimizer_solve_ms'] = solution.solve_time_ms
        outputs.metrics['optimizer_cost_breakdown'] = solution.cost_breakdown
        outputs.metrics['optimizer_total_shed_kw'] = solution.total_shed_kw
        outputs.metrics['optimizer_total_gen_kw'] = solution.total_gen_kw
        
        outputs.info.append(
            f"Optimized dispatch: obj={solution.objective_value:.4f}, "
            f"shed={solution.total_shed_kw:.1f}kW, "
            f"gen={solution.total_gen_kw:.1f}kW, "
            f"xfer={solution.total_export_kw:.1f}kW "
            f"({solution.solve_time_ms:.1f}ms)"
        )
        
        return outputs
    
    # =========================================================================
    # CITY-LEVEL STATE MACHINE
    # =========================================================================
    
    def _run_city_state_machine(self, meas: CityWideMeasurements,
                               outputs: CityControlOutputs) -> CityControlOutputs:
        """
        City-level operation mode state machine
        
        Transitions:
        NORMAL -> PARTIAL_OUTAGE (some microgrids islanded)
        PARTIAL_OUTAGE -> WIDESPREAD_OUTAGE (all microgrids islanded)
        WIDESPREAD_OUTAGE -> EMERGENCY (critical resource shortage)
        Any -> RECOVERY (grid restoration begins)
        RECOVERY -> NORMAL (all back to normal)
        """
        current_mode = self.state.city_mode
        
        # Check for outage conditions
        if current_mode == CityOperationMode.NORMAL:
            if meas.microgrids_islanded > 0:
                if meas.microgrids_islanded == len(meas.microgrid_statuses):
                    # All microgrids islanded
                    outputs.info.append("Widespread outage detected - all microgrids islanded")
                    self.state.reset_mode(CityOperationMode.WIDESPREAD_OUTAGE, meas.timestamp)
                    self.state.outage_start_time = meas.timestamp
                    outputs.city_mode = CityOperationMode.WIDESPREAD_OUTAGE
                else:
                    # Partial outage
                    outputs.info.append(f"Partial outage - {meas.microgrids_islanded} microgrids islanded")
                    self.state.reset_mode(CityOperationMode.PARTIAL_OUTAGE, meas.timestamp)
                    outputs.city_mode = CityOperationMode.PARTIAL_OUTAGE
        
        # Check for emergency conditions
        elif current_mode in [CityOperationMode.PARTIAL_OUTAGE, CityOperationMode.WIDESPREAD_OUTAGE]:
            if meas.microgrids_in_emergency > 0:
                outputs.warnings.append(f"EMERGENCY: {meas.microgrids_in_emergency} microgrids critical")
                self.state.reset_mode(CityOperationMode.EMERGENCY, meas.timestamp)
                outputs.city_mode = CityOperationMode.EMERGENCY
            
            # Check for recovery
            elif meas.microgrids_islanded < len(meas.microgrid_statuses):
                if current_mode == CityOperationMode.WIDESPREAD_OUTAGE:
                    outputs.info.append("Recovery initiated - some microgrids reconnecting")
                    self.state.reset_mode(CityOperationMode.RECOVERY, meas.timestamp)
                    outputs.city_mode = CityOperationMode.RECOVERY
        
        # Emergency mode
        elif current_mode == CityOperationMode.EMERGENCY:
            if meas.microgrids_in_emergency == 0:
                # Return to outage coordination
                if meas.microgrids_islanded == len(meas.microgrid_statuses):
                    self.state.reset_mode(CityOperationMode.WIDESPREAD_OUTAGE, meas.timestamp)
                    outputs.city_mode = CityOperationMode.WIDESPREAD_OUTAGE
                else:
                    self.state.reset_mode(CityOperationMode.PARTIAL_OUTAGE, meas.timestamp)
                    outputs.city_mode = CityOperationMode.PARTIAL_OUTAGE
        
        # Recovery mode
        elif current_mode == CityOperationMode.RECOVERY:
            if meas.microgrids_islanded == 0:
                outputs.info.append("Recovery complete - all microgrids grid-connected")
                self.state.reset_mode(CityOperationMode.NORMAL, meas.timestamp)
                outputs.city_mode = CityOperationMode.NORMAL
                self.state.outage_start_time = None
        
        return outputs
    
    # =========================================================================
    # COORDINATION: NORMAL MODE
    # =========================================================================
    
    def _coordinate_normal(self, meas: CityWideMeasurements,
                          outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordination for normal grid-connected operation
        
        Objectives:
        - Optimize city-wide energy costs
        - Prepare for potential outages
        - Balance battery SoC across microgrids
        - Monitor resource availability
        """
        for mg_id, status in meas.microgrid_statuses.items():
            cmd = SupervisoryCommand(
                microgrid_id=mg_id,
                timestamp=meas.timestamp,
                reason="Normal operation - maintain readiness"
            )
            
            # Ensure batteries charged for backup readiness
            mg_info = self.microgrids.get(mg_id)
            if mg_info:
                if mg_info.priority in [MicrogridPriority.CRITICAL, MicrogridPriority.HIGH]:
                    # Critical microgrids: maintain high SoC
                    cmd.battery_soc_target_percent = 85.0
                else:
                    # Lower priority: can use battery for cost savings
                    cmd.battery_soc_target_percent = 70.0
            
            outputs.supervisory_commands[mg_id] = cmd
        
        outputs.info.append("Normal operation - all microgrids grid-connected")
        return outputs
    
    # =========================================================================
    # COORDINATION: PARTIAL OUTAGE
    # =========================================================================
    
    def _coordinate_partial_outage(self, meas: CityWideMeasurements,
                                   outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordination for partial outage (some microgrids islanded)
        
        Objectives:
        - Support islanded microgrids
        - Prepare grid-connected microgrids for potential islanding
        - Allocate resources based on priority
        """
        # Identify islanded vs connected microgrids
        islanded_mgs = [mg_id for mg_id, status in meas.microgrid_statuses.items() 
                       if status.is_islanded]
        connected_mgs = [mg_id for mg_id, status in meas.microgrid_statuses.items() 
                        if not status.is_islanded]
        
        outputs.info.append(f"Partial outage: {len(islanded_mgs)} islanded, "
                          f"{len(connected_mgs)} connected")
        
        # For islanded microgrids: apply priority-based support
        for mg_id in islanded_mgs:
            outputs = self._apply_priority_coordination(mg_id, meas, outputs)
        
        # For connected microgrids: prepare for potential islanding
        for mg_id in connected_mgs:
            cmd = SupervisoryCommand(
                microgrid_id=mg_id,
                timestamp=meas.timestamp,
                battery_soc_target_percent=90.0,  # Charge up for potential outage
                reason="Prepare for potential islanding"
            )
            outputs.supervisory_commands[mg_id] = cmd
        
        # Coordinated outage response: shared gen + demand reduction
        outputs = self._coordinate_outage_response(meas, outputs)
        
        # Resource sharing: connected MGs can share with islanded MGs
        outputs = self._coordinate_resource_sharing(meas, outputs)
        
        return outputs
    
    # =========================================================================
    # COORDINATION: WIDESPREAD OUTAGE
    # =========================================================================
    
    def _coordinate_widespread_outage(self, meas: CityWideMeasurements,
                                     outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordination for widespread outage (all microgrids islanded)
        
        This is the PRIMARY RESILIENCE MODE where priority-aware policies
        are most critical for city-level survivability.
        
        Objectives:
        - Maximize city-wide survivability duration
        - Enforce priority-aware load shedding
        - Optimize resource allocation across microgrids
        - Protect critical infrastructure
        """
        outputs.info.append("Widespread outage - coordinating all microgrids")
        
        # Calculate city-wide resource situation
        city_resources = self._assess_city_resources(meas)
        
        # Determine priority-based load shedding allocation
        shedding_allocation = self._calculate_priority_shedding(meas, city_resources)
        outputs.load_shedding_allocation = shedding_allocation
        
        # Generate supervisory commands for each microgrid
        for mg_id, status in meas.microgrid_statuses.items():
            outputs = self._apply_priority_coordination(mg_id, meas, outputs, 
                                                       shedding_allocation.get(mg_id, 0))
        
        # Coordinated outage response: shared gen + demand reduction
        outputs = self._coordinate_outage_response(meas, outputs)
        
        # Resource sharing: surplus MGs donate to deficit MGs
        outputs = self._coordinate_resource_sharing(meas, outputs)
        
        # Calculate and report city survivability
        survivability_hours = self._calculate_city_survivability(meas, shedding_allocation)
        outputs.metrics['city_survivability_hours'] = survivability_hours
        outputs.info.append(f"Estimated city survivability: {survivability_hours:.1f} hours")
        
        return outputs
    
    # =========================================================================
    # COORDINATION: EMERGENCY MODE
    # =========================================================================
    
    def _coordinate_emergency(self, meas: CityWideMeasurements,
                             outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordination for emergency conditions (critical resource shortage)
        
        Objectives:
        - Protect critical infrastructure at all costs
        - Aggressive load shedding on lower-priority microgrids
        - Emergency resource reallocation
        """
        outputs.warnings.append("EMERGENCY MODE - Critical resource shortage")
        
        # Identify microgrids in emergency
        emergency_mgs = [mg_id for mg_id, status in meas.microgrid_statuses.items()
                        if status.resource_criticality == "emergency"]
        
        # Apply emergency policies
        for mg_id, status in meas.microgrid_statuses.items():
            mg_info = self.microgrids.get(mg_id)
            if not mg_info:
                continue
            
            cmd = SupervisoryCommand(
                microgrid_id=mg_id,
                timestamp=meas.timestamp,
                emergency_mode=True
            )
            
            if mg_info.priority == MicrogridPriority.CRITICAL:
                # Critical microgrids: protect at all costs
                cmd.critical_only_mode = True
                cmd.reason = "EMERGENCY - Critical infrastructure protection"
            elif mg_info.priority == MicrogridPriority.HIGH:
                # High priority: shed 50% non-critical
                cmd.target_shed_percent = 50.0
                cmd.reason = "EMERGENCY - Aggressive load shedding"
            else:
                # Lower priority: shed 80% or more
                cmd.target_shed_percent = 80.0
                cmd.reason = "EMERGENCY - Maximum load shedding"
            
            outputs.supervisory_commands[mg_id] = cmd
        
        # Coordinated outage response: shared gen + demand reduction
        outputs = self._coordinate_outage_response(meas, outputs)
        
        # Resource sharing even in emergency — surplus MGs can help critical MGs
        outputs = self._coordinate_resource_sharing(meas, outputs)
        
        return outputs
    
    # =========================================================================
    # COORDINATION: RECOVERY MODE
    # =========================================================================
    
    def _coordinate_recovery(self, meas: CityWideMeasurements,
                        outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordination for recovery phase (grid restoration in progress)
        
        Enhanced with RecoveryController staged reconnection and
        StabilityMonitor secondary instability prevention.
        """
        outputs.info.append("Recovery mode - coordinating grid reconnection")
        
        # Prioritize reconnection order
        reconnection_order = self._calculate_reconnection_priority(meas)
        outputs.resource_prioritization = reconnection_order
        
        # --- Use RecoveryController if available ---
        if self.recovery_controller:
            # Start recovery if not already active
            if not self.recovery_controller.is_active:
                self.recovery_controller.start_recovery(
                    current_step=0  # Will be overridden by DT manager
                )
            
            # Check stability before advancing
            if self.stability_monitor:
                is_stable, reason = self.stability_monitor.check_stability(
                    meas.microgrid_statuses, self.microgrids
                )
                if not is_stable:
                    outputs.warnings.append(
                        f"Recovery paused: {reason}"
                    )
                    logger.warning(f"Recovery stability check failed: {reason}")
            
            # Determine grid availability per MG
            grid_available = {}
            for mg_id, status in meas.microgrid_statuses.items():
                grid_available[mg_id] = not status.is_islanded
            
            # Get recovery commands
            recovery_cmds = self.recovery_controller.update(
                meas.microgrid_statuses, self.microgrids,
                current_step=0, grid_available=grid_available,
            )
            
            # Merge into outputs
            for mg_id, cmd in recovery_cmds.items():
                outputs.supervisory_commands[mg_id] = cmd
            
            outputs.info.append(
                f"Recovery phase: {self.recovery_controller.phase.name}, "
                f"reconnected: {self.recovery_controller.reconnected_mgs}"
            )
            
            return outputs
        
        # --- Fallback: basic recovery ---
        for mg_id, status in meas.microgrid_statuses.items():
            mg_info = self.microgrids.get(mg_id)
            if not mg_info:
                continue
            
            cmd = SupervisoryCommand(
                microgrid_id=mg_id,
                timestamp=meas.timestamp
            )
            
            if status.is_islanded:
                cmd.battery_soc_target_percent = 80.0
                cmd.reason = "Prepare for grid reconnection"
            else:
                cmd.target_shed_percent = 0.0
                cmd.reason = "Grid restored - resume normal operation"
            
            outputs.supervisory_commands[mg_id] = cmd
        
        return outputs
    
    # =========================================================================
    # PRIORITY-BASED COORDINATION
    # =========================================================================
    
    def _apply_priority_coordination(self, mg_id: str, meas: CityWideMeasurements,
                                    outputs: CityControlOutputs,
                                    target_shed_percent: float = None) -> CityControlOutputs:
        """
        Apply priority-aware coordination to a specific microgrid
        """
        mg_info = self.microgrids.get(mg_id)
        status = meas.microgrid_statuses.get(mg_id)
        
        if not mg_info or not status:
            return outputs
        
        cmd = SupervisoryCommand(
            microgrid_id=mg_id,
            timestamp=meas.timestamp,
            city_priority_level=mg_info.priority.value
        )
        
        # Priority-based guidance
        if mg_info.priority == MicrogridPriority.CRITICAL:
            # Hospital: Protect at all costs
            cmd.battery_reserve_percent = 15.0  # Minimum SoC
            cmd.generator_enable = True
            cmd.max_shed_limit_percent = 0.0  # No shedding of critical loads
            cmd.reason = "Critical priority - maximum protection"
            
        elif mg_info.priority == MicrogridPriority.HIGH:
            # University: Protect with moderate margins
            cmd.battery_reserve_percent = 20.0
            cmd.generator_enable = True
            cmd.max_shed_limit_percent = 30.0
            cmd.reason = "High priority - moderate protection"
            
        elif mg_info.priority == MicrogridPriority.MEDIUM:
            # Industrial: Balance production and resource conservation
            cmd.battery_reserve_percent = 25.0
            cmd.generator_enable = status.battery_soc_percent < 35.0
            cmd.max_shed_limit_percent = 70.0
            cmd.reason = "Medium priority - balanced approach"
            
        else:  # LOW priority
            # Residential: Comfort loads sheddable
            cmd.battery_reserve_percent = 30.0
            cmd.generator_enable = status.battery_soc_percent < 25.0
            cmd.max_shed_limit_percent = 90.0
            cmd.reason = "Low priority - aggressive conservation"
        
        # Apply target shedding if specified
        if target_shed_percent is not None:
            cmd.target_shed_percent = min(target_shed_percent, cmd.max_shed_limit_percent)
        
        outputs.supervisory_commands[mg_id] = cmd
        return outputs
    
    # =========================================================================
    # COORDINATED OUTAGE RESPONSE
    # =========================================================================
    
    def _coordinate_outage_response(
        self, meas: CityWideMeasurements, outputs: CityControlOutputs
    ) -> CityControlOutputs:
        """
        Run coordinated outage response: shared generation + demand reduction.
        Called from outage coordination modes (partial, widespread, emergency).
        """
        if not self.gen_scheduler or not self.demand_propagator:
            return outputs
        
        # --- Shared Generation Scheduling ---
        gen_commands = self.gen_scheduler.schedule(
            meas.microgrid_statuses, self.microgrids
        )
        
        if gen_commands:
            total_shared = sum(
                c.power_setpoint_kw for c in gen_commands.values()
            )
            outputs.info.append(
                f"Shared gen: {len(gen_commands)} dispatch(es), "
                f"{total_shared:.1f} kW shared"
            )
        
        # --- Demand Reduction Propagation ---
        city_deficit = meas.total_load_kw - meas.total_generation_kw
        shed_allocation = self.demand_propagator.propagate(
            meas.microgrid_statuses, self.microgrids, city_deficit
        )
        
        # Apply shed to supervisory commands
        for mg_id, shed_pct in shed_allocation.items():
            if mg_id in outputs.supervisory_commands:
                outputs.supervisory_commands[mg_id].target_shed_percent = shed_pct
            else:
                cmd = SupervisoryCommand(
                    microgrid_id=mg_id,
                    timestamp=meas.timestamp,
                    target_shed_percent=shed_pct,
                    reason=f"Coordinated demand reduction: {shed_pct:.0f}%"
                )
                outputs.supervisory_commands[mg_id] = cmd
        
        return outputs
    
    # =========================================================================
    # INTER-MICROGRID RESOURCE SHARING
    # =========================================================================
    
    def _coordinate_resource_sharing(self, meas: CityWideMeasurements,
                                     outputs: CityControlOutputs) -> CityControlOutputs:
        """
        Coordinate inter-microgrid resource sharing via the energy exchange bus.
        
        Workflow:
        1. Clear previous step's buffers.
        2. For each registered microgrid, detect surplus or deficit.
        3. Feed reports/requests to the exchange bus.
        4. Run priority-weighted allocation algorithm.
        5. Apply transfer allocations to supervisory commands.
        
        Only runs when exchange_bus is available and SHARING_AVAILABLE.
        """
        if not self.exchange_bus:
            return outputs
        
        # Clear previous step
        self.exchange_bus.clear_step()
        
        # Collect surplus/deficit reports from all microgrids
        for mg_id, status in meas.microgrid_statuses.items():
            mg_info = self.microgrids.get(mg_id)
            if not mg_info or not mg_info.can_share_power:
                continue
            
            # Detect surplus
            surplus = EnergyExchangeBus.detect_surplus(status, mg_info)
            if surplus:
                self.exchange_bus.report_surplus(surplus)
            
            # Detect deficit
            deficit = EnergyExchangeBus.detect_deficit(status, mg_info)
            if deficit:
                self.exchange_bus.request_energy(deficit)
        
        # Run allocation algorithm
        transfers = self.exchange_bus.allocate_transfers()
        
        if transfers:
            # Apply to supervisory commands
            self.exchange_bus.apply_to_commands(outputs.supervisory_commands)
            outputs.resource_transfers = transfers
            
            total_kw = sum(t.delivered_kw for t in transfers)
            outputs.info.append(
                f"Resource sharing: {len(transfers)} transfer(s), "
                f"{total_kw:.1f} kW delivered"
            )
        
        return outputs
    
    # =========================================================================
    # RESOURCE ASSESSMENT AND ALLOCATION
    # =========================================================================
    
    def _assess_city_resources(self, meas: CityWideMeasurements) -> Dict[str, float]:
        """
        Assess total city-wide resource availability
        """
        total_battery_kwh = sum(
            status.battery_capacity_kwh * (status.battery_soc_percent / 100.0)
            for status in meas.microgrid_statuses.values()
        )
        
        total_fuel_liters = sum(
            status.fuel_remaining_liters
            for status in meas.microgrid_statuses.values()
        )
        
        total_generation_kw = sum(
            status.pv_generation_kw + status.generator_power_kw
            for status in meas.microgrid_statuses.values()
        )
        
        return {
            'total_battery_kwh': total_battery_kwh,
            'total_fuel_liters': total_fuel_liters,
            'total_generation_kw': total_generation_kw,
            'average_soc_percent': (total_battery_kwh / meas.total_battery_energy_kwh * 100.0) 
                                  if meas.total_battery_energy_kwh > 0 else 0
        }
    
    def _calculate_priority_shedding(self, meas: CityWideMeasurements,
                                    city_resources: Dict[str, float]) -> Dict[str, float]:
        """
        Calculate priority-aware load shedding allocation across microgrids
        
        This is the CORE RESILIENCE ALGORITHM that enforces priority policies.
        Strictly enforces: NEVER shed from higher priority if lower priority has headroom.
        
        KEY PRINCIPLE: Trigger shedding based on CRITICAL/HIGH priority MG health,
        then allocate shedding to LOWER priorities first. This prevents violations.
        """
        policy_params = self.policy_parameters[self.state.active_policy]
        shed_order = policy_params['shed_order']
        
        # PRIORITY-AWARE TRIGGER: Check health of CRITICAL and HIGH priority MGs
        # If they're struggling, shed from lower priorities to protect them.
        critical_high_min_soc = 100.0
        for mg_id, mg_info in self.microgrids.items():
            if mg_info.priority in [MicrogridPriority.CRITICAL, MicrogridPriority.HIGH]:
                status = meas.microgrid_statuses.get(mg_id)
                if status:
                    critical_high_min_soc = min(critical_high_min_soc, status.battery_soc_percent)
        
        # Conservative trigger: Only shed from lower priorities when higher priorities
        # are moderately stressed
        required_city_shed_percent = 0.0
        if critical_high_min_soc < 35:
            required_city_shed_percent = 60.0
        elif critical_high_min_soc < 45:
            required_city_shed_percent = 40.0
        elif critical_high_min_soc < 55:
            required_city_shed_percent = 25.0

        # Convert to kW target using current total load
        total_city_load_kw = sum(status.total_load_kw for status in meas.microgrid_statuses.values())
        required_city_shed_kw = (required_city_shed_percent / 100.0) * total_city_load_kw

        allocation: Dict[str, float] = {mg_id: 0.0 for mg_id in self.microgrids.keys()}
        remaining_kw = required_city_shed_kw

        # STRICT PRIORITY ENFORCEMENT: Allocate from LOW→HIGH, never skip tiers
        for priority in shed_order:
            if remaining_kw <= 1e-6:
                break
            
            # Get all MGs at this priority level
            priority_mgs = [(mg_id, mg_info) for mg_id, mg_info in self.microgrids.items() 
                           if mg_info.priority == priority]
            
            # Calculate total available headroom at this priority tier
            tier_available_kw = 0.0
            for mg_id, mg_info in priority_mgs:
                status = meas.microgrid_statuses.get(mg_id)
                if status and status.total_load_kw > 0:
                    max_shed_kw = (mg_info.max_shed_percent / 100.0) * status.total_load_kw
                    tier_available_kw += max_shed_kw
            
            # If this tier can handle all remaining, distribute proportionally within tier
            if tier_available_kw >= remaining_kw:
                for mg_id, mg_info in priority_mgs:
                    status = meas.microgrid_statuses.get(mg_id)
                    if not status or status.total_load_kw <= 0:
                        continue
                    max_shed_kw = (mg_info.max_shed_percent / 100.0) * status.total_load_kw
                    # Proportional share of remaining
                    share = (max_shed_kw / tier_available_kw) if tier_available_kw > 0 else 0
                    shed_kw = share * remaining_kw
                    allocation[mg_id] = (shed_kw / status.total_load_kw) * 100.0
                remaining_kw = 0.0
                break
            else:
                # Max out this entire tier and continue to next
                for mg_id, mg_info in priority_mgs:
                    status = meas.microgrid_statuses.get(mg_id)
                    if not status or status.total_load_kw <= 0:
                        continue
                    max_shed_kw = (mg_info.max_shed_percent / 100.0) * status.total_load_kw
                    allocation[mg_id] = mg_info.max_shed_percent
                    remaining_kw -= max_shed_kw

        return allocation

    def _calculate_city_survivability(self, meas: CityWideMeasurements,
                                     shedding_allocation: Dict[str, float]) -> float:
        """
        Calculate estimated city-wide survivability duration in hours
        """
        min_survivability = float('inf')
        
        for mg_id, status in meas.microgrid_statuses.items():
            mg_info = self.microgrids.get(mg_id)
            if not mg_info:
                continue
            
            # Only consider critical and high priority microgrids
            if mg_info.priority not in [MicrogridPriority.CRITICAL, MicrogridPriority.HIGH]:
                continue
            
            # Calculate effective load after shedding
            shed_percent = shedding_allocation.get(mg_id, 0)
            effective_load = status.total_load_kw * (1.0 - shed_percent / 100.0)
            
            # Calculate runtime with current resources
            if effective_load > 0:
                battery_runtime = (status.battery_capacity_kwh * status.battery_soc_percent / 100.0) / effective_load
                
                # Add generator contribution
                if status.fuel_remaining_liters > 0:
                    # Rough estimate: 0.25 L/kWh fuel consumption
                    gen_energy_kwh = status.fuel_remaining_liters / 0.25
                    gen_runtime = gen_energy_kwh / effective_load
                    total_runtime = battery_runtime + gen_runtime
                else:
                    total_runtime = battery_runtime
                
                min_survivability = min(min_survivability, total_runtime)
        
        return min_survivability if min_survivability != float('inf') else 0.0
    
    def _calculate_reconnection_priority(self, meas: CityWideMeasurements) -> List[str]:
        """
        Calculate priority order for grid reconnection during recovery
        """
        # Prioritize by microgrid priority, then by resource criticality
        priority_list = []
        
        for priority_level in [MicrogridPriority.CRITICAL, MicrogridPriority.HIGH,
                              MicrogridPriority.MEDIUM, MicrogridPriority.LOW]:
            for mg_id, mg_info in self.microgrids.items():
                if mg_info.priority == priority_level:
                    status = meas.microgrid_statuses.get(mg_id)
                    if status and status.is_islanded:
                        priority_list.append(mg_id)
        
        return priority_list
    
    # =========================================================================
    # STATE UPDATE AND METRICS
    # =========================================================================
    
    def _update_city_state(self, meas: CityWideMeasurements):
        """Update internal city-level state from measurements"""
        self.state.total_city_battery_kwh = meas.total_battery_energy_kwh
        self.state.total_city_fuel_liters = meas.total_fuel_liters
        
        if meas.grid_outage_active and not self.state.outage_detected:
            self.state.outage_detected = True
            self.state.outage_start_time = meas.timestamp
        elif not meas.grid_outage_active:
            self.state.outage_detected = False
    
    def _calculate_city_metrics(self, meas: CityWideMeasurements,
                               outputs: CityControlOutputs) -> Dict[str, Any]:
        """Calculate comprehensive city-level metrics"""
        metrics = {
            'timestamp': meas.timestamp.isoformat(),
            'city_mode': self.state.city_mode.value,
            'active_policy': self.state.active_policy.value,
            'total_microgrids': len(meas.microgrid_statuses),
            'microgrids_islanded': meas.microgrids_islanded,
            'microgrids_in_emergency': meas.microgrids_in_emergency,
            'total_load_kw': meas.total_load_kw,
            'total_critical_load_kw': meas.total_critical_load_kw,
            'total_generation_kw': meas.total_generation_kw,
            'average_battery_soc': (meas.total_battery_energy_kwh / 
                                   sum(mg.battery_capacity_kwh for mg in self.microgrids.values())
                                   * 100.0) if self.microgrids else 0,
            'city_survivability_hours': meas.city_survivability_hours,
            'outage_duration_hours': meas.outage_duration_hours,
        }
        
        # Per-priority metrics
        for priority in MicrogridPriority:
            priority_mgs = [mg for mg in self.microgrids.values() if mg.priority == priority]
            if priority_mgs:
                metrics[f'{priority.name.lower()}_count'] = len(priority_mgs)
                metrics[f'{priority.name.lower()}_islanded'] = sum(
                    1 for mg in priority_mgs 
                    if meas.microgrid_statuses.get(mg.microgrid_id, None) and 
                    meas.microgrid_statuses[mg.microgrid_id].is_islanded
                )
        
        # Resource sharing metrics
        if self.exchange_bus:
            sharing = self.exchange_bus.get_metrics()
            metrics['resource_sharing'] = sharing
            metrics['energy_exchanged_kwh'] = sharing['total_energy_exchanged_kwh']
            metrics['total_transfers'] = sharing['total_transfers']
        
        return metrics
    
    # =========================================================================
    # POLICY MANAGEMENT
    # =========================================================================
    
    def set_resilience_policy(self, policy: ResiliencePolicy):
        """
        Change the active resilience policy
        
        Args:
            policy: New resilience policy to enforce
        """
        logger.info(f"Changing resilience policy from {self.state.active_policy.value} "
                   f"to {policy.value}")
        self.state.active_policy = policy
    
    def get_policy_description(self, policy: ResiliencePolicy = None) -> str:
        """Get description of a resilience policy"""
        if policy is None:
            policy = self.state.active_policy
        
        descriptions = {
            ResiliencePolicy.CRITICAL_FIRST: 
                "Prioritizes critical infrastructure (hospitals) above all else. "
                "Residential and industrial loads shed first.",
            ResiliencePolicy.BALANCED: 
                "Balanced approach with moderate prioritization. "
                "All microgrids receive fair treatment with priority weighting.",
            ResiliencePolicy.EQUITABLE: 
                "Equal treatment for all microgrids regardless of priority. "
                "Load shedding distributed proportionally.",
            ResiliencePolicy.ECONOMIC: 
                "Prioritizes economic activity (industrial) alongside critical services. "
                "Minimizes economic impact of outages."
        }
        
        return descriptions.get(policy, "Unknown policy")
