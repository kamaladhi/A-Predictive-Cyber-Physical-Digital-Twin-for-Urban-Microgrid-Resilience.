# Digital Twin Framework for Urban Microgrid Coordination

A comprehensive digital twin-based coordination framework for heterogeneous urban microgrids that enforces priority-aware resilience policies and improves city-level survivability during grid outages.

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🎯 Project Overview

This framework implements a **7-layer digital twin architecture** for coordinating multiple heterogeneous microgrids in an urban environment. It ensures **100% critical load protection** through priority-aware policies while maximizing overall load satisfaction during grid outages.

### Key Features

- ✅ **Multi-Microgrid Coordination**: Manages 4 heterogeneous microgrids (Hospital, University, Residential, Industrial)
- ✅ **Priority-Aware Policies**: Enforces critical load protection with configurable priority levels
- ✅ **Real-Time Monitoring**: MQTT-based telemetry ingestion with data fusion
- ✅ **Load Forecasting**: 6-hour ahead forecasting with 4 different methods (Exponential Smoothing, Time-of-Day, Linear Trend, Persistence)
- ✅ **Demand Response**: Automated DR alerts (Peak Load, High Cost, Battery Low, Opportunity, Resilience)
- ✅ **Energy Management**: Local + Global EMS with inter-microgrid power exchange
- ✅ **Live Dashboard**: Streamlit-based visualization with real-time metrics
- ✅ **Comprehensive Logging**: 6 log types + PDF reports + JSON summaries

---

## 🏗️ Architecture

### 7-Layer Digital Twin Framework

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 7: Visualization & Reporting (Streamlit Dashboard)   │
├─────────────────────────────────────────────────────────────┤
│ Layer 6: Analytics & Logging (6 logs + PDF reports)        │
├─────────────────────────────────────────────────────────────┤
│ Layer 5: Forecasting & Validation (6h ahead, 4 methods)    │
├─────────────────────────────────────────────────────────────┤
│ Layer 4: Energy Sharing (Power Exchange Model)             │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: Global EMS Coordination (City-Level)              │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: Local EMS Management (Per-Microgrid)              │
├─────────────────────────────────────────────────────────────┤
│ Layer 1: Digital Twin Core (MQTT + Fusion + DR Alerts)     │
└─────────────────────────────────────────────────────────────┘
```

### System Components

| Component | Description | Files |
|-----------|-------------|-------|
| **DigitalTwin** | Core DT logic, MQTT, data fusion, forecasting | 8 files |
| **EMS** | Energy management, coordination, power exchange | 6 files |
| **Analytics** | City-level metrics and survivability tracking | 2 files |
| **Utils** | Factory patterns for microgrid loading | 2 files |
| **Visualization** | Streamlit dashboard for live monitoring | 2 files |
| **Microgrid** | 4 heterogeneous microgrid simulators | 4+ dirs |

---

## 📋 Requirements

- Python 3.12+
- pandas
- numpy
- matplotlib
- plotly
- streamlit
- dataclasses
- typing

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone <repository-url>
cd Digital-twin-microgrid

# Install dependencies
pip install pandas numpy matplotlib plotly streamlit
```

### 2. Run City Simulation

```bash
# Run complete city-scale simulation with all 4 microgrids
python run_digital_twin_city_simulation.py
```

This will:
- Load all 4 microgrids (Hospital, University, Residence, Industrial)
- Run coordinated simulation across multiple scenarios
- Generate results in `city_simulation_results/`
- Create logs in `digital_twin_logs/`

### 3. View Live Dashboard

```bash
# Launch Streamlit dashboard
streamlit run Visualization/streamlit_dashboard.py
```

Access at: `http://localhost:8501`

---

## 📁 Project Structure

```
Digital-twin-microgrid/
│
├── DigitalTwin/                    # Core Digital Twin (8 files)
│   ├── mqtt_subscriber.py          # MQTT telemetry ingestion
│   ├── data_fusion.py              # Multi-source state fusion
│   ├── dr_alerts.py                # 5 types of DR alerts
│   ├── dt_logging.py               # 6 log files + PDF generation
│   ├── twin_forecasting.py         # 4 forecasting methods
│   ├── forecast_validator.py       # MAE/RMSE/MAPE validation
│   ├── digital_twin.py             # Core state management
│   └── __init__.py
│
├── EMS/                            # Energy Management System (6 files)
│   ├── iems_orchestrator.py        # Rule-based EMS commands
│   ├── local_ems_manager.py        # Per-microgrid EMS
│   ├── global_ems.py               # City-level coordination
│   ├── power_exchange_model.py     # Inter-microgrid power flows
│   ├── coordinator.py              # Priority-aware coordination
│   ├── proactive_coordinator.py    # Forecast-based coordination
│   └── __init__.py
│
├── Analytics/                      # Metrics & Reporting (2 files)
│   ├── city_metrics.py             # City survivability metrics
│   └── __init__.py
│
├── Utils/                          # Utilities (2 files)
│   ├── microgrid_factory.py        # Factory for 4 microgrid types
│   └── __init__.py
│
├── Visualization/                  # Dashboards (2 files)
│   ├── streamlit_dashboard.py      # Live monitoring dashboard
│   └── __init__.py
│
├── Microgrid/                      # Simulators (4 types)
│   ├── Hospital/                   # 500-bed teaching hospital
│   ├── university_microgrid/       # Multi-campus university
│   ├── residence/                  # 400-apartment community
│   └── Industry_microgrid/         # Auto manufacturing plant
│
├── run_digital_twin_city_simulation.py    # Main entry point
│
├── city_simulation_results/        # Simulation outputs
├── digital_twin_logs/              # Runtime logs
├── docs/                           # Documentation
└── README.md                       # This file
```

---

## 🎮 Usage Examples

### Example 1: Load and Run Single Microgrid

```python
from Utils.microgrid_factory import MicrogridFactory, MicrogridType

# Load hospital microgrid
config = MicrogridFactory.load_config(MicrogridType.HOSPITAL)
simulator = MicrogridFactory.load_simulator(MicrogridType.HOSPITAL, config)

# Run simulation
state = simulator.step(grid_available=False)
print(f"Critical load protected: {state.critical_load_kw} kW")
```

### Example 2: City-Level Coordination

```python
from EMS.coordinator import PriorityAwareCoordinator
from Analytics.city_metrics import CityMetricsTracker

# Initialize coordinator
coordinator = PriorityAwareCoordinator()

# Register microgrids with priorities
coordinator.register_microgrid('hospital', 'hospital', 'City Hospital', priority=1)
coordinator.register_microgrid('university', 'university', 'City University', priority=2)

# Coordinate
coordination_state = coordinator.coordinate(timestamp="2026-01-05 10:00:00")
print(f"Resilience Score: {coordination_state.resilience_score:.2%}")
```

### Example 3: Load Forecasting

```python
from DigitalTwin.twin_forecasting import LoadForecaster

forecaster = LoadForecaster()
forecast = forecaster.forecast_exponential_smoothing(
    historical_load=[400, 420, 410, 430],
    hours_ahead=6
)
print(f"Next hour forecast: {forecast.forecast_hours[0]:.1f} kW")
print(f"Confidence: {forecast.confidence:.2%}")
```

---

## 📊 Simulation Scenarios

The framework includes pre-configured scenarios:

| Scenario | Description | Grid Availability | Duration |
|----------|-------------|-------------------|----------|
| **Normal Operation** | Standard operation | 100% | 24h |
| **Morning Peak Outage** | Outage during morning peak | 0% (07:00-12:00) | 5h |
| **Evening Peak Outage** | Outage during evening peak | 0% (17:00-22:00) | 5h |
| **Night Outage** | Outage during low demand | 0% (00:00-06:00) | 6h |
| **Extended Outage** | Long-duration outage | 0% (08:00-20:00) | 12h |

Results include:
- ✅ **100% Critical Load Protection** maintained across all scenarios
- 📈 Resilience scores: 0.80-0.84 (degraded) to 0.98+ (normal)
- 📊 Load satisfaction: 85-100% depending on scenario

---

## 🔧 Configuration

### Microgrid Priorities

```python
PRIORITIES = {
    1: 'Hospital',      # Highest - Medical/Life Safety
    2: 'University',    # High - Education/Research
    3: 'Residence',     # Medium - Residential Safety
    4: 'Industrial',    # Lowest - Manufacturing (flexible)
}
```

### Forecasting Methods

- **Exponential Smoothing**: Weighted average of historical data (α=0.3)
- **Time-of-Day**: Pattern-based using 7-day historical averages
- **Linear Trend**: Regression-based with recent trend
- **Persistence**: Last known value (baseline)

---

## 📈 Performance Metrics

### City-Level Metrics
- **Resilience Score**: 0-1 (0.7×critical_ratio + 0.3×load_ratio)
- **Critical Load Protection**: Percentage of critical load served
- **Load Satisfaction**: Percentage of total load served
- **Energy Exchange**: Inter-microgrid power flows (kW)

### Forecast Accuracy
- **MAE**: Mean Absolute Error
- **RMSE**: Root Mean Square Error
- **MAPE**: Mean Absolute Percentage Error
- **Confidence Intervals**: 95% confidence bounds

---

## 📚 Documentation

Comprehensive documentation available in:

- **[ARCHITECTURE_OVERVIEW.md](ARCHITECTURE_OVERVIEW.md)** - System architecture and data flow
- **[FILES_USAGE_GUIDE.md](FILES_USAGE_GUIDE.md)** - Which files to use and import patterns
- **[CODEBASE_CLEANUP_REPORT.md](CODEBASE_CLEANUP_REPORT.md)** - Recent cleanup and organization
- **[docs/](docs/)** - Additional guides and API documentation

---

## 🧪 Testing

Run integration tests:

```bash
# Run test suite
python -m pytest tests/

# Or import and test manually
from DigitalTwin.digital_twin import DigitalTwin
from EMS.coordinator import PriorityAwareCoordinator
# ... test your components
```

---

## 📊 Output Files

### Simulation Results
- `city_simulation_results/<scenario>/` - Coordination data and metrics
- Format: CSV (time-series) + JSON (summary)

### Logs
- `digital_twin_logs/state_updates.csv` - Digital twin state changes
- `digital_twin_logs/data_fusion.csv` - Fused sensor data
- `digital_twin_logs/dr_alerts.json` - Demand response alerts
- `digital_twin_logs/iems_commands.csv` - EMS commands issued
- `digital_twin_logs/mqtt_telemetry.csv` - Raw MQTT data
- `digital_twin_logs/summary_report_<timestamp>.json` - Session summary
- `digital_twin_logs/report_<timestamp>.pdf` - Visual report

---

## 🤝 Contributing

This is a research project for digital twin-based microgrid coordination. For academic use or contributions, please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/improvement`)
3. Commit changes (`git commit -m 'Add improvement'`)
4. Push to branch (`git push origin feature/improvement`)
5. Open a Pull Request

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 👥 Authors

**Person 3 - Digital Twin Core Engineer**

Contributions:
- ✅ Layer 1: MQTT Subscriber + Data Fusion + DR Alerts + Logging
- ✅ Layer 5: Load Forecasting (4 methods) + Forecast Validation
- ✅ Layer 6: PDF Report Generation
- ✅ Integration: Complete 7-layer architecture
- ✅ Documentation: Comprehensive guides

---

## 🎓 Academic Context

This framework demonstrates:

1. **Digital Twin Technology**: Real-time synchronization between physical microgrids and digital models
2. **Priority-Aware Policies**: Tiered load shedding based on criticality
3. **Multi-Microgrid Coordination**: City-scale coordination with heterogeneous systems
4. **Resilience Engineering**: 100% critical load protection during outages
5. **Demand Response**: Automated alerts and proactive load management

### Validation Results
- ✅ **Critical Protection**: 100% maintained across all scenarios
- ✅ **Resilience Scores**: 0.80-0.98 (degraded to normal operations)
- ✅ **Forecast Accuracy**: MAPE < 15% for most scenarios
- ✅ **Coordination Efficiency**: Minimized load shedding via priority enforcement

---

## 📞 Support

For questions, issues, or academic collaboration:
- Open an issue on GitHub
- Check documentation in [docs/](docs/)
- Review [FILES_USAGE_GUIDE.md](FILES_USAGE_GUIDE.md) for usage patterns

---

## 🔮 Future Enhancements

Potential areas for expansion:
- [ ] Real-time MQTT broker integration
- [ ] Machine learning-based forecasting
- [ ] Multi-objective optimization (cost + resilience)
- [ ] Weather-dependent renewable generation
- [ ] Electric vehicle integration
- [ ] Blockchain-based energy trading
- [ ] Extended to 10+ microgrids

---

**Last Updated**: January 5, 2026  
**Status**: Production Ready ✅  
**Version**: 1.0.0
