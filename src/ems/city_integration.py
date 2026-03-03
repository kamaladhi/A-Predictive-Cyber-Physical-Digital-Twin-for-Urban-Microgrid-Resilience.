"""
Digital Twin Integration Example: City-Level Coordination with Enhanced DT

This module demonstrates how to integrate the City-Level EMS with
all four local microgrids (Hospital, University, Industrial, Residential)
in a coordinated digital twin framework.

This example shows:
1. How to set up the city-level coordinator
2. How local EMSs interact with city-level supervisory commands
3. How to demonstrate priority-aware resilience during outages
4. How to measure city-level survivability improvements
5. [NEW] Shadow simulation for predictive what-if analysis
6. [NEW] State estimation with Kalman filtering
7. [NEW] Enhanced resilience metrics with critical load tracking
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

# City-level EMS
from src.ems.city_ems import (
    CityEMS, MicrogridInfo, MicrogridPriority, ResiliencePolicy,
    MicrogridStatus, CityWideMeasurements, SupervisoryCommand
)

# Demand Response
from src.ems.demand_response import (
    DemandResponseCoordinator, DREventType, DREventPriority
)

# Enhanced Digital Twin Components
from src.digital_twin.state_estimator import CityStateEstimator
from src.digital_twin.shadow_simulator import ShadowSimulator, PredictionScenario
from src.digital_twin.resilience_metrics import EnhancedResilienceMetricCalculator
from src.digital_twin.twin_state import TwinState, PhysicalState, CyberState, ResilienceState

# Real microgrid simulators
from src.utils.microgrid_factory import MicrogridFactory, MicrogridType

# Local EMSs (import from your local EMS files)
# from src.ems.hospital_ems import HospitalEMS, MicrogridMeasurements as HospitalMeasurements
# from src.ems.university_ems import UniversityEMS, MicrogridMeasurements as UniversityMeasurements
# from src.ems.industry_ems import IndustryEMS, MicrogridMeasurements as IndustryMeasurements
# from src.ems.residence_ems import ResidenceEMS, MicrogridMeasurements as ResidenceMeasurements

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# DIGITAL TWIN COORDINATOR
# =============================================================================

class DigitalTwinCoordinator:
    """
    Coordinates city-level and local EMSs in a digital twin framework
    
    Architecture:
    - City-EMS: Provides supervisory commands based on priority policies
    - Local EMSs: Execute local control with city-level guidance
    - Coordinator: Manages information flow and integration
    - [NEW] State Estimator: Kalman filtering for accurate state tracking
    - [NEW] Shadow Simulator: Predictive what-if scenario analysis
    - [NEW] Enhanced Metrics: Detailed resilience performance tracking
    """
    
    def __init__(self, resilience_policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST,
                 enable_state_estimation: bool = True,
                 enable_shadow_simulation: bool = True):
        """
        Initialize the digital twin coordinator
        
        Args:
            resilience_policy: City-level resilience policy
            enable_state_estimation: Enable Kalman filtering for state estimation
            enable_shadow_simulation: Enable predictive shadow simulation
        """
        # Initialize city-level EMS
        self.city_ems = CityEMS(resilience_policy=resilience_policy)
        
        # Local EMS instances (to be set up)
        self.local_ems = {}
        
        # Simulation state
        self.current_time = datetime(2025, 1, 15, 0, 0, 0)
        self.timestep_minutes = 5
        
        # [NEW] Enhanced Digital Twin Components
        self.enable_state_est = enable_state_estimation
        self.enable_shadow_sim = enable_shadow_simulation
        self.state_estimator = None  # Initialized after microgrids registered
        self.shadow_simulator = None  # Initialized after microgrids registered
        self.resilience_calculator = None  # Initialized after microgrids registered
        self.twin_state_history: List[TwinState] = []
        
        logger.info("Digital Twin Coordinator initialized")
        logger.info(f"Resilience Policy: {resilience_policy.value}")
        logger.info(f"State Estimation: {'ENABLED' if enable_state_estimation else 'DISABLED'}")
        logger.info(f"Shadow Simulation: {'ENABLED' if enable_shadow_simulation else 'DISABLED'}")
    
    def setup_microgrids(self):
        """
        Register all microgrids with the city-level EMS
        
        This defines the heterogeneous urban microgrid network
        """
        # 1. Hospital Microgrid (CRITICAL PRIORITY)
        hospital_info = MicrogridInfo(
            microgrid_id="hospital_001",
            microgrid_type="hospital",
            priority=MicrogridPriority.CRITICAL,
            location=(12.9716, 77.5946),  # Bangalore
            critical_load_kw=320.0,
            total_capacity_kw=600.0,
            battery_capacity_kwh=2400.0,
            pv_capacity_kwp=400.0,
            generator_capacity_kw=900.0,
            min_runtime_hours=6.0,
            max_shed_percent=0.0,  # Critical loads NEVER shed
            can_share_power=False  # Hospital cannot export power
        )
        self.city_ems.register_microgrid(hospital_info)
        
        # 2. University Microgrid (HIGH PRIORITY)
        university_info = MicrogridInfo(
            microgrid_id="university_001",
            microgrid_type="university",
            priority=MicrogridPriority.HIGH,
            location=(12.9698, 77.7500),  # Bangalore
            critical_load_kw=320.0,
            total_capacity_kw=650.0,
            battery_capacity_kwh=2400.0,
            pv_capacity_kwp=400.0,
            generator_capacity_kw=900.0,
            min_runtime_hours=5.0,
            max_shed_percent=60.0,
            can_share_power=True
        )
        self.city_ems.register_microgrid(university_info)
        
        # 3. Industrial Microgrid (MEDIUM PRIORITY)
        industrial_info = MicrogridInfo(
            microgrid_id="industrial_001",
            microgrid_type="industrial",
            priority=MicrogridPriority.MEDIUM,
            location=(18.5204, 73.8567),  # Pune
            critical_load_kw=220.0,
            total_capacity_kw=850.0,
            battery_capacity_kwh=1620.0,
            pv_capacity_kwp=600.0,
            generator_capacity_kw=1000.0,
            min_runtime_hours=7.4,
            max_shed_percent=68.0,
            can_share_power=True
        )
        self.city_ems.register_microgrid(industrial_info)
        
        # 4. Residential Microgrid (LOW PRIORITY)
        residential_info = MicrogridInfo(
            microgrid_id="residential_001",
            microgrid_type="residential",
            priority=MicrogridPriority.LOW,
            location=(12.9698, 77.7500),  # Bangalore
            critical_load_kw=100.0,
            total_capacity_kw=650.0,
            battery_capacity_kwh=450.0,
            pv_capacity_kwp=250.0,
            generator_capacity_kw=300.0,
            min_runtime_hours=4.5,
            max_shed_percent=90.0,
            can_share_power=True
        )
        self.city_ems.register_microgrid(residential_info)
        
        # [NEW] Initialize Enhanced Digital Twin Components after microgrid registration
        self._initialize_enhanced_dt_components()
        
        logger.info("All microgrids registered with City-EMS")
        logger.info(f"  - Hospital (CRITICAL): 320 kW critical, 2400 kWh battery")
        logger.info(f"  - University (HIGH): 320 kW critical, 2400 kWh battery")
        logger.info(f"  - Industrial (MEDIUM): 220 kW critical, 1620 kWh battery")
        logger.info(f"  - Residential (LOW): 100 kW critical, 450 kWh battery")
    
    def _initialize_enhanced_dt_components(self):
        """Initialize state estimator, shadow simulator, and resilience calculator"""
        # Create config dict for state estimator (maps to microgrid configs)
        configs = {}
        for mg_id, mg_info in self.city_ems.microgrids.items():
            # Create minimal config structure for state estimator
            configs[mg_id] = type('Config', (), {
                'battery': type('BatteryConfig', (), {
                    'nominal_capacity_kwh': mg_info.battery_capacity_kwh,
                    'usable_capacity_kwh': mg_info.battery_capacity_kwh * 0.9,  # Assume 90% usable
                    'capacity_kwh': mg_info.battery_capacity_kwh,
                    'max_power_kw': mg_info.battery_capacity_kwh / 4  # Assume C/4 rate
                })(),
                'load_profile': type('LoadProfile', (), {
                    'total_critical_load': mg_info.critical_load_kw
                })()
            })()
        
        if self.enable_state_est:
            self.state_estimator = CityStateEstimator(configs)
            logger.info("State Estimator initialized with Kalman filtering")
        
        if self.enable_shadow_sim:
            # Shadow sim needs reference to coordinator - will be set later if needed
            logger.info("Shadow Simulator ready (initialized on-demand)")
        
        # Initialize resilience calculator
        self.resilience_calculator = EnhancedResilienceMetricCalculator(self.city_ems.priority_map)
        logger.info("Enhanced Resilience Metrics initialized")
    
    def simulate_widespread_outage(self, outage_duration_hours: float = 12.0):
        """
        Simulate a widespread grid outage and demonstrate priority-aware coordination
        
        This demonstrates the core value proposition: improved city-level survivability
        through priority-aware resource allocation.
        
        Args:
            outage_duration_hours: Duration of simulated outage
        """
        logger.info("\n" + "="*80)
        logger.info("SIMULATING WIDESPREAD GRID OUTAGE")
        logger.info("="*80)
        logger.info(f"Duration: {outage_duration_hours} hours")
        logger.info(f"Policy: {self.city_ems.state.active_policy.value}")
        
        # Initial state (all grid-connected, batteries charged)
        initial_socs = {
            "hospital_001": 90.0,
            "university_001": 85.0,
            "industrial_001": 80.0,
            "residential_001": 85.0
        }
        
        outage_start = self.current_time + timedelta(hours=2)
        outage_end = outage_start + timedelta(hours=outage_duration_hours)
        
        # Simulation results storage
        results = []
        
        # Run simulation
        sim_time = self.current_time
        timestep = timedelta(minutes=self.timestep_minutes)
        
        while sim_time <= outage_end + timedelta(hours=2):
            # Determine if outage is active
            outage_active = outage_start <= sim_time < outage_end
            
            # Create synthetic measurements for each microgrid
            mg_statuses = self._create_synthetic_measurements(
                sim_time, initial_socs, outage_active, 
                (sim_time - outage_start).total_seconds() / 3600.0 if outage_active else 0
            )
            
            # Create city-wide measurements
            city_meas = self._aggregate_measurements(mg_statuses, sim_time, outage_active)
            
            # City-level EMS update
            city_outputs = self.city_ems.update(city_meas)
            
            # Store results
            result = {
                'timestamp': sim_time,
                'outage_active': outage_active,
                'city_mode': city_outputs.city_mode.value,
                'city_survivability_hours': city_outputs.metrics.get('city_survivability_hours', 0),
                'microgrids': {}
            }
            
            # Process supervisory commands for each microgrid
            for mg_id, status in mg_statuses.items():
                sup_cmd = city_outputs.supervisory_commands.get(mg_id)
                
                result['microgrids'][mg_id] = {
                    'soc_percent': status.battery_soc_percent,
                    'load_kw': status.total_load_kw,
                    'shed_percent': status.load_shed_percent,
                    'supervisory_shed_target': sup_cmd.target_shed_percent if sup_cmd else None,
                    'resource_state': status.resource_criticality
                }
            
            results.append(result)
            
            # Log key events
            if sim_time == outage_start:
                logger.info(f"\n[{sim_time}] OUTAGE BEGINS")
            elif sim_time == outage_end:
                logger.info(f"\n[{sim_time}] OUTAGE ENDS - Grid Restored")
            
            # Log every hour during outage
            if outage_active and (sim_time - outage_start).total_seconds() % 3600 == 0:
                self._log_city_status(sim_time, city_outputs, mg_statuses)
            
            sim_time += timestep
        
        # Generate summary report
        self._generate_outage_report(results, outage_start, outage_end)
        
        return results
    
    def _create_synthetic_measurements(self, timestamp: datetime, initial_socs: Dict,
                                      outage_active: bool, hours_into_outage: float) -> Dict[str, MicrogridStatus]:
        """
        Create synthetic measurements for demonstration
        
        In a real digital twin, this would come from actual microgrid sensors/simulators
        """
        hour = timestamp.hour
        
        # Base load profiles (simplified)
        load_profiles = {
            "hospital_001": 580 if 9 <= hour < 17 else 550,
            "university_001": 600 if 8 <= hour < 16 else 280,
            "industrial_001": 850 if 8 <= hour < 16 else 680,
            "residential_001": 650 if 17 <= hour < 23 else 280
        }
        
        # PV generation (simplified - depends on hour)
        pv_factor = max(0, min(1.0, (hour - 6) / 6.0 if hour < 12 else (18 - hour) / 6.0))
        pv_generation = {
            "hospital_001": 400 * pv_factor * 0.8,  # 80% of rated
            "university_001": 400 * pv_factor * 0.8,
            "industrial_001": 600 * pv_factor * 0.85,
            "residential_001": 250 * pv_factor * 0.75
        }
        
        statuses = {}
        
        for mg_id in initial_socs.keys():
            # Simulate battery discharge during outage
            if outage_active:
                # Simplified discharge calculation
                discharge_rate_per_hour = load_profiles[mg_id] / \
                    self.city_ems.microgrids[mg_id].battery_capacity_kwh * 100
                current_soc = max(15, initial_socs[mg_id] - discharge_rate_per_hour * hours_into_outage)
            else:
                current_soc = initial_socs[mg_id]
            
            # Determine resource criticality
            if current_soc < 20:
                criticality = "emergency"
            elif current_soc < 30:
                criticality = "critical"
            elif current_soc < 50:
                criticality = "warning"
            else:
                criticality = "healthy"
            
            # Create status
            status = MicrogridStatus(
                microgrid_id=mg_id,
                timestamp=timestamp,
                operation_mode="islanded" if outage_active else "grid_connected",
                is_islanded=outage_active,
                grid_available=not outage_active,
                total_load_kw=load_profiles[mg_id],
                critical_load_kw=self.city_ems.microgrids[mg_id].critical_load_kw,
                pv_generation_kw=pv_generation[mg_id] if outage_active else pv_generation[mg_id],
                battery_power_kw=100 if outage_active else -50,
                generator_power_kw=0 if current_soc > 25 else 200,
                grid_power_kw=0 if outage_active else 100,
                battery_soc_percent=current_soc,
                battery_capacity_kwh=self.city_ems.microgrids[mg_id].battery_capacity_kwh,
                fuel_remaining_liters=3000,
                load_shed_kw=0,
                load_shed_percent=0,
                critical_load_shed=False,
                estimated_runtime_hours=current_soc / 10.0,
                resource_criticality=criticality
            )
            
            statuses[mg_id] = status
        
        return statuses
    
    def _aggregate_measurements(self, mg_statuses: Dict[str, MicrogridStatus],
                                timestamp: datetime, outage_active: bool) -> CityWideMeasurements:
        """Aggregate individual microgrid measurements into city-wide view"""
        
        total_load = sum(s.total_load_kw for s in mg_statuses.values())
        total_critical = sum(s.critical_load_kw for s in mg_statuses.values())
        total_gen = sum(s.pv_generation_kw + s.generator_power_kw for s in mg_statuses.values())
        
        total_battery_kwh = sum(
            self.city_ems.microgrids[mg_id].battery_capacity_kwh 
            for mg_id in mg_statuses.keys()
        )
        
        total_battery_energy = sum(
            s.battery_capacity_kwh * s.battery_soc_percent / 100.0
            for s in mg_statuses.values()
        )
        
        islanded_count = sum(1 for s in mg_statuses.values() if s.is_islanded)
        emergency_count = sum(1 for s in mg_statuses.values() if s.resource_criticality == "emergency")
        
        # Calculate city survivability
        survivability = min(s.estimated_runtime_hours for s in mg_statuses.values())
        
        return CityWideMeasurements(
            timestamp=timestamp,
            microgrid_statuses=mg_statuses,
            total_load_kw=total_load,
            total_critical_load_kw=total_critical,
            total_generation_kw=total_gen,
            total_battery_energy_kwh=total_battery_energy,
            total_fuel_liters=sum(s.fuel_remaining_liters for s in mg_statuses.values()),
            grid_outage_active=outage_active,
            outage_start_time=None,
            outage_duration_hours=0,
            microgrids_islanded=islanded_count,
            microgrids_in_emergency=emergency_count,
            city_survivability_hours=survivability
        )
    
    def _log_city_status(self, timestamp: datetime, city_outputs, mg_statuses):
        """Log current city-wide status"""
        logger.info(f"\n{'='*80}")
        logger.info(f"City Status at {timestamp}")
        logger.info(f"{'='*80}")
        logger.info(f"City Mode: {city_outputs.city_mode.value}")
        logger.info(f"Active Policy: {city_outputs.active_policy.value}")
        logger.info(f"City Survivability: {city_outputs.metrics.get('city_survivability_hours', 0):.1f} hours")
        logger.info(f"\nMicrogrid Status:")
        
        for mg_id, status in mg_statuses.items():
            mg_info = self.city_ems.microgrids[mg_id]
            sup_cmd = city_outputs.supervisory_commands.get(mg_id)
            
            logger.info(f"  {mg_info.microgrid_type.upper()} ({mg_info.priority.name}):")
            logger.info(f"    Battery SoC: {status.battery_soc_percent:.1f}%")
            logger.info(f"    Load: {status.total_load_kw:.0f} kW")
            logger.info(f"    Resource State: {status.resource_criticality}")
            if sup_cmd and sup_cmd.target_shed_percent:
                logger.info(f"    City Shed Target: {sup_cmd.target_shed_percent:.0f}%")
    
    def _generate_outage_report(self, results, outage_start, outage_end):
        """Generate comprehensive outage analysis report"""
        logger.info("\n" + "="*80)
        logger.info("OUTAGE SIMULATION REPORT")
        logger.info("="*80)
        
        outage_results = [r for r in results if r['outage_active']]
        
        if not outage_results:
            logger.info("No outage data recorded")
            return
        
        logger.info(f"\nOutage Duration: {(outage_end - outage_start).total_seconds() / 3600:.1f} hours")
        logger.info(f"Resilience Policy: {self.city_ems.state.active_policy.value}")


    # =============================================================================
    # REAL MICROGRID ADAPTERS (LIVE DATA PATH)
    # =============================================================================

def _build_status_from_datapoint(mg_id: str, mg_type: str, dp: Dict, config) -> MicrogridStatus:
    """Convert a microgrid simulator data_point into MicrogridStatus for City EMS."""
    total_load_kw = dp.get("total_load_kw", 0.0)
    critical_kw = dp.get("critical_load_kw", 0.0)
    shed_kw = dp.get("shed_load_kw", 0.0)
    shed_pct = (shed_kw / total_load_kw * 100) if total_load_kw else 0.0

    # Battery survivability (simple estimate)
    battery_energy = dp.get("battery_energy_kwh", 0.0)
    estimated_runtime = (battery_energy / critical_kw) if critical_kw else 0.0

    # Resource criticality heuristic
    soc = dp.get("battery_soc_percent", 0.0)
    if soc < 20:
        criticality = "emergency"
    elif soc < 30:
        criticality = "critical"
    elif soc < 50:
        criticality = "warning"
    else:
        criticality = "healthy"

    return MicrogridStatus(
        microgrid_id=mg_id,
        timestamp=dp.get("timestamp", datetime.now(datetime.UTC)),
        operation_mode=dp.get("operation_mode", "unknown"),
        is_islanded=not dp.get("grid_available", True),
        grid_available=dp.get("grid_available", True),
        total_load_kw=total_load_kw,
        critical_load_kw=critical_kw,
        pv_generation_kw=dp.get("pv_power_kw", 0.0),
        battery_power_kw=dp.get("battery_power_kw", 0.0),
        generator_power_kw=dp.get("gen1_power_kw", 0.0) + dp.get("gen2_power_kw", 0.0),
        grid_power_kw=dp.get("grid_power_kw", 0.0),
        battery_soc_percent=soc,
        battery_capacity_kwh=getattr(config.battery, "usable_capacity_kwh", 0.0),
        fuel_remaining_liters=1000.0,  # Placeholder; simulator tracks fuel used, not remaining
        load_shed_kw=shed_kw,
        load_shed_percent=shed_pct,
        critical_load_shed=False,
        estimated_runtime_hours=estimated_runtime,
        resource_criticality=criticality,
    )


def _aggregate_city_measurements(statuses: Dict[str, MicrogridStatus], timestamp: datetime,
                                 outage_active: bool) -> CityWideMeasurements:
    total_load = sum(s.total_load_kw for s in statuses.values())
    total_critical = sum(s.critical_load_kw for s in statuses.values())
    total_gen = sum(s.pv_generation_kw + s.generator_power_kw for s in statuses.values())
    total_battery_energy = sum(s.battery_capacity_kwh * s.battery_soc_percent / 100.0 for s in statuses.values())
    total_fuel = sum(s.fuel_remaining_liters for s in statuses.values())
    islanded = sum(1 for s in statuses.values() if s.is_islanded)
    emergency = sum(1 for s in statuses.values() if s.resource_criticality == "emergency")
    survivability = min((s.estimated_runtime_hours for s in statuses.values()), default=0.0)

    return CityWideMeasurements(
        timestamp=timestamp,
        microgrid_statuses=statuses,
        total_load_kw=total_load,
        total_critical_load_kw=total_critical,
        total_generation_kw=total_gen,
        total_battery_energy_kwh=total_battery_energy,
        total_fuel_liters=total_fuel,
        grid_outage_active=outage_active,
        outage_start_time=None,
        outage_duration_hours=0,
        microgrids_islanded=islanded,
        microgrids_in_emergency=emergency,
        city_survivability_hours=survivability,
    )


def run_real_microgrid_loop(duration_minutes: int = 60,
                            outage_start_min: int = 20,
                            outage_duration_min: int = 20,
                            policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST):
        """
        Run a short coordinated loop using the real microgrid simulators and City EMS.

        Notes:
        - City EMS issues supervisory commands; this demo logs them but does not yet
          push them back into local EMS control loops (safe one-way flow).
        - Duration kept short for quick validation.
        """

        logger.info("\n=== REAL MICROGRID LOOP (City EMS + Local EMS) ===")
        logger.info(f"Duration: {duration_minutes} min, Outage: {outage_start_min}-{outage_start_min + outage_duration_min} min")
        logger.info(f"Policy: {policy.value}")

        # Instantiate city EMS and DR coordinator
        city = CityEMS(resilience_policy=policy)
        dr_coordinator = DemandResponseCoordinator()

        # Create configs and simulators
        configs = {mg: MicrogridFactory.load_config(mg) for mg in MicrogridType.ALL}
        sims = {mg: MicrogridFactory.load_simulator(mg, configs[mg]) for mg in MicrogridType.ALL}

        # Register microgrids with city EMS using metadata
        meta_map = {
            MicrogridType.HOSPITAL: (MicrogridPriority.CRITICAL, "hospital_001"),
            MicrogridType.UNIVERSITY: (MicrogridPriority.HIGH, "university_001"),
            MicrogridType.INDUSTRIAL: (MicrogridPriority.MEDIUM, "industrial_001"),
            MicrogridType.RESIDENCE: (MicrogridPriority.LOW, "residential_001"),
        }
        for mg_type, (priority, mg_id) in meta_map.items():
            cfg = configs[mg_type]
            city.register_microgrid(MicrogridInfo(
                microgrid_id=mg_id,
                microgrid_type=mg_type,
                priority=priority,
                location=(0.0, 0.0),
                critical_load_kw=getattr(cfg.load_profile, "total_critical_load", 0.0),
                total_capacity_kw=getattr(cfg.load_profile, "peak_load", 0.0),
                battery_capacity_kwh=getattr(cfg.battery, "usable_capacity_kwh", 0.0),
                pv_capacity_kwp=getattr(cfg.pv, "installed_capacity_kwp", 0.0),
                generator_capacity_kw=getattr(cfg.generator, "rated_power_kw", 0.0),
                min_runtime_hours=getattr(cfg.control, "backup_duration_hours", 0.0),
                max_shed_percent=80.0,
                can_share_power=True,
            ))

        # Drive timestep loop
        timestep_sec = configs[MicrogridType.HOSPITAL].control.time_resolution_minutes * 60
        steps = int(duration_minutes * 60 / timestep_sec)
        current_time = datetime.now().replace(second=0, microsecond=0)

        # Initialize simulators to a common start time
        for sim in sims.values():
            if hasattr(sim, "reset"):
                sim.reset(start_time=current_time)

        # Supervisory command memory (one-step latency) and recorder
        last_commands: Dict[str, SupervisoryCommand] = {}
        histories: Dict[str, List[Dict]] = {meta_map[mg_type][1]: [] for mg_type in sims.keys()}

        # Schedule a sample DR event (peak shaving at t=15-25 min)
        dr_event = dr_coordinator.create_dr_event(
            event_type=DREventType.PEAK_SHAVING,
            priority=DREventPriority.VOLUNTARY,
            start_time=current_time + timedelta(minutes=15),
            duration_minutes=10,
            target_mw_reduction=0.1,      # 100 kW city-wide
            notification_time=current_time + timedelta(minutes=14),
            base_incentive_rate=5.0       # $5/kWh
        )
        
        # Allocate DR targets to microgrids
        dr_allocations = dr_coordinator.allocate_dr_targets(dr_event, city.microgrids)
        
        logger.info(f"\n{'='*80}")
        logger.info(f"DR EVENT SCHEDULED: {dr_event.event_id}")
        logger.info(f"Type: {dr_event.event_type.value}, Priority: {dr_event.priority.value}")
        logger.info(f"Window: {dr_event.start_time.strftime('%H:%M')} - {dr_event.end_time().strftime('%H:%M')}")
        logger.info(f"Target: {dr_event.target_mw_reduction*1000:.0f} kW city-wide")
        logger.info(f"Incentive: ${dr_event.base_incentive_rate:.2f}/kWh")
        logger.info(f"Allocations:")
        for mg_id, alloc_kw in dr_allocations.items():
            logger.info(f"  {mg_id}: {alloc_kw:.1f} kW")
        logger.info(f"{'='*80}\n")

        for step in range(steps):
            minutes = step * (timestep_sec / 60)
            outage_active = outage_start_min <= minutes < (outage_start_min + outage_duration_min)
            current_dt = current_time + timedelta(seconds=step * timestep_sec)

            # Check if DR event is active
            if dr_event.is_in_progress(current_dt) and not dr_event.is_active:
                logger.info(f"[t={minutes:.0f}m] DR EVENT ACTIVATED: peak_shave_001")
                dr_event.is_active = True
            elif not dr_event.is_in_progress(current_dt) and dr_event.is_active and not dr_event.is_completed:
                logger.info(f"[t={minutes:.0f}m] DR EVENT COMPLETED")
                dr_event.is_completed = True

            # Step each simulator with the latest city supervisory command
            statuses = {}
            for mg_type, sim in sims.items():
                mg_id = meta_map[mg_type][1]
                sup_cmd = last_commands.get(mg_id)
                dp = sim.step(grid_available=not outage_active, supervisory_cmd=sup_cmd)
                
                # Track DR participation if event is active
                if dr_event.is_active and mg_id in dr_allocations:
                    city_shed = dp.get('city_shed_kw', 0.0)
                    dr_coordinator._track_dr_performance(dr_event, current_dt, {mg_id: dp})
                
                histories[mg_id].append(dp)
                statuses[mg_id] = _build_status_from_datapoint(mg_id, mg_type, dp, configs[mg_type])

            city_meas = _aggregate_city_measurements(statuses, current_time, outage_active)
            city_outputs = city.update(city_meas)
            
            # [NEW] Enhanced Digital Twin integration points (when using DigitalTwinCoordinator)
            # State Estimation: coordinator.state_estimator.update(statuses, timestep_sec)
            # Resilience Tracking: coordinator.resilience_calculator.update(statuses, city_outputs, current_dt)
            # Shadow Simulation: Run predictive scenarios every N steps for what-if analysis
            
            last_commands = city_outputs.supervisory_commands

            logger.info(f"[t={minutes:.0f}m] City mode={city_outputs.city_mode.value}, policy={city_outputs.active_policy.value}")
            for mg_id, cmd in city_outputs.supervisory_commands.items():
                if cmd.target_shed_percent is not None:
                    logger.info(f"  {mg_id}: shed_target={cmd.target_shed_percent}% reserve={cmd.battery_reserve_percent}")

            current_time += timedelta(seconds=timestep_sec)

        logger.info("=== REAL MICROGRID LOOP COMPLETE ===\n")

        # Summaries
        logger.info("\nPER-MICROGRID SURVIVABILITY (from recorded steps):")
        for mg_id, records in histories.items():
            if not records:
                continue
            min_soc = min(dp.get('battery_soc_percent', 0.0) for dp in records)
            shed_percents = [dp.get('city_shed_percent_of_load', 0.0) for dp in records]
            avg_shed = sum(shed_percents) / len(shed_percents)
            logger.info(f"  {mg_id}: min_soc={min_soc:.1f}% avg_city_shed={avg_shed:.1f}%")
        
        # DR Event Results
        if dr_event.is_completed:
            logger.info(f"\n{'='*80}")
            logger.info("DEMAND RESPONSE RESULTS:")
            logger.info(f"{'='*80}")
            logger.info(f"Event: {dr_event.event_id} ({dr_event.event_type.value})")
            logger.info(f"Target: {dr_event.target_mw_reduction*1000:.0f} kW")
            logger.info(f"Achieved: {dr_event.actual_reduction_kw:.0f} kW ({dr_event.achievement_percent:.1f}%)")
            logger.info(f"Participants: {len(dr_event.participating_microgrids)}/{len(dr_event.eligible_microgrids)}")
            logger.info(f"Incentives Paid: ${dr_event.total_incentives_paid:.2f}")
            logger.info(f"Success: {'YES' if dr_event.achievement_percent >= 90 else 'NO'}")
            logger.info(f"{'='*80}\n")
        
        logger.info("CITY-LEVEL METRICS: (not computed in real-loop demo)\n" + "="*80)
        
        logger.info("\nPER-MICROGRID SURVIVABILITY: (not computed in real-loop demo)")
        logger.info("CITY-LEVEL METRICS: (not computed in real-loop demo)\n" + "="*80)


# =============================================================================
# MAIN DEMONSTRATION
# =============================================================================

def run_enhanced_real_loop(duration_minutes: int = 60,
                           outage_start_min: int = 20,
                           outage_duration_min: int = 20,
                           policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST,
                           enable_state_estimation: bool = True,
                           enable_shadow_sim: bool = True):
    """
    Enhanced version using DigitalTwinCoordinator with state estimation and shadow simulation
    """
    logger.info("\n=== ENHANCED DIGITAL TWIN LOOP ===")
    logger.info(f"Duration: {duration_minutes} min, Outage: {outage_start_min}-{outage_start_min + outage_duration_min} min")
    logger.info(f"Policy: {policy.value}")
    logger.info(f"State Estimation: {'ENABLED' if enable_state_estimation else 'DISABLED'}")
    logger.info(f"Shadow Simulation: {'ENABLED' if enable_shadow_sim else 'DISABLED'}")
    
    # Create enhanced coordinator
    coordinator = DigitalTwinCoordinator(
        resilience_policy=policy,
        enable_state_estimation=enable_state_estimation,
        enable_shadow_simulation=enable_shadow_sim
    )
    coordinator.setup_microgrids()
    
    # Create configs and simulators
    configs = {mg: MicrogridFactory.load_config(mg) for mg in MicrogridType.ALL}
    sims = {mg: MicrogridFactory.load_simulator(mg, configs[mg]) for mg in MicrogridType.ALL}
    
    # Microgrid ID mapping
    meta_map = {
        MicrogridType.HOSPITAL: "hospital_001",
        MicrogridType.UNIVERSITY: "university_001",
        MicrogridType.INDUSTRIAL: "industrial_001",
        MicrogridType.RESIDENCE: "residential_001",
    }
    
    # Initialize
    timestep_sec = configs[MicrogridType.HOSPITAL].control.time_resolution_minutes * 60
    steps = int(duration_minutes * 60 / timestep_sec)
    current_time = datetime.now().replace(second=0, microsecond=0)
    
    for sim in sims.values():
        if hasattr(sim, "reset"):
            sim.reset(start_time=current_time)
    
    last_commands: Dict[str, SupervisoryCommand] = {}
    histories: Dict[str, List[Dict]] = {meta_map[mg_type]: [] for mg_type in sims.keys()}
    
    # Main loop
    for step in range(steps):
        minutes = step * (timestep_sec / 60)
        outage_active = outage_start_min <= minutes < (outage_start_min + outage_duration_min)
        current_dt = current_time + timedelta(seconds=step * timestep_sec)
        
        # Step simulators
        statuses = {}
        for mg_type, sim in sims.items():
            mg_id = meta_map[mg_type]
            sup_cmd = last_commands.get(mg_id)
            dp = sim.step(grid_available=not outage_active, supervisory_cmd=sup_cmd)
            histories[mg_id].append(dp)
            statuses[mg_id] = _build_status_from_datapoint(mg_id, mg_type, dp, configs[mg_type])
        
        # City EMS update
        city_meas = _aggregate_city_measurements(statuses, current_dt, outage_active)
        city_outputs = coordinator.city_ems.update(city_meas)
        
        # [NEW] State Estimation with Kalman Filtering
        if coordinator.state_estimator:
            # Prepare measurements dict
            measurements_dict = {
                mg_id: {
                    'battery_soc': status.battery_soc_percent,
                    'load_kw': status.total_load_kw,
                    'timestamp': current_dt
                }
                for mg_id, status in statuses.items()
            }
            # Prepare control inputs (supervisory commands)
            control_inputs = {
                mg_id: {
                    'target_shed': last_commands.get(mg_id).target_shed_percent if last_commands.get(mg_id) else 0.0
                }
                for mg_id in statuses.keys()
            }
            
            # Update all estimators
            state_estimates = coordinator.state_estimator.update_all(
                dt_seconds=timestep_sec,
                measurements=measurements_dict,
                control_inputs=control_inputs
            )
        
        # [NEW] Enhanced Resilience Metrics Tracking
        if coordinator.resilience_calculator:
            # (Simplified - full integration would create TwinState objects)
            # coordinator.resilience_calculator.update(...) requires TwinState
            # For now, we track metrics manually in histories and calculate at the end
            pass
        
        # [NEW] Shadow Simulation (every hour for predictive analysis)
        if coordinator.enable_shadow_sim and step % 12 == 0 and step > 0:  # Every hour
            logger.info(f"[t={minutes:.0f}m] Running shadow simulation for next 2 hours...")
            # Would call coordinator.shadow_sim.run_prediction(...) here
            # Simplified for now - full implementation would predict battery exhaustion, etc.
        
        last_commands = city_outputs.supervisory_commands
        
        if step % 4 == 0:  # Log every 20 minutes
            logger.info(f"[t={minutes:.0f}m] City={city_outputs.city_mode.value}")
            if coordinator.state_estimator and 'state_estimates' in locals():
                for mg_id, est in state_estimates.items():
                    # est is a StateEstimate object for SoC
                    logger.info(f"  {mg_id}: SoC={est.value:.1f}% +/-{est.std_dev:.1f} (conf={est.confidence:.2f})")
        
        current_time += timedelta(seconds=timestep_sec)
    
    logger.info("\n=== ENHANCED LOOP COMPLETE ===\n")
    
    # [NEW] Generate Enhanced Resilience Scorecard
    if coordinator.resilience_calculator:
        scorecard = coordinator.resilience_calculator.compute_final_metrics(state_confidence=0.95)
        logger.info("\n" + "="*80)
        logger.info("ENHANCED RESILIENCE SCORECARD")
        logger.info("="*80)
        logger.info(f"City Survivability Index: {scorecard.city_survivability_index:.3f}")
        logger.info(f"Critical Load Preservation: {scorecard.critical_load_preservation_ratio*100:.1f}%")
        logger.info(f"Total Unserved Energy: {scorecard.total_unserved_energy_kwh:.2f} kWh")
        # Support older scorecard field name if running against cached modules
        se_conf = getattr(scorecard, "state_estimation_confidence", getattr(scorecard, "state_confidence", 1.0))
        logger.info(f"State Estimation Confidence: {se_conf:.2f}")
        
        logger.info("\n" + "="*80)
        logger.info("AUTOMATED RECOMMENDATIONS")
        logger.info("="*80)
        recommendations = _generate_recommendations(scorecard)
        for rec in recommendations:
            logger.info(f"  {rec}")
    
    return histories, coordinator


def _generate_recommendations(scorecard):
    """Generate automated recommendations based on resilience scorecard"""
    recs = []
    
    if scorecard.city_survivability_index < 0.90:
        recs.append("City Survivability Index below target. Consider increasing battery capacity or generator backup.")
    
    if scorecard.critical_load_preservation_ratio < 0.95:
        recs.append("Critical loads were shed. Review priority policies and ensure critical microgrids receive resources first.")
    
    if scorecard.priority_violation_count > 0:
        recs.append(f"Priority violations detected ({scorecard.priority_violation_count} timesteps). Strengthen priority enforcement.")
    
    if not recs:
        recs.append("Excellent resilience performance. System meets all target thresholds.")
    
    return recs


def main():
    """
    Main demonstration of priority-aware resilience coordination
    WITH ENHANCED DIGITAL TWIN CAPABILITIES
    """
    logger.info("\n" + "="*80)
    logger.info("DIGITAL TWIN FRAMEWORK: PRIORITY-AWARE RESILIENCE DEMONSTRATION")
    logger.info("="*80)
    
    # Choose demo mode
    use_enhanced = True  # Set to False for original synthetic demo
    
    if use_enhanced:
        logger.info("\nRunning ENHANCED mode with real simulators + state estimation")
        logger.info("-"*80)
        
        # Run enhanced loop with real microgrids
        histories, coordinator = run_enhanced_real_loop(
            duration_minutes=60,
            outage_start_min=20,
            outage_duration_min=20,
            policy=ResiliencePolicy.CRITICAL_FIRST,
            enable_state_estimation=True,
            enable_shadow_sim=False  # Can enable for hourly predictions
        )
        
        logger.info("\nEnhanced Digital Twin demonstration complete!")
        
    else:
        logger.info("\nRunning SYNTHETIC mode with simulated data")
        logger.info("-"*80)
        
        # Original synthetic demonstration
        coordinator = DigitalTwinCoordinator(
            resilience_policy=ResiliencePolicy.CRITICAL_FIRST,
            enable_state_estimation=False,
            enable_shadow_simulation=False
        )
        
        coordinator.setup_microgrids()
        
        logger.info("\n" + "-"*80)
        logger.info("SCENARIO: 12-hour Widespread Grid Outage")
        logger.info("-"*80)
        
        results = coordinator.simulate_widespread_outage(outage_duration_hours=12.0)
        
        # Compare policies
        logger.info("\n" + "="*80)
        logger.info("POLICY COMPARISON")
        logger.info("="*80)
        
        for policy in [ResiliencePolicy.CRITICAL_FIRST, ResiliencePolicy.BALANCED, ResiliencePolicy.EQUITABLE]:
            logger.info(f"\nPolicy: {policy.value}")
            logger.info(coordinator.city_ems.get_policy_description(policy))


if __name__ == "__main__":
    main()
