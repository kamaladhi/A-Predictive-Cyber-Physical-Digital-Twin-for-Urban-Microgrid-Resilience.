# Codebase Architecture Diagram

## 🏗️ Production Codebase Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   PRODUCTION CODEBASE (26+ files)               │
└─────────────────────────────────────────────────────────────────┘

                    run_digital_twin_city_simulation.py
                              │
                ┌─────────────┼─────────────┐
                │             │             │
           ┌────▼────┐   ┌────▼────┐   ┌───▼────┐
           │DigitalTwin   │  EMS   │   │Microgrid
           │ (8 files)    │(6 files)   │(4 sims)
           └────┬────┘   └────┬────┘   └───┬────┘
                │             │            │
         ┌──────┴──────┐      │            │
         │             │      │            │
    [MQTT]        [DataFusion]│            │
    [Alerts]      [Forecasting]          [Hospital]
    [Logging]     [Validation] │         [University]
    [DT Core]                  │         [Residence]
                               │         [Industrial]
                    ┌──────────┴──────────┐
                    │                     │
            [EMS Orchestration]  [Coordination]
            [Local Managers]     [Proactive Mode]
            [Power Exchange]     [City-Level]
                    │
         ┌──────────┴──────────┐
         │                     │
     ┌───▼────┐            ┌──▼────┐
     │Analytics   │       │ Utils  │
     │(2 files)  │       │(2 files)
     └───┬────┘           └──┬────┘
         │                    │
    [CityMetrics]      [Factory Pattern]
         │                    │
         └─────────┬──────────┘
                   │
            ┌──────▼──────┐
            │Visualization│
            │(2 files)    │
            └──────┬──────┘
                   │
          [Streamlit Dashboard]
          [Live Monitoring]
```

---

## 📂 Directory Tree (Production)

```
digital-twin-microgrid/
│
├── 🟢 DigitalTwin/                    [PRODUCTION - Core DT Logic]
│   ├── mqtt_subscriber.py             Remote telemetry ingestion
│   ├── data_fusion.py                 Multi-source state fusion
│   ├── dr_alerts.py                   Demand response alerts (5 types)
│   ├── dt_logging.py                  System logging + PDF reports
│   ├── twin_forecasting.py            Load forecasting (4 methods, 6h horizon)
│   ├── forecast_validator.py          MAE/RMSE/MAPE metrics
│   ├── digital_twin.py                Core state management
│   └── __init__.py
│
├── 🟢 EMS/                            [PRODUCTION - Energy Management]
│   ├── iems_orchestrator.py           Rule-based EMS commands
│   ├── local_ems_manager.py           Per-microgrid EMS instances
│   ├── global_ems.py                  City-level EMS coordination
│   ├── power_exchange_model.py        Inter-microgrid power flows
│   ├── coordinator.py                 ✓ Priority-aware coordination
│   ├── proactive_coordinator.py       ✓ Forecast-based coordination
│   └── __init__.py
│
├── 🟢 Analytics/                      [PRODUCTION - Metrics & Reporting]
│   ├── city_metrics.py                ✓ City survivability metrics
│   └── __init__.py
│
├── 🟢 Utils/                          [PRODUCTION - Utilities]
│   ├── microgrid_factory.py           ✓ Factory for 4 microgrid types
│   └── __init__.py
│
├── 🟢 Visualization/                  [PRODUCTION - Dashboards]
│   ├── streamlit_dashboard.py         ✓ Live Plotly dashboard
│   └── __init__.py
│
├── 🟢 Microgrid/                      [PRODUCTION - Simulators]
│   ├── Hospital/                      500-bed teaching hospital
│   ├── university_microgrid/          Multi-campus university
│   ├── residence/                     400-apt residential community
│   ├── Industry_microgrid/            Auto manufacturing plant
│   └── __init__.py
│
├── 🟢 docs/                           [Documentation]
│   └── ...
│
├── 🟢 city_simulation_results/        [Simulation Outputs]
│   └── ...
│
├── 🟢 digital_twin_logs/              [Runtime Logs]
│   └── ...
│
├── 🟡 run_digital_twin_city_simulation.py    [Main Entry Point - RENAMED]
│
├── 🔴 unnecessary/                    [ARCHIVED FILES]
│   ├── DigitalTwin_duplicates/
│   │   ├── coordinator.py             [Duplicate - in EMS/]
│   │   ├── proactive_coordinator.py   [Duplicate - in EMS/]
│   │   ├── city_metrics.py            [Duplicate - in Analytics/]
│   │   └── microgrid_factory.py       [Duplicate - in Utils/]
│   │
│   ├── demo_files/
│   │   ├── demo_city_full_coordination.py
│   │   ├── demo_person3.py
│   │   └── streamlit_dashboard.py     [Duplicate - in Visualization/]
│   │
│   ├── old_tests/
│   │   ├── integration_test_demo.py
│   │   └── test_integration.py
│   │
│   ├── old_configs/
│   │   └── digital_twin_config.json
│   │
│   └── misc/
│       ├── main.py
│       └── run_city_simulation.py
│
├── README.md
├── LICENSE
├── CODEBASE_CLEANUP_REPORT.md         [Cleanup Documentation]
└── FILES_USAGE_GUIDE.md               [Usage Reference]
```

---

## 🔄 Data Flow Architecture

```
                    ┌─────────────────────┐
                    │   Microgrid Data    │
                    │   (4 simulators)    │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  MQTT Subscriber    │  ← Ingestion
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Data Fusion       │  ← Integration
                    │  (MQTT + NILM)      │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Digital Twin Core  │  ← State Mgmt
                    │  (State + Tracking) │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
       ┌──────▼──────┐  ┌──────▼──────┐  ┌─────▼──────┐
       │Forecasting  │  │ DR Alerts   │  │  Logging   │
       │(Load 6h)    │  │  (5 types)  │  │(6 logs+PDF)│
       └──────┬──────┘  └──────┬──────┘  └─────┬──────┘
              │                │               │
       ┌──────▼──────┐  ┌──────▼──────┐      │
       │  Validation │  │  Metrics    │      │
       │(MAE/RMSE)   │  │  Tracking   │      │
       └──────┬──────┘  └─────────────┘      │
              │                              │
              └──────────────┬───────────────┘
                             │
                    ┌────────▼────────┐
                    │   EMS Layer     │  ← Energy Management
                    │  (Coordination) │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   City State    │  ← City-Level View
                    │   (All 4 MGs)   │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
        ┌─────▼─────┐  ┌─────▼─────┐  ┌───▼────┐
        │Analytics  │  │Dashboard  │  │Exports │
        │(Metrics)  │  │(Streamlit)│  │(CSV)   │
        └───────────┘  └───────────┘  └────────┘
```

---

## ✅ File Relationships

```
run_digital_twin_city_simulation.py (Main Orchestrator)
│
├─→ Utils.microgrid_factory.MicrogridFactory
│   └─→ Loads all 4 microgrid simulators
│
├─→ DigitalTwin.digital_twin.DigitalTwin (×4 instances)
│   ├─→ DigitalTwin.mqtt_subscriber.MQTTSubscriber
│   ├─→ DigitalTwin.data_fusion.DataFusionEngine
│   ├─→ DigitalTwin.dr_alerts.DRAlertEngine
│   ├─→ DigitalTwin.twin_forecasting.LoadForecaster
│   ├─→ DigitalTwin.forecast_validator.ForecastValidator
│   └─→ DigitalTwin.dt_logging.DigitalTwinLogger
│
├─→ EMS.coordinator.PriorityAwareCoordinator
│   ├─→ EMS.iems_orchestrator.IEMSOrchestrator
│   ├─→ EMS.local_ems_manager.LocalEMSManager
│   ├─→ EMS.global_ems.GlobalEMSCoordinator
│   └─→ EMS.power_exchange_model.PowerExchangeModel
│
├─→ Analytics.city_metrics.CityMetricsTracker
│   └─→ Aggregates city-level metrics
│
└─→ Visualization.streamlit_dashboard (Separate Process)
    └─→ Reads digital_twin_logs/ + city_metrics
```

---

## 📊 File Count Summary

| Component | Files | Status |
|-----------|-------|--------|
| DigitalTwin | 8 | ✅ Production |
| EMS | 6 | ✅ Production |
| Analytics | 2 | ✅ Production |
| Utils | 2 | ✅ Production |
| Visualization | 2 | ✅ Production |
| Microgrid Simulators | 4+ | ✅ Production |
| **ACTIVE** | **26+** | **✅ READY** |
| | | |
| Archived (necessary/) | 12+ | ❌ Unused |

---

**Generated**: January 5, 2026  
**Status**: PRODUCTION READY ✅
