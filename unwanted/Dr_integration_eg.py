"""
Demand Response Integration Example

This module demonstrates how to integrate DR events into the city-level
coordination framework with scheduled events, participation tracking,
and comprehensive performance metrics.

Scenarios Demonstrated:
1. Voluntary Economic DR (peak shaving)
2. Mandatory Emergency DR (grid emergency)
3. Multiple overlapping DR events
4. DR performance analysis and incentive calculation
"""

from datetime import datetime, timedelta
from typing import Dict, List
import logging

# City-level coordination
from city_ems import (
    CityEMS, MicrogridInfo, MicrogridPriority, ResiliencePolicy,
    MicrogridStatus, CityWideMeasurements
)

# Demand Response
from demand_response import (
    DemandResponseCoordinator, DREvent, DREventType, DREventPriority,
    DREventMetrics
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# DR INTEGRATION COORDINATOR
# =============================================================================

class DRIntegrationCoordinator:
    """
    Coordinates DR events with city-level microgrid management
    
    Integrates:
    - City-EMS (supervisory coordination)
    - DR Coordinator (demand response events)
    - Local EMSs (via simulated responses)
    """
    
    def __init__(self, resilience_policy: ResiliencePolicy = ResiliencePolicy.CRITICAL_FIRST):
        """Initialize integrated coordinator"""
        # Initialize city-level EMS
        self.city_ems = CityEMS(resilience_policy=resilience_policy)
        
        # Simulation state
        self.current_time = datetime(2025, 6, 15, 0, 0, 0)  # Summer day for AC loads
        self.timestep_minutes = 5
        
        # Track DR performance
        self.dr_participation_log = []
        
        logger.info("DR Integration Coordinator initialized")
    
    def setup_microgrids(self):
        """Register microgrids with city-level EMS"""
        # Same setup as before - Hospital, University, Industrial, Residential
        
        hospital_info = MicrogridInfo(
            microgrid_id="hospital_001",
            microgrid_type="hospital",
            priority=MicrogridPriority.CRITICAL,
            location=(12.9716, 77.5946),
            critical_load_kw=320.0,
            total_capacity_kw=600.0,
            battery_capacity_kwh=2400.0,
            pv_capacity_kwp=400.0,
            generator_capacity_kw=900.0,
            min_runtime_hours=6.0,
            max_shed_percent=10.0,  # Hospital can shed 10% for economic DR
            can_share_power=False
        )
        self.city_ems.register_microgrid(hospital_info)
        
        university_info = MicrogridInfo(
            microgrid_id="university_001",
            microgrid_type="university",
            priority=MicrogridPriority.HIGH,
            location=(12.9698, 77.7500),
            critical_load_kw=320.0,
            total_capacity_kw=650.0,
            battery_capacity_kwh=2400.0,
            pv_capacity_kwp=400.0,
            generator_capacity_kw=900.0,
            min_runtime_hours=5.0,
            max_shed_percent=40.0,
            can_share_power=True
        )
        self.city_ems.register_microgrid(university_info)
        
        industrial_info = MicrogridInfo(
            microgrid_id="industrial_001",
            microgrid_type="industrial",
            priority=MicrogridPriority.MEDIUM,
            location=(18.5204, 73.8567),
            critical_load_kw=220.0,
            total_capacity_kw=850.0,
            battery_capacity_kwh=1620.0,
            pv_capacity_kwp=600.0,
            generator_capacity_kw=1000.0,
            min_runtime_hours=7.4,
            max_shed_percent=50.0,
            can_share_power=True
        )
        self.city_ems.register_microgrid(industrial_info)
        
        residential_info = MicrogridInfo(
            microgrid_id="residential_001",
            microgrid_type="residential",
            priority=MicrogridPriority.LOW,
            location=(12.9698, 77.7500),
            critical_load_kw=100.0,
            total_capacity_kw=650.0,
            battery_capacity_kwh=450.0,
            pv_capacity_kwp=250.0,
            generator_capacity_kw=300.0,
            min_runtime_hours=4.5,
            max_shed_percent=60.0,
            can_share_power=True
        )
        self.city_ems.register_microgrid(residential_info)
        
        logger.info("All microgrids registered")
    
    def schedule_dr_events(self) -> List[DREvent]:
        """
        Schedule DR events for demonstration
        
        Returns:
            List of scheduled DR events
        """
        events = []
        base_time = self.current_time
        
        # Event 1: Voluntary Peak Shaving (2-hour afternoon peak)
        event1 = self.city_ems.dr_coordinator.create_dr_event(
            event_type=DREventType.PEAK_SHAVING,
            priority=DREventPriority.VOLUNTARY,
            start_time=base_time + timedelta(hours=14),  # 2 PM
            duration_minutes=120,  # 2 hours
            target_mw_reduction=0.5,  # 500 kW city-wide
            notification_time=base_time + timedelta(hours=12),  # 2-hour notice
            base_incentive_rate=0.50  # $0.50/kWh
        )
        
        # Allocate to microgrids
        self.city_ems.dr_coordinator.allocate_dr_targets(
            event1, self.city_ems.microgrids
        )
        events.append(event1)
        
        # Event 2: Mandatory Emergency DR (1-hour grid emergency)
        event2 = self.city_ems.dr_coordinator.create_dr_event(
            event_type=DREventType.EMERGENCY,
            priority=DREventPriority.EMERGENCY,
            start_time=base_time + timedelta(hours=17),  # 5 PM (peak)
            duration_minutes=60,  # 1 hour
            target_mw_reduction=0.8,  # 800 kW city-wide
            notification_time=base_time + timedelta(hours=16, minutes=45),  # 15-min notice
            base_incentive_rate=1.00  # $1.00/kWh (emergency rate)
        )
        
        self.city_ems.dr_coordinator.allocate_dr_targets(
            event2, self.city_ems.microgrids
        )
        events.append(event2)
        
        # Event 3: Voluntary Economic DR (3-hour evening)
        event3 = self.city_ems.dr_coordinator.create_dr_event(
            event_type=DREventType.ECONOMIC,
            priority=DREventPriority.RECOMMENDED,
            start_time=base_time + timedelta(hours=19),  # 7 PM
            duration_minutes=180,  # 3 hours
            target_mw_reduction=0.4,  # 400 kW
            notification_time=base_time + timedelta(hours=17),  # 2-hour notice
            base_incentive_rate=0.40  # $0.40/kWh
        )
        
        self.city_ems.dr_coordinator.allocate_dr_targets(
            event3, self.city_ems.microgrids
        )
        events.append(event3)
        
        logger.info(f"\n{'='*80}")
        logger.info("DR EVENTS SCHEDULED")
        logger.info(f"{'='*80}")
        for event in events:
            logger.info(f"\nEvent: {event.event_id}")
            logger.info(f"  Type: {event.event_type.value}")
            logger.info(f"  Priority: {event.priority.value}")
            logger.info(f"  Start: {event.start_time.strftime('%H:%M')}")
            logger.info(f"  Duration: {event.duration_minutes} min")
            logger.info(f"  Target: {event.target_mw_reduction:.2f} MW")
            logger.info(f"  Incentive: ${event.base_incentive_rate:.2f}/kWh")
        
        return events
    
    def simulate_dr_day(self):
        """
        Simulate a full day with multiple DR events
        
        Demonstrates:
        - DR event detection and command generation
        - Local EMS participation (simulated)
        - Performance tracking
        - Incentive calculation
        """
        logger.info("\n" + "="*80)
        logger.info("SIMULATING DR DAY")
        logger.info("="*80)
        
        # Schedule DR events
        scheduled_events = self.schedule_dr_events()
        
        # Run simulation for 24 hours
        sim_time = self.current_time
        end_time = self.current_time + timedelta(hours=24)
        timestep = timedelta(minutes=self.timestep_minutes)
        
        results = []
        
        while sim_time <= end_time:
            # Create synthetic measurements
            mg_statuses = self._create_measurements(sim_time, scheduled_events)
            
            # Aggregate to city-wide
            city_meas = self._aggregate_measurements(mg_statuses, sim_time)
            
            # City-level EMS update (includes DR coordination)
            city_outputs = self.city_ems.update(city_meas)
            
            # Log DR commands
            if city_outputs.dr_commands:
                for dr_cmd in city_outputs.dr_commands:
                    logger.info(f"\n[{sim_time.strftime('%H:%M')}] {dr_cmd}")
            
            # Store results
            result = {
                'timestamp': sim_time,
                'hour': sim_time.hour,
                'dr_active': len(city_outputs.dr_commands) > 0,
                'total_load_kw': city_meas.total_load_kw,
                'microgrids': {mg_id: status.total_load_kw 
                              for mg_id, status in mg_statuses.items()}
            }
            results.append(result)
            
            # Advance time
            sim_time += timestep
        
        # Generate DR reports
        self._generate_dr_reports()
        
        return results
    
    def _create_measurements(self, timestamp: datetime, 
                           scheduled_events: List[DREvent]) -> Dict[str, MicrogridStatus]:
        """
        Create synthetic measurements with DR participation
        
        Simulates local EMS response to DR commands
        """
        hour = timestamp.hour
        
        # Check if any DR event is active
        dr_reduction_factor = 1.0
        for event in scheduled_events:
            if event.is_in_progress(timestamp):
                # Simulate participation
                if event.priority == DREventPriority.EMERGENCY:
                    dr_reduction_factor = 0.85  # 15% reduction
                elif event.priority == DREventPriority.MANDATORY:
                    dr_reduction_factor = 0.90  # 10% reduction
                else:
                    dr_reduction_factor = 0.92  # 8% reduction (voluntary)
                break
        
        # Base load profiles (summer day with AC)
        load_profiles = {
            "hospital_001": 580 if 9 <= hour < 17 else 550,
            "university_001": 600 if 8 <= hour < 16 else 280,
            "industrial_001": 850 if 8 <= hour < 16 else 680,
            "residential_001": 650 if 17 <= hour < 23 else 280  # High evening load (AC)
        }
        
        # Apply DR reduction
        if dr_reduction_factor < 1.0:
            load_profiles = {k: v * dr_reduction_factor for k, v in load_profiles.items()}
        
        # PV generation
        pv_factor = max(0, min(1.0, (hour - 6) / 6.0 if hour < 12 else (18 - hour) / 6.0))
        pv_generation = {
            "hospital_001": 400 * pv_factor * 0.8,
            "university_001": 400 * pv_factor * 0.8,
            "industrial_001": 600 * pv_factor * 0.85,
            "residential_001": 250 * pv_factor * 0.75
        }
        
        statuses = {}
        
        for mg_id in load_profiles.keys():
            status = MicrogridStatus(
                microgrid_id=mg_id,
                timestamp=timestamp,
                operation_mode="grid_connected",
                is_islanded=False,
                grid_available=True,
                total_load_kw=load_profiles[mg_id],
                critical_load_kw=self.city_ems.microgrids[mg_id].critical_load_kw,
                pv_generation_kw=pv_generation[mg_id],
                battery_power_kw=-50 if pv_factor > 0.5 else 50,
                generator_power_kw=0,
                grid_power_kw=load_profiles[mg_id] - pv_generation[mg_id],
                battery_soc_percent=75.0,
                battery_capacity_kwh=self.city_ems.microgrids[mg_id].battery_capacity_kwh,
                fuel_remaining_liters=3000,
                load_shed_kw=0,
                load_shed_percent=0,
                critical_load_shed=False,
                estimated_runtime_hours=10.0,
                resource_criticality="healthy"
            )
            statuses[mg_id] = status
        
        return statuses
    
    def _aggregate_measurements(self, mg_statuses: Dict[str, MicrogridStatus],
                                timestamp: datetime) -> CityWideMeasurements:
        """Aggregate microgrid measurements"""
        total_load = sum(s.total_load_kw for s in mg_statuses.values())
        total_critical = sum(s.critical_load_kw for s in mg_statuses.values())
        total_gen = sum(s.pv_generation_kw for s in mg_statuses.values())
        
        total_battery_kwh = sum(
            self.city_ems.microgrids[mg_id].battery_capacity_kwh 
            for mg_id in mg_statuses.keys()
        )
        
        return CityWideMeasurements(
            timestamp=timestamp,
            microgrid_statuses=mg_statuses,
            total_load_kw=total_load,
            total_critical_load_kw=total_critical,
            total_generation_kw=total_gen,
            total_battery_energy_kwh=total_battery_kwh,
            total_fuel_liters=12000,
            grid_outage_active=False,
            outage_start_time=None,
            outage_duration_hours=0,
            microgrids_islanded=0,
            microgrids_in_emergency=0,
            city_survivability_hours=10.0
        )
    
    def _generate_dr_reports(self):
        """Generate comprehensive DR performance reports"""
        logger.info("\n" + "="*80)
        logger.info("DR PERFORMANCE REPORT")
        logger.info("="*80)
        
        # Get summary
        summary = self.city_ems.dr_coordinator.get_summary_report()
        
        logger.info(f"\nOVERALL SUMMARY:")
        logger.info(f"  Total Events: {summary['total_events']}")
        logger.info(f"  Target MW: {summary.get('total_target_mw', 0):.2f}")
        logger.info(f"  Actual MW: {summary.get('total_actual_mw', 0):.2f}")
        logger.info(f"  Achievement: {summary.get('overall_achievement_percent', 0):.1f}%")
        logger.info(f"  Total Incentives: ${summary.get('total_incentives_paid', 0):.2f}")
        logger.info(f"  Successful Events: {summary.get('events_successful', 0)}/{summary['total_events']}")
        
        # Individual event metrics
        for event_id in self.city_ems.dr_coordinator.completed_events.keys():
            metrics = self.city_ems.dr_coordinator.calculate_event_metrics(event_id)
            if metrics:
                logger.info(f"\n{'-'*80}")
                logger.info(f"Event: {event_id}")
                logger.info(f"  Type: {metrics.event_type}, Priority: {metrics.priority}")
                logger.info(f"  Participation: {metrics.participating_count}/{metrics.eligible_count} "
                          f"({metrics.participation_rate_percent:.1f}%)")
                logger.info(f"  Target: {metrics.target_reduction_kw:.1f} kW")
                logger.info(f"  Actual: {metrics.actual_reduction_kw:.1f} kW")
                logger.info(f"  Achievement: {metrics.achievement_percent:.1f}%")
                logger.info(f"  Duration: {metrics.duration_minutes} min")
                logger.info(f"  Lead Time: {metrics.notification_lead_time_minutes} min")
                logger.info(f"  Incentives Paid: ${metrics.total_incentives_paid:.2f}")
                logger.info(f"  Penalties: ${metrics.total_penalties_assessed:.2f}")
                
                logger.info(f"\n  Performance by Type:")
                for mg_type, perf in metrics.performance_by_type.items():
                    achievement = (perf['total_actual'] / perf['total_target'] * 100.0) if perf['total_target'] > 0 else 0
                    logger.info(f"    {mg_type.upper()}:")
                    logger.info(f"      Target: {perf['total_target']:.1f} kW")
                    logger.info(f"      Actual: {perf['total_actual']:.1f} kW")
                    logger.info(f"      Achievement: {achievement:.1f}%")
                    logger.info(f"      Incentives: ${perf['total_incentives']:.2f}")


# =============================================================================
# MAIN DEMONSTRATION
# =============================================================================

def main():
    """
    Main DR integration demonstration
    """
    logger.info("\n" + "="*80)
    logger.info("DEMAND RESPONSE INTEGRATION DEMONSTRATION")
    logger.info("="*80)
    
    # Initialize coordinator
    coordinator = DRIntegrationCoordinator(
        resilience_policy=ResiliencePolicy.CRITICAL_FIRST
    )
    
    # Setup microgrids
    coordinator.setup_microgrids()
    
    # Simulate DR day
    logger.info("\n" + "-"*80)
    logger.info("SCENARIO: Summer Day with Multiple DR Events")
    logger.info("-"*80)
    
    results = coordinator.simulate_dr_day()
    
    logger.info("\n" + "="*80)
    logger.info("SIMULATION COMPLETE")
    logger.info("="*80)


if __name__ == "__main__":
    main()