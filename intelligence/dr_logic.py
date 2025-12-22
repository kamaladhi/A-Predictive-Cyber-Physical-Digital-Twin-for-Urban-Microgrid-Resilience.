import logging
import random
import numpy as np
from datetime import datetime, timedelta
from typing import List, Tuple
from Core.statemodel import MicrogridState, DRAlert, DRAction, AlertLevel, DRActionType
from intelligence.datafusion import DataFusionEngine

logger = logging.getLogger(__name__)


class DemandResponseEngine:
    """
    PAPER-COMPLIANT Demand Response Engine
    Implements:
    1. Peak prediction
    2. Cost-based alerts
    3. Load shifting via Hybrid Genetic Algorithm (paper's method)
    4. Battery dispatch optimization
    5. Net-zero emissions control
    """
    
    def __init__(self, fusion_engine: DataFusionEngine):
        self.fusion = fusion_engine
        
        # Thresholds from paper
        self.peak_price_threshold = 0.040  # $/kWh
        self.critical_load_ratio = 0.90
        self.low_battery_threshold = 0.20
        self.max_capacity = 10.0  # kW
        self.emissions_threshold = 0.3  # kg CO2/kWh (paper's net-zero control)
        
        # Hybrid GA parameters (paper's optimization method)
        self.ga_population_size = 30
        self.ga_generations = 20
        self.ga_mutation_rate = 0.1
        self.ga_crossover_rate = 0.7
        
        self.active_alerts = []
        
        logger.info("DR Engine initialized (Paper-compliant with Hybrid GA)")
    
    def analyze_and_respond(self, state: MicrogridState) -> dict:
        """Main DR analysis - PAPER COMPLIANT"""
        alerts = []
        actions = []
        
        # 1. Peak Price Prediction (paper requirement)
        if state.forecast:
            peak_time = state.forecast.get_peak_price_time()
            peak_value = state.forecast.get_peak_price_value()
            
            if peak_value > self.peak_price_threshold:
                alert = self._create_peak_price_alert(state, peak_time, peak_value)
                alerts.append(alert)
                
                # Generate actions
                actions.extend(self._generate_peak_actions(state))
        
        # 2. Critical Load Detection
        load_ratio = state.total_load / self.max_capacity
        if load_ratio > self.critical_load_ratio:
            alerts.append(DRAlert(
                timestamp=datetime.now(),
                level=AlertLevel.CRITICAL,
                alert_type='critical_load',
                message=f'Load at {load_ratio:.0%} of capacity',
                recommended_actions=[
                    'Reduce non-essential loads immediately',
                    'Consider load shedding'
                ]
            ))
        
        # 3. Battery Optimization
        battery_actions = self._optimize_battery(state)
        actions.extend(battery_actions)
        
        # 4. Low Renewable Penetration
        if state.metrics.renewable_penetration < 0.30:
            actions.extend(self._boost_renewable_usage(state))
        
        # 5. PAPER REQUIREMENT: Hybrid Genetic Algorithm for RTP/TOU
        shiftable_apps = [app for app in state.appliances.values() if app.is_shiftable and app.power > 0.3]
        if shiftable_apps and state.forecast and state.forecast.price_forecast:
            schedule = self._hybrid_genetic_algorithm(state, shiftable_apps)
            
            for i, app in enumerate(shiftable_apps):
                actions.append(DRAction(
                    timestamp=datetime.now(),
                    action_type=DRActionType.LOAD_SHIFT,
                    target_appliance=app.name,
                    scheduled_time=datetime.now() + timedelta(hours=schedule[i]),
                    reason="Optimized via hybrid GA for RTP/TOU (paper method)",
                    expected_savings=app.power * 0.01 * schedule[i]
                ))
        
        # 6. PAPER REQUIREMENT: Net-zero emissions alert
        if state.emissions_intensity > self.emissions_threshold:
            alerts.append(DRAlert(
                timestamp=datetime.now(),
                level=AlertLevel.WARNING,
                alert_type='high_emissions',
                message=f"High emissions ({state.emissions_intensity:.3f} kg/kWh) - increase RER usage",
                recommended_actions=[
                    'Increase renewable energy usage',
                    'Shift loads to high solar periods',
                    'Reduce fuel cell generation'
                ]
            ))
        
        # 7. Cost Savings Opportunities
        savings_actions = self._identify_savings(state)
        actions.extend(savings_actions)
        
        # Store active alerts
        self.active_alerts = alerts
        
        return {
            'alerts': [alert.to_dict() for alert in alerts],
            'actions': [action.to_dict() for action in actions],
            'timestamp': datetime.now().isoformat()
        }
    
    def _hybrid_genetic_algorithm(self, state: MicrogridState, 
                                  shiftable_apps: List) -> List[float]:
        """
        PAPER'S METHOD: Hybrid Genetic Algorithm for RTP/TOU optimization
        Optimizes appliance scheduling to minimize cost under price forecasts
        
        Returns: List of delay times (hours) for each shiftable appliance
        """
        if not shiftable_apps:
            return []
        
        num_apps = len(shiftable_apps)
        max_delay = 4.0  # Maximum delay in hours
        
        # Initialize population (random schedules)
        population = [
            [random.uniform(0, max_delay) for _ in range(num_apps)]
            for _ in range(self.ga_population_size)
        ]
        
        best_schedule = None
        best_fitness = float('inf')
        
        # Evolutionary optimization
        for generation in range(self.ga_generations):
            # Evaluate fitness for all individuals
            fitness_scores = []
            for individual in population:
                fitness = self._evaluate_schedule_fitness(state, shiftable_apps, individual)
                fitness_scores.append(fitness)
                
                if fitness < best_fitness:
                    best_fitness = fitness
                    best_schedule = individual.copy()
            
            # Selection: Tournament selection
            selected = self._tournament_selection(population, fitness_scores)
            
            # Crossover
            offspring = []
            for i in range(0, len(selected), 2):
                if i + 1 < len(selected):
                    if random.random() < self.ga_crossover_rate:
                        child1, child2 = self._crossover(selected[i], selected[i+1])
                        offspring.extend([child1, child2])
                    else:
                        offspring.extend([selected[i], selected[i+1]])
                else:
                    offspring.append(selected[i])
            
            # Mutation with adaptive step size (CMA-like adaptation)
            step_size = 0.5 * (1 - generation / self.ga_generations)  # Adaptive
            for individual in offspring:
                if random.random() < self.ga_mutation_rate:
                    self._mutate(individual, step_size, max_delay)
            
            # Elitism: Keep best individual
            offspring[0] = best_schedule
            
            population = offspring[:self.ga_population_size]
            
            if generation % 5 == 0:
                logger.debug(f"GA Generation {generation}: Best fitness = {best_fitness:.3f}")
        
        logger.info(f"GA optimization complete. Best cost: ${best_fitness:.2f}")
        return best_schedule
    
    def _evaluate_schedule_fitness(self, state: MicrogridState, 
                                   apps: List, schedule: List[float]) -> float:
        """
        Fitness function: Total cost of appliance operation under given schedule
        Lower is better
        """
        total_cost = 0.0
        
        for i, app in enumerate(apps):
            delay_hours = schedule[i]
            scheduled_time = datetime.now() + timedelta(hours=delay_hours)
            
            # Get forecasted price at scheduled time
            price = self._get_forecast_price(state, scheduled_time)
            
            # Cost = power × price × duration (assume 1 hour operation)
            cost = app.power * price * 1.0
            
            # Penalty for excessive delays (user convenience)
            delay_penalty = 0.01 * delay_hours  # Small penalty per hour
            
            total_cost += cost + delay_penalty
        
        return total_cost
    
    def _get_forecast_price(self, state: MicrogridState, time: datetime) -> float:
        """Get forecasted price at given time, or current price if no forecast"""
        if not state.forecast or not state.forecast.price_forecast:
            return state.grid.current_price
        
        # Find closest forecast point
        min_diff = float('inf')
        closest_price = state.grid.current_price
        
        for forecast_point in state.forecast.price_forecast:
            forecast_time = datetime.fromisoformat(forecast_point['time'])
            diff = abs((forecast_time - time).total_seconds())
            
            if diff < min_diff:
                min_diff = diff
                closest_price = forecast_point['value']
        
        return closest_price
    
    def _tournament_selection(self, population: List, fitness: List, 
                             tournament_size: int = 3) -> List:
        """Tournament selection for GA"""
        selected = []
        
        for _ in range(len(population)):
            # Random tournament
            tournament_indices = random.sample(range(len(population)), tournament_size)
            tournament_fitness = [fitness[i] for i in tournament_indices]
            
            # Select best from tournament
            winner_idx = tournament_indices[tournament_fitness.index(min(tournament_fitness))]
            selected.append(population[winner_idx].copy())
        
        return selected
    
    def _crossover(self, parent1: List[float], parent2: List[float]) -> Tuple[List[float], List[float]]:
        """Single-point crossover"""
        if len(parent1) <= 1:
            return parent1.copy(), parent2.copy()
        
        crossover_point = random.randint(1, len(parent1) - 1)
        
        child1 = parent1[:crossover_point] + parent2[crossover_point:]
        child2 = parent2[:crossover_point] + parent1[crossover_point:]
        
        return child1, child2
    
    def _mutate(self, individual: List[float], step_size: float, max_delay: float):
        """Gaussian mutation with adaptive step size"""
        for i in range(len(individual)):
            if random.random() < self.ga_mutation_rate:
                mutation = random.gauss(0, step_size)
                individual[i] = max(0, min(max_delay, individual[i] + mutation))
    
    def _create_peak_price_alert(self, state: MicrogridState, 
                                peak_time: datetime, peak_value: float) -> DRAlert:
        """Create alert for upcoming peak price"""
        time_to_peak = (peak_time - datetime.now()).total_seconds() / 3600
        
        return DRAlert(
            timestamp=datetime.now(),
            level=AlertLevel.WARNING,
            alert_type='peak_price_upcoming',
            message=f'High price (${peak_value:.3f}/kWh) in {time_to_peak:.1f}h',
            recommended_actions=self._get_peak_recommendations(state),
            potential_savings=self._calculate_peak_savings(state, peak_value),
            expires_at=peak_time
        )
    
    def _generate_peak_actions(self, state: MicrogridState) -> List[DRAction]:
        """Generate actions for peak periods"""
        actions = []
        
        shiftable = [app for app in state.appliances.values() 
                    if app.is_shiftable and app.power > 0.3]
        
        for app in shiftable:
            actions.append(DRAction(
                timestamp=datetime.now(),
                action_type=DRActionType.LOAD_SHIFT,
                target_appliance=app.name,
                target_power=app.power,
                scheduled_time=datetime.now() + timedelta(hours=3),
                reason='Avoid peak pricing period',
                expected_savings=app.power * 3 * 0.01
            ))
        
        return actions
    
    def _optimize_battery(self, state: MicrogridState) -> List[DRAction]:
        """Battery dispatch optimization"""
        actions = []
        
        # Charge during low price + excess solar
        if (state.grid.current_price < 0.030 and
            state.generation.solar > state.total_load and
            state.battery.soc < 80):
            
            charge_power = min(
                state.generation.solar - state.total_load,
                state.battery.max_charge_rate,
                (80 - state.battery.soc) / 100 * state.battery.capacity
            )
            
            actions.append(DRAction(
                timestamp=datetime.now(),
                action_type=DRActionType.BATTERY_CHARGE,
                target_power=charge_power,
                reason='Store excess solar + low price',
                expected_savings=charge_power * 0.01
            ))
        
        # Discharge during high price
        elif (state.grid.current_price > 0.038 and state.battery.soc > 30):
            discharge_power = min(
                state.total_load,
                state.battery.max_discharge_rate,
                (state.battery.soc - 30) / 100 * state.battery.capacity
            )
            
            actions.append(DRAction(
                timestamp=datetime.now(),
                action_type=DRActionType.BATTERY_DISCHARGE,
                target_power=discharge_power,
                reason='Peak price arbitrage',
                expected_savings=discharge_power * 0.008
            ))
        
        return actions
    
    def _boost_renewable_usage(self, state: MicrogridState) -> List[DRAction]:
        """Increase renewable penetration"""
        actions = []
        
        if state.generation.solar > 1.0:
            shiftable = [app for app in state.appliances.values()
                        if app.is_shiftable and app.status == 'off']
            
            if shiftable:
                actions.append(DRAction(
                    timestamp=datetime.now(),
                    action_type=DRActionType.LOAD_SHIFT,
                    target_appliance=shiftable[0].name,
                    scheduled_time=datetime.now(),
                    reason='Use available solar generation',
                    expected_savings=0.5
                ))
        
        return actions
    
    def _identify_savings(self, state: MicrogridState) -> List[DRAction]:
        """Identify cost-saving opportunities"""
        savings_potential = self.fusion.calculate_cost_savings_potential(state)
        actions = []
        
        if savings_potential['total_daily_potential'] > 1.0:
            actions.append(DRAction(
                timestamp=datetime.now(),
                action_type=DRActionType.LOAD_SHIFT,
                reason=f'${savings_potential["total_daily_potential"]:.2f} daily savings available',
                expected_savings=savings_potential['total_daily_potential']
            ))
        
        return actions
    
    def _get_peak_recommendations(self, state: MicrogridState) -> List[str]:
        """Get recommendations for peak period"""
        recommendations = []
        
        shiftable = [app for app in state.appliances.values() if app.is_shiftable]
        if shiftable:
            recommendations.append(f"Shift {len(shiftable)} flexible appliances to off-peak")
        
        if state.battery.soc < 80:
            recommendations.append("Charge battery now for peak discharge")
        
        recommendations.append("Reduce non-essential loads during peak")
        
        return recommendations
    
    def _calculate_peak_savings(self, state: MicrogridState, peak_value: float) -> float:
        """Calculate potential savings from avoiding peak"""
        current_price = state.grid.current_price
        price_diff = peak_value - current_price
        
        shiftable_power = sum(app.power for app in state.appliances.values() if app.is_shiftable)
        
        # Assume 3 hours of peak
        savings = shiftable_power * 3 * price_diff
        
        # Add battery arbitrage
        if state.battery.soc > 30:
            battery_energy = (state.battery.soc - 30) / 100 * state.battery.capacity
            savings += battery_energy * price_diff
        
        return savings