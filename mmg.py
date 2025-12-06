# Configuration data: [site_id, (gen1_tech, gen1_kw), (gen2_tech, gen2_kw), peak_load_kw, bess_kwh, bess_kw]
    # OPTIMIZED: Increased capacities by 50-100% to ensure adequate generation
configs = [
        (0, ("Solar", 600), ("Grid", 1000), 400, 1200, 600),     # Shengda: Academic campus
        (1, ("Wind", 1800), ("Solar", 500), 600, 1500, 750),     # Airport: High wind availability
        (2, ("Grid", 2000), ("Solar", 400), 1200, 800, 400),     # CBD: Dense commercial
        (3, ("Coal", 1000), ("Wind", 500), 800, 600, 300),       # Foxconn: Heavy industrial
        (4, ("Hydro", 600), ("Wind", 800), 200, 500, 250),       # Yellow River: Hydro + wind
        (5, ("Solar", 700), ("Biomass", 300), 150, 600, 300),    # Shaolin: Remote mountain
        (6, ("Grid", 1500), ("Solar", 600), 900, 1500, 750),     # East Station: Transport hub
        (7, ("Grid", 1200), ("Diesel", 400), 700, 500, 250),     # City Center: Urban dense
        (8, ("Biomass", 800), ("Coal", 600), 900, 600, 300),     # West Industrial: Manufacturing
        (9, ("Solar", 800), ("Wind", 200), 300, 1000, 500),      # Green Expo: Suburban park
        (10, ("Solar", 300), ("Grid", 400), 350, 500, 250),      # Longhu: Residential area
        (11, ("Solar", 700), ("Grid", 800), 800, 1200, 600),     # HighTech: Technology zone
        (12, ("Wind", 400), ("Diesel", 200), 150, 300, 150),     # Shangjie: General aviation
    ]

import time
import math
import random
import requests
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
from enum import Enum
import sqlite3
from pathlib import Path

# ==================== CONFIGURATION ====================
# IEC 61850 Communication Standards
SCADA_UPDATE_RATE = 3.0  # seconds (industry standard: 1-5s)
DATA_LOGGING_INTERVAL = 60  # seconds for persistent storage
ALARM_CHECK_INTERVAL = 1.0  # seconds

# Geographic Center - Zhengzhou Electric Power Bureau
GUJARAT_CENTER_LAT = 23.09
GUJARAT_CENTER_LON = 69.96
API_URL = "https://api.open-meteo.com/v1/forecast"

# IEEE 1547 Grid Interconnection Standards
GRID_VOLTAGE_NOMINAL = 400.0  # Volts (400V Three-phase)
GRID_FREQUENCY_NOMINAL = 50.0  # Hz (China Standard)
GRID_VOLTAGE_TOLERANCE = 0.1  # ±10%
GRID_FREQUENCY_TOLERANCE = 0.5  # ±0.5Hz

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler('microgrid_system.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('MicrogridDT')

# ==================== ENUMERATIONS ====================
class AlarmLevel(Enum):
    """IEC 61850 Alarm Classification"""
    NORMAL = 0
    WARNING = 1
    ALARM = 2
    CRITICAL = 3

class OperatingMode(Enum):
    """IEEE 1547 Operating Modes"""
    GRID_CONNECTED = "Grid-Connected"
    ISLANDED = "Islanded"
    TRANSITION = "Transition"
    MAINTENANCE = "Maintenance"

class AssetStatus(Enum):
    """Equipment Status per IEC 60812"""
    ONLINE = "Online"
    OFFLINE = "Offline"
    DEGRADED = "Degraded"
    MAINTENANCE = "Maintenance"
    FAULT = "Fault"

# ==================== DATA MODELS ====================

@dataclass
class IEC61850_MeasurementPoint:
    """IEC 61850-7-4 Logical Node Structure"""
    timestamp: float
    value: float
    quality: str  # "GOOD", "QUESTIONABLE", "BAD"
    unit: str
    source: str = ""
    
    def to_dict(self) -> Dict:
        return {
            'ts': self.timestamp,
            'val': round(self.value, 3),
            'q': self.quality,
            'unit': self.unit,
            'src': self.source
        }

@dataclass
class LocationProfile:
    """Enhanced Site Profile with GIS Data"""
    id: int
    name: str
    lat: float
    lon: float
    altitude: float  # meters ASL
    # Environmental bias factors (calibrated from historical data)
    wind_correction: float  # Multiplier based on terrain
    solar_correction: float  # Multiplier based on shading/orientation
    urban_density: float  # 0.0 (rural) to 1.0 (dense urban)
    grid_connection_capacity: float  # kW
    site_type: str  # "Industrial", "Commercial", "Residential", "Utility"
    
    def get_distance_km(self, other: 'LocationProfile') -> float:
        """Haversine formula for distance calculation"""
        R = 6371
        dlat = math.radians(other.lat - self.lat)
        dlon = math.radians(other.lon - self.lon)
        a = (math.sin(dlat/2)**2 + 
             math.cos(math.radians(self.lat)) * 
             math.cos(math.radians(other.lat)) * 
             math.sin(dlon/2)**2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

# ==================== REAL ZHENGZHOU SITES ====================
INDUSTRIAL_SITES = [
    LocationProfile(1, "Bhuj-SolarPark", 23.25, 69.67, 160, 1.40, 0.95, 0.08, 1800, "Utility"),
    LocationProfile(2, "Kutch-WindFarm", 23.55, 69.97, 155, 1.10, 1.35, 0.12, 2200, "Industrial"),
    
    LocationProfile(3, "Mundra-PortEZ", 22.84, 69.72, 100, 1.25, 0.90, 0.20, 1500, "Industrial"),
    LocationProfile(4, "Adani-SolarMFG", 22.82, 69.63, 95, 1.35, 0.85, 0.15, 1200, "Industrial"),
    
    LocationProfile(5, "Khavda-UltraSolar", 23.80, 69.60, 170, 1.50, 0.80, 0.05, 3000, "Utility"),
    LocationProfile(6, "Samakhiali-Hub", 23.31, 70.37, 110, 1.20, 0.95, 0.18, 1000, "Commercial"),
    
    LocationProfile(7, "Anjar-Industrial", 23.13, 70.03, 98, 1.18, 1.00, 0.22, 900, "Industrial"),
    LocationProfile(8, "Gandhidham-CBD", 23.07, 70.13, 105, 1.10, 0.85, 0.25, 800, "Commercial"),
    
    LocationProfile(9, "Bhachau-Res", 23.30, 70.22, 102, 1.05, 0.75, 0.28, 600, "Residential"),
    LocationProfile(10, "Rapar-Logistics", 23.57, 70.25, 108, 1.15, 0.90, 0.18, 700, "Commercial"),
    
    LocationProfile(11, "Jamnagar-OilSEZ", 22.47, 70.06, 115, 1.20, 0.70, 0.30, 2500, "Industrial"),
    LocationProfile(12, "Dwarka-CoastalWind", 22.25, 68.97, 120, 1.00, 1.30, 0.18, 1800, "Utility"),
    
    LocationProfile(13, "Morbi-CeramicHub", 22.82, 70.83, 98, 1.15, 0.85, 0.22, 1600, "Industrial"),
]

# ==================== WEATHER & SCADA LAYER ====================

class WeatherStation:
    """
    Real-time Meteorological Data Acquisition System
    Validates data quality per WMO standards
    """
    def __init__(self):
        self.current = {
            'temp_c': 20.0,
            'wind_ms': 3.0,
            'solar_wm2': 0.0,
            'humidity_pct': 50.0,
            'pressure_hpa': 1013.25,
            'timestamp': time.time()
        }
        self.history = deque(maxlen=2880)  # 24h at 30s intervals
        self.last_fetch = 0
        self.api_success = 0
        self.api_failure = 0
        self.quality_score = 100.0  # Data quality indicator
        
    def fetch_live_data(self) -> bool:
        """Fetch real-time weather from Open-Meteo API"""
        if time.time() - self.last_fetch < 30:
            return True
            
        try:
            params = {
                "latitude": GUJARAT_CENTER_LAT,
                "longitude": GUJARAT_CENTER_LON,
                "current": [
                    "temperature_2m",
                    "wind_speed_10m", 
                    "direct_radiation",
                    "relative_humidity_2m",
                    "surface_pressure"
                ]
            }
            
            response = requests.get(API_URL, params=params, timeout=5)
            response.raise_for_status()
            data = response.json().get('current', {})
            
            # Data Validation (WMO QC Standards)
            temp = data.get('temperature_2m', 20)
            wind = data.get('wind_speed_10m', 0) / 3.6  # km/h to m/s
            solar = data.get('direct_radiation', 0)
            
            # Sanity checks
            if not (-40 <= temp <= 50):
                logger.warning(f"Temperature out of range: {temp}°C")
                self.quality_score *= 0.95
                return False
                
            if not (0 <= wind <= 50):
                logger.warning(f"Wind speed anomaly: {wind} m/s")
                self.quality_score *= 0.95
                return False
            
            # Update current readings
            self.current = {
                'temp_c': temp,
                'wind_ms': wind,
                'solar_wm2': max(0, solar),
                'humidity_pct': data.get('relative_humidity_2m', 50),
                'pressure_hpa': data.get('surface_pressure', 1013.25),
                'timestamp': time.time()
            }
            
            self.history.append(self.current.copy())
            self.last_fetch = time.time()
            self.api_success += 1
            self.quality_score = min(100, self.quality_score + 0.1)
            
            logger.info(f"Weather update: {temp:.1f}°C, {wind:.1f}m/s, {solar:.0f}W/m²")
            return True
            
        except Exception as e:
            self.api_failure += 1
            self.quality_score *= 0.90
            logger.error(f"Weather API error: {str(e)}")
            return False
    
    def get_site_weather(self, site: LocationProfile) -> Dict:
        """
        Calculate micro-climate using meteorological models
        Implements:
        - Altitude lapse rate (temperature)
        - Urban heat island effect
        - Terrain wind correction
        - Solar angle and shading
        """
        now = datetime.now()
        hour = now.hour
        day_of_year = now.timetuple().tm_yday
        
        # Solar elevation angle (simplified)
        declination = 23.45 * math.sin(math.radians(360/365 * (day_of_year - 81)))
        hour_angle = 15 * (hour - 12)
        elevation = math.asin(
            math.sin(math.radians(site.lat)) * math.sin(math.radians(declination)) +
            math.cos(math.radians(site.lat)) * math.cos(math.radians(declination)) * 
            math.cos(math.radians(hour_angle))
        )
        solar_factor = max(0, math.sin(elevation))
        
        # Temperature: Altitude lapse rate + Urban heat island
        temp_lapse = -0.0065 * site.altitude  # -6.5°C per 1000m
        urban_heat = site.urban_density * 2.5  # Up to +2.5°C in dense urban
        local_temp = self.current['temp_c'] + temp_lapse + urban_heat
        
        # Wind: Terrain correction + altitude enhancement
        wind_altitude_factor = 1 + (site.altitude / 1000) * 0.15
        local_wind = (self.current['wind_ms'] * 
                     site.wind_correction * 
                     wind_altitude_factor *
                     random.uniform(0.85, 1.15))
        
        # Solar: Correction factors + time-of-day
        local_solar = (self.current['solar_wm2'] * 
                      site.solar_correction * 
                      solar_factor *
                      random.uniform(0.90, 1.05))
        
        return {
            'temperature_c': local_temp,
            'wind_speed_ms': max(0, local_wind),
            'solar_irradiance_wm2': max(0, local_solar),
            'humidity_pct': self.current['humidity_pct'],
            'pressure_hpa': self.current['pressure_hpa'],
            'timestamp': time.time(),
            'quality': 'GOOD' if self.quality_score > 80 else 'QUESTIONABLE'
        }

# ==================== POWER GENERATION ASSETS ====================

class DERAsset:
    """
    Distributed Energy Resource (IEEE 1547-2018 Compliant)
    
    Implements real power generation characteristics:
    - Manufacturer power curves
    - Temperature derating
    - Efficiency degradation
    - Maintenance scheduling
    """
    def __init__(self, asset_id: str, technology: str, rated_capacity_kw: float,
                 install_date: datetime, manufacturer: str = "Generic"):
        self.id = asset_id
        self.technology = technology
        self.rated_capacity = rated_capacity_kw
        self.install_date = install_date
        self.manufacturer = manufacturer
        self.status = AssetStatus.ONLINE
        
        # Performance tracking
        self.operating_hours = 0.0
        self.energy_produced_kwh = 0.0
        self.starts = 0
        self.trips = 0
        
        # Degradation model (industry validated)
        self.degradation_rate = self._get_degradation_rate()
        self.current_efficiency = 1.0
        
        # Maintenance
        self.next_maintenance_hours = self._get_maintenance_interval()
        self.maintenance_history = []
        
    def _get_degradation_rate(self) -> float:
        """Annual degradation rates from REAL FIELD DATA (not lab conditions)"""
        rates = {
            "Solar": 0.008,      # 0.8% per year (real-world with dust, soiling)
            "Wind": 0.015,       # 1.5% per year (mechanical wear)
            "Battery": 0.025,    # 2.5% per year (calendar + cycle aging)
            "Diesel": 0.012,     # 1.2% per year (engine wear)
            "Gas": 0.008,        # 0.8% per year
            "Biomass": 0.015,    # 1.5% per year (corrosion, fuel quality)
            "Coal": 0.010,       # 1.0% per year (boiler scaling)
            "Hydro": 0.005,      # 0.5% per year (most reliable)
            "Grid": 0.0          # No degradation
        }
        return rates.get(self.technology, 0.015)
    
    def _get_maintenance_interval(self) -> float:
        """Hours between scheduled maintenance"""
        intervals = {
            "Solar": 8760,       # Annual
            "Wind": 4380,        # Semi-annual
            "Battery": 2190,     # Quarterly
            "Diesel": 500,       # 500 operating hours
            "Gas": 8760,
            "Biomass": 4380,
            "Coal": 8760,
            "Hydro": 8760,
            "Grid": float('inf')
        }
        return intervals.get(self.technology, 8760)
    
    def calculate_output(self, weather: Dict, grid_demand_kw: float = 0) -> Tuple[float, Dict]:
        """
        Calculate real-time power output with physics-based models
        Returns: (power_kw, telemetry_dict)
        """
        if self.status in [AssetStatus.OFFLINE, AssetStatus.FAULT]:
            return 0.0, {'status': self.status.value, 'output_kw': 0}
        
        base_output = 0.0
        telemetry = {}
        
        # ===== SOLAR PV GENERATION (REAL WORLD) =====
        if self.technology == "Solar":
            # Sandia PV Performance Model (simplified)
            G = weather['solar_irradiance_wm2']  # W/m²
            T_cell = weather['temperature_c'] + (G / 800) * 30  # Cell temp estimate
            
            # Temperature coefficient (realistic: -0.45%/°C for standard Si panels)
            temp_coeff = 1 - 0.0045 * (T_cell - 25)
            
            # Efficiency at different irradiance levels (REAL BEHAVIOR)
            if G < 50:  # Very low light - almost nothing
                irr_efficiency = 0.0
            elif G < 150:  # Dawn/dusk - poor efficiency
                irr_efficiency = 0.70
            elif G < 400:  # Cloudy - reduced
                irr_efficiency = 0.85
            elif G < 800:  # Partly cloudy
                irr_efficiency = 0.93
            else:  # Full sun
                irr_efficiency = 0.96
            
            # Soiling losses (REAL: dust, bird droppings, pollution)
            soiling_factor = 0.92  # 8% loss is realistic without cleaning
            
            # Mismatch losses (string mismatch, shading)
            mismatch_factor = 0.97  # 3% loss
            
            # DC to AC conversion (inverter)
            inverter_efficiency = 0.96  # 4% inverter loss
            
            base_output = (G / 1000) * self.rated_capacity * temp_coeff * irr_efficiency * soiling_factor * mismatch_factor * inverter_efficiency
            
            telemetry = {
                'irradiance_wm2': G,
                'cell_temp_c': T_cell,
                'temp_coeff': temp_coeff,
                'irr_efficiency': irr_efficiency,
                'soiling_loss_pct': (1-soiling_factor)*100
            }
        
        # ===== WIND TURBINE (REAL WORLD) =====
        elif self.technology == "Wind":
            # IEC 61400-12-1 Power Curve
            v = weather['wind_speed_ms']
            
            # Air density correction (REAL PHYSICS)
            rho = (weather['pressure_hpa'] * 100) / (287.05 * (weather['temperature_c'] + 273.15))
            rho_std = 1.225  # kg/m³ at sea level, 15°C
            density_ratio = rho / rho_std
            
            # REALISTIC wind turbine power curve
            v_cut_in = 3.5   # m/s (real turbines: 3-4 m/s)
            v_rated = 11.0   # m/s (real turbines: 10-12 m/s)
            v_cut_out = 22.0 # m/s (real turbines: 20-25 m/s, safety)
            
            if v < v_cut_in or v > v_cut_out:
                power_coeff = 0
            elif v < v_rated:
                # Cubic region (realistic curve)
                power_coeff = (v / v_rated) ** 2.8  # Slightly less than cubic due to losses
            else:
                # Rated power region with slight dropoff at high winds
                power_coeff = 0.98  # 2% loss at rated power (gearbox, generator heat)
            
            # Turbulence losses (REAL: 2-5% depending on site)
            turbulence_factor = 0.97
            
            # Availability factor (REAL: maintenance, downtime)
            availability = 0.95  # 95% uptime (5% downtime is realistic)
            
            base_output = self.rated_capacity * power_coeff * density_ratio * turbulence_factor * availability
            
            telemetry = {
                'wind_speed_ms': v,
                'air_density_kgm3': rho,
                'density_ratio': density_ratio,
                'power_coefficient': power_coeff,
                'availability_pct': availability * 100
            }
        
        # ===== DIESEL/GAS GENERATOR (REAL WORLD) =====
        elif self.technology in ["Diesel", "Gas"]:
            # Dispatchable - follows load BUT with real constraints
            
            # Minimum load requirement (REAL: generators don't like running below 30%)
            min_load = self.rated_capacity * 0.30
            max_output = self.rated_capacity * 0.90  # 90% max continuous (not 95%)
            
            if grid_demand_kw < min_load:
                # Run at minimum load (wet stacking prevention)
                base_output = min_load
            else:
                base_output = min(grid_demand_kw, max_output)
            
            # Efficiency varies with load (REAL: best at 70-80% load)
            load_factor = base_output / self.rated_capacity
            if load_factor < 0.4:
                fuel_efficiency = 0.75  # Poor efficiency at low load
            elif load_factor < 0.7:
                fuel_efficiency = 0.88
            else:
                fuel_efficiency = 0.92  # Best efficiency at high load
            
            # Fuel consumption (REAL: diesel ~0.28 L/kWh, gas better)
            fuel_rate_lh = base_output * (0.28 if self.technology == "Diesel" else 0.22) / fuel_efficiency
            
            # Warm-up time simulation (REAL: generators need 30 seconds to ramp)
            if self.operating_hours < 0.01:  # First start
                base_output *= 0.5  # Half power during warm-up
            
            telemetry = {
                'output_kw': base_output,
                'load_factor_pct': load_factor * 100,
                'fuel_efficiency': fuel_efficiency,
                'fuel_consumption_lh': fuel_rate_lh,
                'runtime_hours': self.operating_hours
            }
        
       # ===== BIOMASS/COAL (Thermal - REAL WORLD) =====
        elif self.technology in ["Biomass", "Coal"]:
            # Baseload with REAL thermal inefficiencies and variability
            thermal_efficiency = 0.32 if self.technology == "Biomass" else 0.36  # Real: 32-36%
            
            # NIGHTTIME PEAK OPERATION for Coal plants
            hour = datetime.now().hour
            is_night = hour >= 20 or hour <= 6  # 8pm - 6am
            
            if self.technology == "Coal" and is_night:
                # Ramp up to maximum output during night (cheap electricity period)
                load_factor = random.uniform(0.85, 0.95)  # 85-95% capacity at night
            else:
                # Normal operation - follow local demand
                if grid_demand_kw > 0:
                    load_factor = min(0.80, grid_demand_kw / self.rated_capacity)
                else:
                    load_factor = 0.50  # Minimum stable operation
            
            target_output = self.rated_capacity * load_factor
            
            # Fuel quality variation (REAL: moisture content, heating value varies)
            fuel_quality = random.uniform(0.88, 0.98)
            
            # Boiler efficiency losses (REAL: stack losses, blowdown)
            boiler_efficiency = 0.93
            
            # Auxiliary load (REAL: fans, pumps, controls consume power)
            auxiliary_load_pct = 0.08  # 8% parasitic load
            
            base_output = target_output * thermal_efficiency * fuel_quality * boiler_efficiency * (1 - auxiliary_load_pct)
            
            # Random variations (REAL: combustion instability)
            base_output *= random.uniform(0.92, 1.00)
            
            telemetry = {
                'thermal_efficiency': thermal_efficiency,
                'fuel_quality': fuel_quality,
                'output_kw': base_output,
                'load_factor_pct': load_factor * 100,
                'night_peak_mode': is_night if self.technology == "Coal" else False,
                'auxiliary_load_pct': auxiliary_load_pct * 100
            }
        
        # ===== HYDRO (REAL WORLD) =====
        elif self.technology == "Hydro":
            # Seasonal and flow-dependent (REAL CONDITIONS)
            month = datetime.now().month
            hour = datetime.now().hour
            
            # Seasonal water availability (REAL: based on rainfall patterns)
            if 6 <= month <= 9:  # Rainy season (June-September)
                seasonal_factor = random.uniform(0.85, 0.98)
            elif month in [4, 5, 10]:  # Transition seasons
                seasonal_factor = random.uniform(0.60, 0.75)
            else:  # Dry season (Nov-Mar)
                seasonal_factor = random.uniform(0.40, 0.60)
            
            # Time-of-day pattern (REAL: reservoir management)
            if 8 <= hour <= 22:  # Peak hours - maximize output
                time_factor = 1.0
            else:  # Off-peak - conserve water
                time_factor = 0.7
            
            # Turbine efficiency (REAL: best at 80-95% of rated flow)
            turbine_efficiency = 0.88  # Modern Francis turbine: 85-90%
            
            # Head variation (REAL: reservoir level affects output)
            head_factor = random.uniform(0.92, 1.00)
            
            base_output = self.rated_capacity * seasonal_factor * time_factor * turbine_efficiency * head_factor
            
            telemetry = {
                'seasonal_factor': seasonal_factor,
                'water_availability_pct': seasonal_factor * 100,
                'turbine_efficiency': turbine_efficiency,
                'output_kw': base_output
            }
        
        # ===== GRID CONNECTION (REAL WORLD) =====
        elif self.technology == "Grid":
            # Infinite capacity BUT with real constraints
            
            # Grid availability (REAL: 99.9% uptime, occasional outages)
            if random.random() < 0.001:  # 0.1% chance of grid failure
                base_output = 0
                self.status = AssetStatus.FAULT
                logger.error(f"{self.id} - Grid outage detected!")
            else:
                # Power factor correction losses (REAL: 1-2%)
                pf_correction = 0.98
                
                # Transformer losses (REAL: 1.5-2.5%)
                transformer_efficiency = 0.975
                
                # Available capacity after losses
                available = self.rated_capacity * pf_correction * transformer_efficiency
                base_output = min(grid_demand_kw, available)
            
            telemetry = {
                'available_capacity_kw': self.rated_capacity,
                'transformer_efficiency': 0.975 if self.status == AssetStatus.ONLINE else 0
            }
        
        # Apply degradation and efficiency losses
        actual_output = base_output * self.current_efficiency
        
        # Update operating hours
        if actual_output > 0.05 * self.rated_capacity:
            self.operating_hours += SCADA_UPDATE_RATE / 3600
            self.energy_produced_kwh += actual_output * (SCADA_UPDATE_RATE / 3600)
        
        # Check maintenance needs
        if self.operating_hours >= self.next_maintenance_hours:
            self.status = AssetStatus.MAINTENANCE
            logger.warning(f"{self.id} requires scheduled maintenance")
        
        # Update degradation
        years_operating = self.operating_hours / 8760
        self.current_efficiency = 1.0 - (self.degradation_rate * years_operating)
        self.current_efficiency = max(0.7, self.current_efficiency)  # Min 70% efficiency
        
        telemetry.update({
            'technology': self.technology,
            'rated_capacity_kw': self.rated_capacity,
            'actual_output_kw': actual_output,
            'efficiency': self.current_efficiency,
            'status': self.status.value,
            'operating_hours': self.operating_hours
        })
        
        return actual_output, telemetry

# ==================== ENERGY STORAGE SYSTEM ====================

class BatteryEnergyStorageSystem:
    """
    BESS - IEC 62933 Compliant
    
    Models:
    - Li-ion chemistry degradation (calendar + cycle aging)
    - Thermal management
    - State-of-Health estimation
    - C-rate limitations
    """
    def __init__(self, bess_id: str, capacity_kwh: float, power_kw: float, 
                 chemistry: str = "Li-NMC"):
        self.id = bess_id
        self.rated_capacity_kwh = capacity_kwh
        self.rated_power_kw = power_kw
        self.chemistry = chemistry
        
        # State variables
        self.soc_pct = 50.0  # State of Charge
        self.soh_pct = 100.0  # State of Health
        self.temperature_c = 25.0
        self.cycle_count = 0.0
        self.throughput_kwh = 0.0
        
        # Performance parameters (Li-NMC typical - REAL WORLD VALUES)
        self.round_trip_efficiency = 0.90  # Realistic: 90% (not 92%)
        self.max_soc = 90.0  # REAL LIMIT: Don't charge above 90% (warranty protection)
        self.min_soc = 20.0  # REAL LIMIT: Don't discharge below 20% (longevity)
        self.max_charge_rate_c = 0.5  # 0.5C charging (manufacturer limit)
        self.max_discharge_rate_c = 0.8  # 0.8C discharging (realistic, not 1C)
        
        # Degradation tracking
        self.calendar_aging_per_day = 0.02 / 365  # 2% per year
        self.cycle_aging_factor = 0.0001  # Per equivalent full cycle
        
        self.status = AssetStatus.ONLINE
        
    def calculate_max_charge_power(self) -> float:
        """Maximum charge power considering SOC and SOH"""
        if self.soc_pct >= self.max_soc:
            return 0.0
        
        # C-rate limit
        max_power = self.rated_capacity_kwh * self.max_charge_rate_c
        
        # Reduce power near max SOC (CC-CV charging)
        if self.soc_pct > 85:
            taper_factor = (self.max_soc - self.soc_pct) / (self.max_soc - 85)
            max_power *= max(0.1, taper_factor)  # Minimum 10% power even near full
        
        # Health-based derating
        max_power *= (self.soh_pct / 100)
        
        return min(max_power, self.rated_power_kw)
    
    def calculate_max_discharge_power(self) -> float:
        """Maximum discharge power considering SOC and SOH"""
        if self.soc_pct <= self.min_soc:
            return 0.0
        
        # C-rate limit
        max_power = self.rated_capacity_kwh * self.max_discharge_rate_c
        
        # Voltage sag near min SOC
        if self.soc_pct < 20:
            derating = max(0.1, (self.soc_pct - self.min_soc) / (20 - self.min_soc))
            max_power *= derating
        
        # Health-based derating
        max_power *= (self.soh_pct / 100)
        
        return min(max_power, self.rated_power_kw)
    
    def charge(self, power_kw: float, duration_hours: float) -> Tuple[float, float]:
        """
        Attempt to charge battery
        Returns: (actual_power_accepted_kw, energy_consumed_from_source_kwh)
        """
        max_power = self.calculate_max_charge_power()
        actual_power = min(power_kw, max_power)
        
        if actual_power <= 0.01:  # Below 10W is negligible
            return 0.0, 0.0
        
        # Energy that will be consumed from source (before efficiency losses)
        energy_from_source = actual_power * duration_hours
        
        # Energy actually stored in battery (after efficiency losses)
        energy_to_battery = energy_from_source * self.round_trip_efficiency
        
        # Check capacity limits
        capacity_now = self.rated_capacity_kwh * (self.soh_pct / 100)
        available_capacity = (self.max_soc - self.soc_pct) / 100 * capacity_now
        energy_stored = min(energy_to_battery, available_capacity)
        
        # Recalculate actual power if capacity limited
        if energy_stored < energy_to_battery:
            actual_power = energy_stored / (duration_hours * self.round_trip_efficiency)
            energy_from_source = actual_power * duration_hours
        
        # Update SOC
        self.soc_pct += (energy_stored / capacity_now) * 100
        self.soc_pct = min(self.soc_pct, self.max_soc)
        
        # Track throughput
        self.throughput_kwh += energy_stored
        self.cycle_count += energy_stored / (self.rated_capacity_kwh * 2)
        
        # Thermal model (simplified)
        self.temperature_c += actual_power / self.rated_capacity_kwh * 0.5
        self.temperature_c = min(45, self.temperature_c)
        
        return actual_power, energy_from_source
    
    def discharge(self, power_kw: float, duration_hours: float) -> Tuple[float, float]:
        """
        Attempt to discharge battery
        Returns: (actual_power_delivered_kw, energy_to_load_kwh)
        """
        max_power = self.calculate_max_discharge_power()
        actual_power = min(power_kw, max_power)
        
        if actual_power <= 0.01:  # Below 10W is negligible
            return 0.0, 0.0
        
        # Energy needed to deliver to load (after efficiency losses)
        energy_to_load = actual_power * duration_hours
        
        # Energy that must come from battery (before efficiency losses)
        energy_from_battery = energy_to_load / self.round_trip_efficiency
        
        # Check available energy
        capacity_now = self.rated_capacity_kwh * (self.soh_pct / 100)
        available_energy = (self.soc_pct - self.min_soc) / 100 * capacity_now
        energy_discharged = min(energy_from_battery, available_energy)
        
        # Recalculate actual power if energy limited
        if energy_discharged < energy_from_battery:
            actual_power = energy_discharged * self.round_trip_efficiency / duration_hours
            energy_to_load = actual_power * duration_hours
        
        # Update SOC
        self.soc_pct -= (energy_discharged / capacity_now) * 100
        self.soc_pct = max(self.soc_pct, self.min_soc)
        
        # Track throughput
        self.throughput_kwh += energy_discharged
        self.cycle_count += energy_discharged / (self.rated_capacity_kwh * 2)
        
        # Thermal
        self.temperature_c += actual_power / self.rated_capacity_kwh * 0.7
        self.temperature_c = min(45, self.temperature_c)
        
        return actual_power, energy_to_load
    
    def update_aging(self, dt_hours: float):
        """Update State of Health based on aging models"""
        # Calendar aging
        calendar_loss = self.calendar_aging_per_day * (dt_hours / 24)
        
        # Cycle aging (from NREL/BNEF models)
        cycle_loss = self.cycle_aging_factor * (self.cycle_count)
        
        self.soh_pct = 100 - (calendar_loss + cycle_loss) * 100
        self.soh_pct = max(70, self.soh_pct)  # End of life at 70% (industry standard)
        
        # Thermal decay (simplified)
        self.temperature_c -= dt_hours * 2  # Cooling
        self.temperature_c = max(25, self.temperature_c)
        
        if self.soh_pct < 80:
            self.status = AssetStatus.DEGRADED
            logger.warning(f"BESS {self.id} SOH below 80%: {self.soh_pct:.1f}%")

# ==================== MICROGRID CONTROLLER ====================

class MicrogridController:
    """
    IEEE 1547-2018 Compliant Microgrid Controller
    
    Functions:
    - Energy Management System (EMS)
    - Load balancing
    - Grid synchronization
    - Islanding detection
    - Economic dispatch
    """
    def __init__(self, mg_id: int, site: LocationProfile):
        self.id = mg_id
        self.site = site
        self.operating_mode = OperatingMode.GRID_CONNECTED
        
        # Assets
        self.generators: List[DERAsset] = []
        self.battery: Optional[BatteryEnergyStorageSystem] = None
        
        # Load profile
        self.peak_load_kw = 0
        self.current_load_kw = 0
        
        # Grid interface
        self.grid_voltage_v = GRID_VOLTAGE_NOMINAL
        self.grid_frequency_hz = GRID_FREQUENCY_NOMINAL
        self.grid_available = True
        
        # Telemetry
        self.metrics = {}
        self.alarms = []
        self.history = deque(maxlen=2880)  # 24h history
        
        # Economics
        self.grid_import_kwh = 0.0
        self.grid_export_kwh = 0.0
        self.grid_import_cost = 0.0
        self.grid_export_revenue = 0.0
        
        # Grid tariff (REAL time-of-use pricing - China CNY/kWh)
        self.tariff_peak = 1.35      # Peak: 8am-10pm (¥1.35/kWh)
        self.tariff_offpeak = 0.52   # Off-peak: 10pm-8am (¥0.52/kWh)
        self.export_price = 0.38     # Feed-in tariff (¥0.38/kWh) - realistic for China
        
    def add_generator(self, technology: str, capacity_kw: float, manufacturer: str = "Generic"):
        """Add a DER asset to the microgrid"""
        asset_id = f"MG{self.id:02d}_{technology[:3].upper()}_{len(self.generators)+1}"
        asset = DERAsset(
            asset_id=asset_id,
            technology=technology,
            rated_capacity_kw=capacity_kw,
            install_date=datetime.now(),
            manufacturer=manufacturer
        )
        self.generators.append(asset)
        logger.info(f"Added {technology} generator ({capacity_kw}kW) to MG-{self.id}")
        
    def add_battery(self, capacity_kwh: float, power_kw: float):
        """Add BESS to the microgrid"""
        bess_id = f"MG{self.id:02d}_BESS"
        self.battery = BatteryEnergyStorageSystem(bess_id, capacity_kwh, power_kw)
        logger.info(f"Added BESS ({capacity_kwh}kWh, {power_kw}kW) to MG-{self.id}")
        
    def set_load_profile(self, peak_kw: float):
        """Define load characteristics"""
        self.peak_load_kw = peak_kw
        
    def calculate_current_load(self) -> float:
        """Model REALISTIC load profile with time-of-day and site type patterns"""
        hour = datetime.now().hour
        minute = datetime.now().minute
        weekday = datetime.now().weekday() < 5  # Mon-Fri
        
        # Base load factor by site type (REAL PATTERNS)
        if self.site.site_type == "Industrial":
            if weekday and 6 <= hour <= 22:
                # Factory hours: 3-shift operation with lunch dip
                if hour in [12, 13]:  # Lunch break
                    load_factor = random.uniform(0.60, 0.70)
                elif hour in [6, 7, 22]:  # Shift changes
                    load_factor = random.uniform(0.65, 0.75)
                else:
                    load_factor = random.uniform(0.80, 0.95)
            elif weekday:  # Night maintenance
                load_factor = random.uniform(0.35, 0.50)
            else:  # Weekend: reduced operations
                load_factor = random.uniform(0.25, 0.45)
                
        elif self.site.site_type == "Commercial":
            if weekday and 8 <= hour <= 20:
                # Business hours with peak at midday
                if 11 <= hour <= 14:  # Lunch rush
                    load_factor = random.uniform(0.85, 0.98)
                elif hour in [8, 9, 19, 20]:  # Opening/closing
                    load_factor = random.uniform(0.60, 0.75)
                else:
                    load_factor = random.uniform(0.70, 0.88)
            elif not weekday and 10 <= hour <= 18:  # Weekend shopping
                load_factor = random.uniform(0.55, 0.75)
            else:  # Closed hours
                load_factor = random.uniform(0.15, 0.30)  # Security, HVAC
                
        elif self.site.site_type == "Residential":
            if hour in [7, 8]:  # Morning peak
                load_factor = random.uniform(0.70, 0.85)
            elif hour in [18, 19, 20, 21]:  # Evening peak (cooking, TV)
                load_factor = random.uniform(0.75, 0.95)
            elif 9 <= hour <= 17:  # Daytime (people at work)
                load_factor = random.uniform(0.30, 0.45)
            elif 22 <= hour or hour <= 5:  # Night
                load_factor = random.uniform(0.20, 0.35)
            else:
                load_factor = random.uniform(0.35, 0.55)
                
        else:  # Utility
            # Relatively constant with small variation
            load_factor = random.uniform(0.65, 0.80)
        
        # Add small random noise (REAL: sudden loads, equipment cycling)
        noise = random.uniform(-0.03, 0.03)
        load_factor = max(0.1, min(1.0, load_factor + noise))
        
        self.current_load_kw = self.peak_load_kw * load_factor
        return self.current_load_kw
    
    def check_grid_quality(self) -> bool:
        """IEEE 1547 Grid quality checks with REAL grid conditions"""
        # Simulate voltage and frequency (REAL: small variations always present)
        
        # Normal operation: ±3% voltage variation
        self.grid_voltage_v = GRID_VOLTAGE_NOMINAL * random.uniform(0.97, 1.03)
        
        # Normal operation: ±0.1 Hz frequency variation
        self.grid_frequency_hz = GRID_FREQUENCY_NOMINAL + random.uniform(-0.1, 0.1)
        
        # REAL: Occasional grid disturbances (0.5% chance)
        if random.random() < 0.005:
            # Grid disturbance event
            disturbance_type = random.choice(['voltage_sag', 'voltage_swell', 'frequency_deviation'])
            
            if disturbance_type == 'voltage_sag':
                self.grid_voltage_v = GRID_VOLTAGE_NOMINAL * random.uniform(0.85, 0.92)
                logger.warning(f"MG-{self.id}: Voltage sag detected - {self.grid_voltage_v:.1f}V")
            elif disturbance_type == 'voltage_swell':
                self.grid_voltage_v = GRID_VOLTAGE_NOMINAL * random.uniform(1.08, 1.12)
                logger.warning(f"MG-{self.id}: Voltage swell detected - {self.grid_voltage_v:.1f}V")
            else:  # frequency_deviation
                self.grid_frequency_hz = GRID_FREQUENCY_NOMINAL + random.uniform(-0.4, 0.4)
                logger.warning(f"MG-{self.id}: Frequency deviation - {self.grid_frequency_hz:.2f}Hz")
        
        # IEEE 1547 compliance checks
        voltage_ok = abs(self.grid_voltage_v - GRID_VOLTAGE_NOMINAL) / GRID_VOLTAGE_NOMINAL <= GRID_VOLTAGE_TOLERANCE
        frequency_ok = abs(self.grid_frequency_hz - GRID_FREQUENCY_NOMINAL) <= GRID_FREQUENCY_TOLERANCE
        
        self.grid_available = voltage_ok and frequency_ok
        
        if not self.grid_available:
            self.add_alarm(AlarmLevel.ALARM, 
                          f"Grid out of spec - V={self.grid_voltage_v:.1f}V, f={self.grid_frequency_hz:.2f}Hz")
            # REAL: Switch to islanded mode after 2 seconds (anti-islanding protection)
            self.operating_mode = OperatingMode.TRANSITION
            
        return self.grid_available
    
    def execute_control_cycle(self, weather: Dict, dt_seconds: float = SCADA_UPDATE_RATE):
        """
        Main EMS control loop - Economic dispatch with constraints
        """
        dt_hours = dt_seconds / 3600
        
        # 1. Update load
        load_kw = self.calculate_current_load()
        
        # 2. Check grid status
        grid_ok = self.check_grid_quality()
        
        # 3. Calculate generation from all DERs
        total_generation_kw = 0
        gen_telemetry = []
        
        for generator in self.generators:
            output_kw, telemetry = generator.calculate_output(weather, load_kw)
            total_generation_kw += output_kw
            gen_telemetry.append({
                'id': generator.id,
                'tech': generator.technology,
                'output_kw': output_kw,
                'status': generator.status.value,
                'efficiency': generator.current_efficiency
            })
        
        # 4. Energy balance
        net_power_kw = total_generation_kw - load_kw
        
        # 5. Battery management strategy with proper grid spillover
        battery_power_kw = 0
        battery_action = "Idle"
        battery_limited = False
        
        if self.battery:
            self.battery.update_aging(dt_hours)
            
            if net_power_kw > 10:  # Surplus > 10kW
                # Try to charge battery
                actual_charge_kw, energy_consumed_kwh = self.battery.charge(net_power_kw, dt_hours)
                battery_power_kw = actual_charge_kw
                
                if actual_charge_kw > 1.0:  # Meaningful charging (>1kW)
                    battery_action = "Charging"
                    net_power_kw -= actual_charge_kw  # Subtract what battery actually took
                    
                    # Check if battery couldn't take all the power
                    if actual_charge_kw < net_power_kw * 0.9:  # Battery took less than 90%
                        battery_limited = True
                else:
                    # Battery can't charge (full or limited) - all goes to grid
                    battery_action = "Full"
                    battery_limited = True
                
            elif net_power_kw < -10:  # Deficit > 10kW
                # Try to discharge battery
                actual_discharge_kw, energy_delivered_kwh = self.battery.discharge(abs(net_power_kw), dt_hours)
                battery_power_kw = -actual_discharge_kw
                
                if actual_discharge_kw > 1.0:  # Meaningful discharging (>1kW)
                    battery_action = "Discharging"
                    net_power_kw += actual_discharge_kw  # Add what battery actually provided
                    
                    # Check if battery couldn't provide all the power
                    if actual_discharge_kw < abs(net_power_kw) * 0.9:  # Battery gave less than 90%
                        battery_limited = True
                else:
                    # Battery can't discharge (empty or limited) - get all from grid
                    battery_action = "Empty"
                    battery_limited = True
        
        # 6. Grid exchange (FIXED - always account for grid)
        grid_exchange_kw = net_power_kw
        
        if grid_ok:
            if net_power_kw > 0:  # Export to grid
                self.grid_export_kwh += net_power_kw * dt_hours
                self.grid_export_revenue += net_power_kw * dt_hours * self.export_price
            elif net_power_kw < 0:  # Import from grid
                self.grid_import_kwh += abs(net_power_kw) * dt_hours
                tariff = self.tariff_peak if 8 <= datetime.now().hour <= 22 else self.tariff_offpeak
                self.grid_import_cost += abs(net_power_kw) * dt_hours * tariff
            # If net_power_kw == 0, no grid exchange needed
        else:
            # Islanded mode - load shedding if necessary
            if net_power_kw < -10:  # More than 10kW deficit
                actual_load_shed = abs(net_power_kw)
                self.add_alarm(AlarmLevel.CRITICAL, 
                              f"Islanded with {actual_load_shed:.1f}kW deficit - LOAD SHEDDING ACTIVE")
                self.operating_mode = OperatingMode.ISLANDED
                grid_exchange_kw = 0  # No grid available
            else:
                grid_exchange_kw = 0
        
        # 7. Compile metrics (ADDED battery_limited flag)
        self.metrics = {
            'timestamp': time.time(),
            'datetime': datetime.now().isoformat(),
            'load_kw': load_kw,
            'generation_kw': total_generation_kw,
            'battery_power_kw': battery_power_kw,
            'battery_soc_pct': self.battery.soc_pct if self.battery else 0,
            'battery_soh_pct': self.battery.soh_pct if self.battery else 0,
            'battery_limited': battery_limited,  # NEW: indicates battery at limits
            'grid_exchange_kw': grid_exchange_kw,
            'grid_voltage_v': self.grid_voltage_v,
            'grid_frequency_hz': self.grid_frequency_hz,
            'operating_mode': self.operating_mode.value,
            'weather': weather,
            'generators': gen_telemetry,
            'battery_action': battery_action,
            'total_cost_cny': self.grid_import_cost - self.grid_export_revenue
        }
        
        self.history.append(self.metrics.copy())
        
    def add_alarm(self, level: AlarmLevel, message: str):
        """Add alarm to queue"""
        alarm = {
            'timestamp': datetime.now().isoformat(),
            'level': level.name,
            'microgrid': f"MG-{self.id}",
            'site': self.site.name,
            'message': message
        }
        self.alarms.append(alarm)
        if level in [AlarmLevel.ALARM, AlarmLevel.CRITICAL]:
            logger.error(f"ALARM [{level.name}] MG-{self.id}: {message}")
        else:
            logger.warning(f"WARNING MG-{self.id}: {message}")
    
    def get_summary(self) -> Dict:
        """Return operational summary for SCADA display"""
        return {
            'id': self.id,
            'site': self.site.name,
            'type': self.site.site_type,
            'mode': self.operating_mode.value,
            'load_kw': self.current_load_kw,
            'generation_kw': self.metrics.get('generation_kw', 0),
            'grid_kw': self.metrics.get('grid_exchange_kw', 0),
            'soc_pct': self.metrics.get('battery_soc_pct', 0),
            'alarms': len([a for a in self.alarms if a['level'] in ['ALARM', 'CRITICAL']]),
            'cost_cny': self.metrics.get('total_cost_cny', 0)
        }

# ==================== DATA HISTORIAN ====================

class DataHistorian:
    """
    Time-series database for long-term data storage
    IEC 61850 compliant data logging
    """
    def __init__(self, db_path: str = "microgrid_data.db"):
        self.db_path = db_path
        self.conn = None
        self._init_database()
        
    def _init_database(self):
        """Initialize SQLite database schema"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()
        
        # Telemetry table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                microgrid_id INTEGER,
                site_name TEXT,
                load_kw REAL,
                generation_kw REAL,
                grid_exchange_kw REAL,
                battery_soc_pct REAL,
                battery_soh_pct REAL,
                weather_temp_c REAL,
                weather_wind_ms REAL,
                weather_solar_wm2 REAL,
                operating_mode TEXT,
                grid_cost_cny REAL
            )
        """)
        
        # Events/Alarms table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alarms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                microgrid_id INTEGER,
                level TEXT,
                message TEXT
            )
        """)
        
        # Asset performance table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS asset_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                asset_id TEXT,
                technology TEXT,
                output_kw REAL,
                efficiency REAL,
                operating_hours REAL,
                status TEXT
            )
        """)
        
        self.conn.commit()
        logger.info(f"Data historian initialized: {self.db_path}")
        
    def log_telemetry(self, microgrid: MicrogridController):
        """Store microgrid telemetry"""
        if not microgrid.metrics:
            return
            
        cursor = self.conn.cursor()
        m = microgrid.metrics
        w = m.get('weather', {})
        
        cursor.execute("""
            INSERT INTO telemetry VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m['timestamp'],
            microgrid.id,
            microgrid.site.name,
            m['load_kw'],
            m['generation_kw'],
            m['grid_exchange_kw'],
            m['battery_soc_pct'],
            m['battery_soh_pct'],
            w.get('temperature_c', 0),
            w.get('wind_speed_ms', 0),
            w.get('solar_irradiance_wm2', 0),
            m['operating_mode'],
            m['total_cost_cny']
        ))
        
        # Log asset performance
        for gen_data in m.get('generators', []):
            cursor.execute("""
                INSERT INTO asset_performance VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)
            """, (
                m['timestamp'],
                gen_data['id'],
                gen_data['tech'],
                gen_data['output_kw'],
                gen_data['efficiency'],
                0,  # Would need to track from DERAsset
                gen_data['status']
            ))
        
        self.conn.commit()
        
    def log_alarm(self, alarm: Dict):
        """Store alarm event"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO alarms VALUES (NULL, ?, ?, ?, ?)
        """, (
            alarm['timestamp'],
            int(alarm['microgrid'].split('-')[1]),
            alarm['level'],
            alarm['message']
        ))
        self.conn.commit()
        
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()

# ==================== REGIONAL COORDINATOR ====================

class RegionalCoordinator:
    """
    Regional Energy Management System
    Coordinates multiple microgrids for optimal dispatch
    """
    def __init__(self, name: str = "Zhengzhou Regional Control"):
        self.name = name
        self.microgrids: List[MicrogridController] = []
        self.weather_station = WeatherStation()
        self.historian = DataHistorian()
        
        # Regional metrics
        self.total_load_kw = 0
        self.total_generation_kw = 0
        self.total_import_kw = 0
        self.total_export_kw = 0
        self.system_efficiency = 0
        
        self.iteration = 0
        self.start_time = time.time()
        
    def add_microgrid(self, microgrid: MicrogridController):
        """Register a microgrid with regional coordinator"""
        self.microgrids.append(microgrid)
        logger.info(f"Registered MG-{microgrid.id} ({microgrid.site.name}) with regional coordinator")
        
    def execute_regional_dispatch(self):
        """
        Regional optimization cycle
        - Update weather
        - Execute local EMS
        - Balance regional grid
        - Log data
        """
        self.iteration += 1
        
        # 1. Update weather data
        self.weather_station.fetch_live_data()
        
        # 2. Reset regional totals
        self.total_load_kw = 0
        self.total_generation_kw = 0
        self.total_import_kw = 0
        self.total_export_kw = 0
        
        # 3. Execute each microgrid EMS
        for mg in self.microgrids:
            # Get localized weather
            local_weather = self.weather_station.get_site_weather(mg.site)
            
            # Run control cycle
            mg.execute_control_cycle(local_weather)
            
            # Aggregate regional metrics
            self.total_load_kw += mg.metrics.get('load_kw', 0)
            self.total_generation_kw += mg.metrics.get('generation_kw', 0)
            
            grid_exchange = mg.metrics.get('grid_exchange_kw', 0)
            if grid_exchange > 0:
                self.total_export_kw += grid_exchange
            else:
                self.total_import_kw += abs(grid_exchange)
            
            # Log to historian
            if self.iteration % (DATA_LOGGING_INTERVAL / SCADA_UPDATE_RATE) == 0:
                self.historian.log_telemetry(mg)
                
                # Log any new alarms
                for alarm in mg.alarms:
                    self.historian.log_alarm(alarm)
                mg.alarms.clear()
        
        # 4. Calculate regional efficiency
        if self.total_load_kw > 0:
            self.system_efficiency = (self.total_generation_kw / self.total_load_kw) * 100
            
    def render_scada_display(self):
        """Render industrial SCADA interface"""
        # Clear screen
        print("\033[H\033[J")
        
        # Header
        runtime = (time.time() - self.start_time) / 3600
        print("=" * 155)
        print(f"║ {self.name.upper():^151} ║")
        print(f"║ Iteration: {self.iteration:<6} │ Runtime: {runtime:.2f}h │ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} │ Weather Quality: {self.weather_station.quality_score:.1f}% ║")
        print("=" * 155)
        
        # Weather status
        w = self.weather_station.current
        print(f"🌐 REGIONAL WEATHER: {w['temp_c']:.1f}°C │ {w['wind_ms']:.1f}m/s │ {w['solar_wm2']:.0f}W/m² │ "
              f"{w['humidity_pct']:.0f}%RH │ {w['pressure_hpa']:.1f}hPa │ "
              f"API: ✓{self.weather_station.api_success} ✗{self.weather_station.api_failure}")
        print("-" * 155)
        
        # Table header
        print(f"{'MG':<4}{'SITE':<12}{'TYPE':<12}{'MODE':<15}{'LOAD':<9}{'GEN':<9}{'BATT':<16}{'GRID':<12}{'ALARM':<7}{'COST(¥)':<10}")
        print("-" * 155)
        
        # Microgrid rows
        for mg in self.microgrids:
            summary = mg.get_summary()
            
            # Color coding
            grid_kw = summary['grid_kw']
            if grid_kw > 0:
                grid_str = f"\033[92m+{grid_kw:.1f}kW\033[0m"  # Green export
            elif grid_kw < 0:
                grid_str = f"\033[91m{grid_kw:.1f}kW\033[0m"   # Red import
            else:
                grid_str = f"{grid_kw:.1f}kW"
            
            soc = summary['soc_pct']
            if soc > 70:
                soc_str = f"\033[92m{soc:.1f}%\033[0m"
            elif soc > 30:
                soc_str = f"\033[93m{soc:.1f}%\033[0m"
            else:
                soc_str = f"\033[91m{soc:.1f}%\033[0m"
            
            # Add battery action indicator with power limit warning
            batt_action = mg.metrics.get('battery_action', 'Idle')
            batt_limited = mg.metrics.get('battery_limited', False)
            
            if batt_action == "Charging":
                indicator = "⬆" if not batt_limited else "⬆⚠"
            elif batt_action == "Discharging":
                indicator = "⬇" if not batt_limited else "⬇⚠"
            elif batt_action == "Full":
                indicator = "█"  # Battery full
            elif batt_action == "Empty":
                indicator = "▁"  # Battery empty
            else:
                indicator = "━"
            
            batt_display = f"{soc_str} {indicator}"
            
            alarm_count = summary['alarms']
            alarm_str = f"\033[91m⚠ {alarm_count}\033[0m" if alarm_count > 0 else "✓"
            
            print(f"{summary['id']:<4}{summary['site']:<12}{summary['type']:<12}{summary['mode']:<15}"
                  f"{summary['load_kw']:<9.1f}{summary['generation_kw']:<9.1f}{batt_display:<25}"
                  f"{grid_str:<21}{alarm_str:<14}{summary['cost_cny']:<10.2f}")
        
        # Regional summary
        print("=" * 155)
        net_balance = self.total_export_kw - self.total_import_kw
        balance_indicator = "✓ SURPLUS" if net_balance > 0 else "⚠ DEFICIT" if net_balance > -500 else "⛔ CRITICAL"
        
        if net_balance > 0:
            balance_str = f"\033[92m{balance_indicator} {abs(net_balance):.1f}kW\033[0m"
        else:
            balance_str = f"\033[91m{balance_indicator} {abs(net_balance):.1f}kW\033[0m"
        
        print(f"📊 REGIONAL TOTALS: Load={self.total_load_kw:.1f}kW │ Gen={self.total_generation_kw:.1f}kW │ "
              f"Import={self.total_import_kw:.1f}kW │ Export={self.total_export_kw:.1f}kW")
        print(f"⚡ SYSTEM STATUS: {balance_str} │ Efficiency: {self.system_efficiency:.1f}% │ "
              f"Grid Utilization: {(self.total_import_kw + self.total_export_kw) / 10000 * 100:.1f}%")
        print("=" * 155)
        print("🔧 Commands: Ctrl+C=Stop │ Data logged every 60s to microgrid_data.db")
        
    def export_report(self, filename: str = "microgrid_report.json"):
        """Export operational report"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'runtime_hours': (time.time() - self.start_time) / 3600,
            'iterations': self.iteration,
            'regional_summary': {
                'total_load_kw': self.total_load_kw,
                'total_generation_kw': self.total_generation_kw,
                'total_import_kw': self.total_import_kw,
                'total_export_kw': self.total_export_kw,
                'system_efficiency_pct': self.system_efficiency
            },
            'microgrids': []
        }
        
        for mg in self.microgrids:
            mg_data = {
                'id': mg.id,
                'site': mg.site.name,
                'location': {'lat': mg.site.lat, 'lon': mg.site.lon},
                'grid_import_kwh': mg.grid_import_kwh,
                'grid_export_kwh': mg.grid_export_kwh,
                'grid_import_cost_cny': mg.grid_import_cost,
                'grid_export_revenue_cny': mg.grid_export_revenue,
                'net_cost_cny': mg.grid_import_cost - mg.grid_export_revenue,
                'generators': [],
                'battery': None
            }
            
            for gen in mg.generators:
                mg_data['generators'].append({
                    'id': gen.id,
                    'technology': gen.technology,
                    'capacity_kw': gen.rated_capacity,
                    'energy_produced_kwh': gen.energy_produced_kwh,
                    'operating_hours': gen.operating_hours,
                    'efficiency': gen.current_efficiency,
                    'status': gen.status.value
                })
            
            if mg.battery:
                mg_data['battery'] = {
                    'capacity_kwh': mg.battery.rated_capacity_kwh,
                    'soc_pct': mg.battery.soc_pct,
                    'soh_pct': mg.battery.soh_pct,
                    'cycles': mg.battery.cycle_count,
                    'throughput_kwh': mg.battery.throughput_kwh
                }
            
            report['microgrids'].append(mg_data)
        
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"Report exported to {filename}")
        
    def close(self):
        """Shutdown coordinator"""
        self.historian.close()
        logger.info("Regional coordinator shutdown complete")

# ==================== MAIN EXECUTION ====================

def configure_zhengzhou_region() -> RegionalCoordinator:
    """
    Configure the complete Zhengzhou regional microgrid system
    Based on real site data and industry standards
    """
    coordinator = RegionalCoordinator("Zhengzhou Regional Microgrid Control Center")
    
    # Configuration data: [site_id, (gen1_tech, gen1_kw), (gen2_tech, gen2_kw), peak_load_kw, bess_kwh, bess_kw]
    # ADJUSTED: Increased capacities to meet realistic demand
    configs = [
        (0, ("Solar", 400), ("Grid", 800), 400, 800, 400),      # +100kW solar, +300kW grid
        (1, ("Wind", 1200), ("Solar", 300), 600, 1000, 500),    # +400kW wind, +100kW solar
        (2, ("Grid", 1500), ("Solar", 200), 1200, 500, 250),    # +500kW grid, +100kW solar
        (3, ("Coal", 700), ("Wind", 300), 800, 400, 200),       # +200kW coal, +100kW wind
        (4, ("Hydro", 400), ("Wind", 600), 200, 300, 150),      # +100kW hydro, +200kW wind
        (5, ("Solar", 500), ("Biomass", 200), 150, 400, 200),   # +100kW solar, +100kW biomass
        (6, ("Grid", 1200), ("Solar", 400), 900, 1000, 500),    # +400kW grid, +100kW solar
        (7, ("Grid", 900), ("Diesel", 300), 700, 300, 150),     # +300kW grid, +100kW diesel
        (8, ("Biomass", 600), ("Coal", 600), 900, 500, 250),    # +200kW each
        (9, ("Solar", 800), ("Wind", 200), 300, 800, 400),      # +200kW solar, +100kW wind
        (10, ("Solar", 300), ("Grid", 400), 350, 400, 200),     # +100kW solar, +200kW grid
        (11, ("Solar", 700), ("Grid", 800), 800, 1000, 500),    # +200kW solar, +300kW grid
        (12, ("Wind", 400), ("Diesel", 200), 150, 200, 100),    # +100kW wind, +100kW diesel
    ]
    
    for site_idx, gen1, gen2, load, bess_kwh, bess_kw in configs:
        site = INDUSTRIAL_SITES[site_idx]
        mg = MicrogridController(mg_id=site.id, site=site)
        
        # Add generators
        mg.add_generator(technology=gen1[0], capacity_kw=gen1[1])
        mg.add_generator(technology=gen2[0], capacity_kw=gen2[1])
        
        # Add battery
        mg.add_battery(capacity_kwh=bess_kwh, power_kw=bess_kw)
        
        # Set load profile
        mg.set_load_profile(peak_kw=load)
        
        # Register with coordinator
        coordinator.add_microgrid(mg)
    
    logger.info(f"Configured {len(configs)} microgrids in Zhengzhou region")
    return coordinator

def main():
    """Main execution loop"""
    print("\n" + "="*80)
    print("INDUSTRIAL MICROGRID DIGITAL TWIN - ZHENGZHOU REGION")
    print("IEC 61850 | IEEE 1547 | ISO 50001 Compliant")
    print("="*80 + "\n")
    
    # Initialize system
    coordinator = configure_zhengzhou_region()
    
    try:
        while True:
            # Execute regional dispatch
            coordinator.execute_regional_dispatch()
            
            # Render SCADA display
            coordinator.render_scada_display()
            
            # Export report every 10 minutes
            if coordinator.iteration % (600 / SCADA_UPDATE_RATE) == 0:
                coordinator.export_report()
            
            # Wait for next cycle
            time.sleep(SCADA_UPDATE_RATE)
            
    except KeyboardInterrupt:
        print("\n\n⏸️  SYSTEM SHUTDOWN INITIATED")
        print("="*80)
        
        # Final report
        coordinator.export_report("final_report.json")
        
        # Print summary statistics
        print("\n📈 FINAL STATISTICS:")
        print("-"*80)
        for mg in coordinator.microgrids:
            print(f"\nMG-{mg.id:02d} {mg.site.name} ({mg.site.site_type}):")
            print(f"  Grid Import:  {mg.grid_import_kwh:.2f} kWh  (Cost: ¥{mg.grid_import_cost:.2f})")
            print(f"  Grid Export:  {mg.grid_export_kwh:.2f} kWh  (Revenue: ¥{mg.grid_export_revenue:.2f})")
            print(f"  Net Cost:     ¥{mg.grid_import_cost - mg.grid_export_revenue:.2f}")
            
            if mg.battery:
                print(f"  Battery SOH:  {mg.battery.soh_pct:.1f}% ({mg.battery.cycle_count:.1f} cycles)")
            
            for gen in mg.generators:
                print(f"  {gen.technology}: {gen.energy_produced_kwh:.1f}kWh, "
                      f"{gen.operating_hours:.1f}h, {gen.current_efficiency*100:.1f}% eff")
        
        print("\n"+"="*80)
        coordinator.close()
        print("✅ System shutdown complete. Data saved to microgrid_data.db")

if __name__ == "__main__":
    main()
