# ⚡ A Predictive Cyber-Physical Digital Twin for Urban Microgrid Resilience

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Build](https://img.shields.io/badge/build-passing-brightgreen)
![Status](https://img.shields.io/badge/status-active_research-orange)

---

## 📖 Abstract

**A Predictive Cyber-Physical Digital Twin for Urban Microgrid Resilience** is an advanced Python-based simulation, control, and visualization framework that shifts energy management from pure economic dispatch to **priority-aware resilience and disaster survivability**.

The system models a city with **4 heterogeneous microgrids** (Hospital, University, Industrial, Residential) and orchestrates them through a 5-layer Digital Twin architecture. It combines **deep-learning solar forecasting**, an **Extended Kalman Filter (EKF)** for state estimation, a **Rolling-Horizon Model Predictive Controller (MPC)** for optimal dispatch, and an **Energy Exchange Bus** for inter-microgrid power sharing — all connected via an **MQTT-based IoT communication layer** with realistic cyber-physical failure modeling.

Under disaster scenarios (grid blackouts, power shortages, cyber-attacks), the system dynamically prioritizes critical infrastructure using a **Value of Lost Load (VOLL)** hierarchy, ensuring hospitals remain powered even at the expense of residential comfort.

---

## ✨ Key Innovations (Novelty)

| # | Innovation | Description |
|---|---|---|
| 1 | **Priority-Aware MPC (VOLL Hierarchy)** | LP-based optimizer enforces strict VOLL penalties: Hospital = $10M/kWh, University = $1M/kWh, Industrial = $100K/kWh, Residential = $1K/kWh |
| 2 | **CNN-BiLSTM Solar Forecasting with Attention** | Deep learning neural network predicts Solar Clearness Index ($K_t$) with probabilistic uncertainty via Quantile Regression |
| 3 | **Extended Kalman Filter (4-State EKF)** | Real-time state estimation with anomaly detection for cyber-attack resilience |
| 4 | **Adaptive Data Fusion Engine** | Weighted sensor fusion (NILM + raw sensors + forecasts) with EKF-confidence-adaptive weights |
| 5 | **Shadow Simulator (Monte Carlo)** | Parallel what-if simulations for predictive horizon and proactive recommendations |
| 6 | **Markov Cyber-Link Failure Model** | Realistic MQTT network failure simulation with P(UP→DOWN) = 1%, P(DOWN→UP) = 50% |
| 7 | **Live IEEE 1366 Reliability Indices** | SAIDI, SAIFI, CAIDI, LOLP, ASAI computed per simulation step — not just post-hoc |
| 8 | **RESTful API Layer (FastAPI)** | SCADA-interoperable endpoints for programmatic Digital Twin interaction |

---

## 🏗️ System Architecture — 5 Layers

```
┌──────────────────────────────────────────────────────────────────────┐
│  LAYER 5: VISUALIZATION & API                                       │
│  ┌─────────────────────┐ ┌──────────────────┐ ┌──────────────────┐ │
│  │ Streamlit Dashboard │ │ FastAPI REST API  │ │  MQTT Broker     │ │
│  │ (4 Persona Tabs)    │ │ /api/v1/state    │ │  (Mosquitto)     │ │
│  └─────────────────────┘ └──────────────────┘ └──────────────────┘ │
├──────────────────────────────────────────────────────────────────────┤
│  LAYER 4: DIGITAL TWIN                                              │
│  ┌───────────────┐ ┌──────────────────┐ ┌───────────────────────┐  │
│  │ EKF State     │ │ Shadow Simulator │ │ Data Fusion Engine    │  │
│  │ Estimator     │ │ (Monte Carlo)    │ │ (Adaptive Weights)    │  │
│  └───────────────┘ └──────────────────┘ └───────────────────────┘  │
├──────────────────────────────────────────────────────────────────────┤
│  LAYER 3: CONTROL (CITY-LEVEL EMS)                                  │
│  ┌──────────────┐ ┌──────────────────┐ ┌────────────────────────┐  │
│  │ MPC Optimizer │ │ Energy Exchange  │ │ Demand Response        │  │
│  │ (LP / PuLP)  │ │ Bus (P2P)        │ │ Coordinator            │  │
│  └──────────────┘ └──────────────────┘ └────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────┤
│  LAYER 2: AI FORECASTING                                            │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ CNN-BiLSTM with Temporal Attention (Solar Clearness Index Kt) │ │
│  │ + Quantile Regression (P10, P50, P90) + Physics-Based PV Model│ │
│  └────────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────┤
│  LAYER 1: PHYSICAL SIMULATION                                       │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────────┐  │
│  │🏥 Hospital │ │🎓 University│ │🏭 Industrial│ │🏠 Residential │  │
│  │ CRITICAL   │ │ HIGH       │ │ MEDIUM     │ │ LOW            │  │
│  └────────────┘ └────────────┘ └────────────┘ └────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 🔬 Algorithms & Techniques — Detailed Reference

### 1. Extended Kalman Filter (EKF) — State Estimation

**Module:** `src/digital_twin/state_estimator.py`

The EKF maintains robust state estimates under noisy sensor conditions.

| Parameter | Value |
|---|---|
| **State Vector** | `[SoC, battery_power, generator_power, load]` (4 states) |
| **Measurement Vector** | `[measured_SoC, measured_power_flow, measured_load]` (3 measurements) |
| **Process Noise (Q)** | Diagonal: `[0.001, 0.5, 0.5, 1.0]` |
| **Measurement Noise (R)** | Diagonal: `[0.01, 2.0, 5.0]` |
| **Confidence Mapping** | `confidence = exp(-variance / σ_ref)` |
| **Anomaly Detection** | Innovation-based: flags when `|innovation| > 3σ` |
| **Time-to-Exhaustion** | `TTE = (SoC × capacity) / net_load` predicted forward |

**Key Classes:**
- `MicrogridStateEstimator` — Per-microgrid EKF with predict/update cycle
- `CityStateEstimator` — Aggregates all MG estimators, computes city-level confidence
- `StateEstimate` — Dataclass with value, variance, confidence, 95% CI

---

### 2. Rolling-Horizon MPC (Model Predictive Control)

**Module:** `src/ems/predictive_optimizer.py`

The MPC solves a Linear Program (LP) at each timestep to optimally dispatch generators, batteries, and grid imports.

| Parameter | Value |
|---|---|
| **Solver** | PuLP (CBC backend) |
| **Horizon** | 8 steps = 2 hours (configurable: 4h, 8h) |
| **Timestep** | 15 minutes (DT_MINUTES = 15) |
| **Objective** | Minimize: `Σ (fuel_cost + grid_cost + VOLL × shed)` |
| **VOLL Hierarchy** | Hospital: $10M/kWh, University: $1M/kWh, Industrial: $100K/kWh, Residential: $1K/kWh |

**Decision Variables (per MG, per step):**
- `gen_kw` — Generator output (0 to capacity)
- `batt_kw` — Battery charge/discharge (-capacity to +capacity)
- `grid_kw` — Grid import (0 when islanded)
- `shed_kw` — Load shed (0 to load, penalized by VOLL)

**Constraints:**
- Power balance: `PV + gen + grid + batt_discharge = load + batt_charge + shed`
- SOC dynamics: `SoC(t+1) = SoC(t) + η_charge × batt × Δt / capacity`
- Generator ramp limits
- Battery SOC bounds: `[5%, 95%]`
- Grid availability (forced to 0 during outage)

**Telemetry Published:** `pv_forecast_kw`, `fuel_remaining_liters`, `renewable_pct`, `grid_cost_step_rs`, `solve_time_ms`

---

### 3. Energy Exchange Bus (Inter-Microgrid P2P Sharing)

**Module:** `src/ems/resource_sharing.py`

A virtual energy trading bus that matches surplus generators with deficit consumers using a priority-weighted algorithm.

| Parameter | Value |
|---|---|
| **Bus Capacity** | 200 kW |
| **Transfer Efficiency** | 95% (5% loss per transfer) |
| **Min Transfer** | 5 kW |
| **Max Simultaneous** | 3 transfers per step |
| **Min Donor SOC** | 30% (donors below this can't export) |

**Algorithm:**
1. Collect `SurplusReport` from MGs with excess generation
2. Collect `EnergyRequest` from MGs with deficit
3. Sort requests by priority (CRITICAL > HIGH > MEDIUM > LOW), then by deficit magnitude
4. Match surplus to requests, respecting bus capacity and link health
5. Apply transfer efficiency loss
6. Log all transfers for audit

**Cyber-Link Failure Model (`CyberLinkManager`):**
- Uses a 2-state Markov chain per communication link
- P(UP → DOWN) = 1% per step
- P(DOWN → UP) = 50% per step
- Failed links block energy transfers to/from that MG

---

### 4. Demand Response (DR) Coordinator

**Module:** `src/ems/demand_response.py`

A comprehensive DR framework coordinating voluntary and mandatory load reduction across all microgrids.

**DR Event Types:**
| Type | Description |
|---|---|
| `ECONOMIC` | Price-signal based voluntary reduction |
| `EMERGENCY` | Mandatory reduction during grid emergencies |
| `PEAK_SHAVING` | Reduce system peak demand |
| `ANCILLARY_SERVICE` | Grid support services |
| `RENEWABLE_CURTAILMENT` | Reduce load when renewables are scarce |

**Priority Levels:** VOLUNTARY (1) → RECOMMENDED (2) → MANDATORY (3) → EMERGENCY (4)

**Incentive/Penalty System:**
- Incentive rate (₹/kWh) for participation
- Performance bonus for exceeding targets
- Penalty for under-delivery
- Participation tracking with achievement percentages

---

### 5. CNN-BiLSTM Solar Forecasting with Temporal Attention

**Module:** `src/solar/solar_forecasting.py`

A deep learning model for multi-horizon solar generation prediction.

| Component | Detail |
|---|---|
| **Input** | Historical NSRDB data (GHI, DNI, DHI, temperature, humidity, wind) |
| **CNN Layers** | 1D convolutional feature extraction for local patterns |
| **BiLSTM** | Bidirectional LSTM for temporal sequence modeling |
| **Attention** | Temporal attention mechanism for focusing on critical forecast windows |
| **Output** | Solar Clearness Index (Kt) prediction |
| **Quantile Regression** | Generates P10, P50, P90 bounds for uncertainty quantification |
| **Horizons** | 1h, 6h, 24h multi-horizon forecasts |
| **Fallback** | Statistical baseline when LSTM model unavailable |

**Data Pipeline (`src/solar/solar_preprocessing.py`):**
- Loads NSRDB CSV files (26,280 rows across 2018–2020)
- Cleans missing values, computes Kt from GHI/extraterrestrial irradiance
- Splits into train/val/test with proper temporal ordering

**Physics-Based PV Model (`src/solar/pv_power_model.py`):**
- Converts Kt predictions to actual PV power output (kW)
- Accounts for panel tilt, azimuth, temperature derating, inverter efficiency

---

### 6. Data Fusion Engine

**Module:** `src/digital_twin/data_fusion_engine.py`

Combines multiple data sources into a unified "Virtual Twin" state.

| Feature | Description |
|---|---|
| **Temporal Sync** | Validates data freshness across NILM, forecasts, measurements (configurable staleness window) |
| **Adaptive Weights** | NILM / raw sensor / forecast weights shift based on EKF confidence |
| **Low Confidence Mode** | When EKF confidence < 50%, trusts NILM more (weight shifts from sensors) |
| **Audit Logging** | Records fusion events with source weights for post-hoc analysis |

**Data Sources:**
1. Raw sensor measurements (SOC, load, PV, generator)
2. NILM (Non-Intrusive Load Monitoring) disaggregated readings
3. AI forecast outputs (solar, load predictions)

---

### 7. Shadow Simulator (Predictive Digital Twin)

**Module:** `src/digital_twin/shadow_simulator.py`

Runs accelerated "what-if" simulations to predict system behavior.

| Parameter | Value |
|---|---|
| **Acceleration Factor** | 10x faster than real-time |
| **Monte Carlo Samples** | 10 per scenario (configurable) |
| **Scenarios** | Grid failure, generator trip, solar variability, load spike |

**Lightweight Prediction (Inline in `run_experiment.py`):**
- Runs every 12 steps (~3 simulation hours)
- Computes per-microgrid: time-to-battery-exhaustion, fuel remaining hours
- Risk assessment: LOW / ELEVATED / CRITICAL
- Publishes proactive recommendations via MQTT `city/predictions`

---

### 8. Resilience Metrics — IEEE 1366-2012

**Module:** `src/digital_twin/resilience_metrics.py`

| Index | Formula | Description |
|---|---|---|
| **SAIDI** | `Σ(interruption_duration × customers) / total_customers` | System Avg Interruption Duration |
| **SAIFI** | `Σ(interruptions × customers) / total_customers` | System Avg Interruption Frequency |
| **CAIDI** | `SAIDI / SAIFI` | Customer Avg Interruption Duration |
| **LOLP** | `shed_steps / total_steps` | Loss of Load Probability |
| **ASAI** | `1 - (Σ unserved_hours / Σ total_hours)` | Avg Service Availability Index |
| **EENS** | `Σ (shed_kW × Δt)` | Expected Energy Not Served (kWh) |

These are computed **per simulation step** and broadcast live via MQTT.

---

### 9. Cyber-Physical Security — Attack Injection & Detection

**Module:** `scripts/run_experiment.py` (integrated)

| Feature | Description |
|---|---|
| **Attack Injection** | Corrupts sensor readings: SOC biased +15–30%, load scaled 0.3–0.6× |
| **EKF Anomaly Detection** | Innovation-based detection: flags when `|innovation| > 3σ` |
| **MQTT Alert Publishing** | Detected anomalies published to `city/alerts` |
| **Dashboard Indicator** | "Cyber Resilience" panel shows attack status + anomaly count |
| **Live Toggle** | Inject/stop attacks via dashboard radio selector or MQTT override |

---

## 🖥️ Dashboard — 4-Tab Persona-Based Command Center

**Module:** `dashboard/app.py`

| Tab | Persona | Key Visualizations |
|---|---|---|
| 🌍 **Macro City View** | City Mayor | City Survival Index, EKF confidence, supply donut, solar forecast vs actual, renewable gauge, fuel bars, cost ticker |
| 🤝 **Energy Market & DR** | Exchange Operator | Power balance metrics, energy mix stacked bar, self-sufficiency gauge, Sankey diagram, DR shedding table |
| 🧠 **Digital Twin AI** | Data Scientist | EKF gauge, time-to-exhaustion, EKF overlay, solver sparkline, IEEE 1366 panel, cyber resilience indicator |
| 🔋 **Local Microgrids** | Grid Operator | 2×2 grid: all 4 MGs with load/PV/SOC/gen metrics, Load vs Gen charts, SOC timelines, status indicators |

**Sidebar Scenario Control (Live Switching via MQTT):**
- 🟢 Normal (Sunny Day)
- 🔴 Grid Blackout
- 🟠 Power Shortage
- 🛡️ Cyber Attack
- 💀 Blackout + Cyber Attack

---

## 🌐 REST API (FastAPI)

**Module:** `src/api_server.py`

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/state` | Current microgrid snapshots |
| `GET` | `/api/v1/metrics` | IEEE 1366 indices + EKF confidence + solver stats |
| `GET` | `/api/v1/alerts` | System alerts (anomalies, threshold violations) |
| `POST` | `/api/v1/scenario` | Inject disaster scenario (`outage`, `shortage`, `cyber_attack`) |
| `GET` | `/api/v1/health` | Service health check |

**Start the API:**
```bash
pip install fastapi uvicorn
uvicorn src.api_server:app --port 8000
```

---

## 📁 Project Structure

```text
Digital-twin-microgrid/
├── .streamlit/
│   └── config.toml                # Streamlit theme configuration
├── dashboard/
│   └── app.py                     # 4-tab Streamlit dashboard (1400+ lines)
├── scripts/
│   ├── run_experiment.py          # Main simulation loop & statistical experiments
│   └── run_live_demo.py           # Real-time orchestrator (MQTT + Dashboard + Sim)
├── src/
│   ├── api_server.py              # FastAPI REST API layer
│   ├── digital_twin/
│   │   ├── state_estimator.py     # EKF (MicrogridStateEstimator, CityStateEstimator)
│   │   ├── shadow_simulator.py    # Monte Carlo predictive engine
│   │   ├── data_fusion_engine.py  # Adaptive weighted sensor fusion
│   │   ├── digital_twin_manager.py# Triple-state DT manager (Physical/Virtual/Predictive)
│   │   ├── resilience_metrics.py  # IEEE 1366 reliability index computation
│   │   ├── scenario_engine.py     # What-if scenario definitions
│   │   ├── outage_event_model.py  # Outage event data structures
│   │   └── twin_state.py          # TwinState dataclass
│   ├── ems/
│   │   ├── city_ems.py            # City-Level EMS coordinator (60K+ lines)
│   │   ├── predictive_optimizer.py# MPC optimizer (Rolling-horizon LP via PuLP)
│   │   ├── demand_response.py     # DR coordinator (events, incentives, tracking)
│   │   ├── resource_sharing.py    # Energy Exchange Bus + Markov cyber-link model
│   │   ├── mqtt_manager.py        # MQTT publisher/subscriber for IoT data
│   │   ├── hospital_ems.py        # Hospital-specific local EMS
│   │   ├── university_ems.py      # University-specific local EMS
│   │   ├── industry_ems.py        # Industrial-specific local EMS
│   │   ├── residence_ems.py       # Residential-specific local EMS
│   │   ├── ems_factory.py         # Factory pattern for EMS instantiation
│   │   ├── ems_decision_logger.py # Structured logging for EMS decisions
│   │   ├── common.py              # Shared enums (Priority, Policy, Mode)
│   │   └── city_integration.py    # Cross-MG coordination logic
│   ├── microgrid/
│   │   ├── Hospital/              # Hospital MG physics + parameters
│   │   ├── university_microgrid/  # University MG physics + parameters
│   │   ├── Industry_microgrid/    # Industrial MG physics + parameters
│   │   └── residence/             # Residential MG physics + parameters
│   ├── solar/
│   │   ├── solar_forecasting.py   # CNN-BiLSTM with Attention + Quantile Regression
│   │   ├── solar_preprocessing.py # NSRDB data loader & feature engineering
│   │   ├── pv_power_model.py      # Physics-based PV power conversion
│   │   ├── physics_utils.py       # Solar geometry & irradiance calculations
│   │   └── validate_solar_integration.py
│   ├── utils/                     # Helper utilities
│   └── visualization/             # Static chart generation & PDF reports
├── data/                          # Raw NSRDB solar data & load profiles
└── results/                       # Generated plots, logs, CSV/JSON outputs
```

---

## ⚙️ Installation

### Prerequisites
- Python 3.10+
- Mosquitto MQTT Broker (for live demo mode)

### Setup

```bash
# Clone
git clone https://github.com/your-username/Digital-twin-microgrid.git
cd Digital-twin-microgrid

# Virtual environment
python -m venv venv
.\venv\Scripts\activate   # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install numpy pandas scipy torch pvlib plotly streamlit
pip install paho-mqtt pulp fastapi uvicorn streamlit-autorefresh
```

---

## 🚀 Usage

### 1. Live Interactive Demo (Recommended)
Launch the full stack: MQTT Broker → Streamlit Dashboard → Real-Time Simulation.

```bash
python scripts/run_live_demo.py
```
- Dashboard opens at **http://localhost:8501**
- Use the **sidebar radio buttons** to switch scenarios live (Normal → Blackout → Shortage → Cyber Attack)
- No restart needed — scenarios change in real-time via MQTT

### 2. Scenario-Specific Demo

```bash
python scripts/run_live_demo.py --outage     # Start with forced blackout
python scripts/run_live_demo.py --shortage   # Start with power shortage
```

### 3. Headless Statistical Experiment
Run full 30-trial experiments for paper results:

```bash
python scripts/run_experiment.py --trials 30 --days 30
python scripts/run_experiment.py --trials 30 --days 30 --config MPC+DR-Optimized
```

### 4. REST API Server

```bash
uvicorn src.api_server:app --port 8000
# Visit http://localhost:8000/docs for Swagger UI
```

---

## 📊 Evaluation Metrics

The system automatically computes and publishes:

**IEEE 1366-2012 Reliability Indices:**
- ASAI (Average Service Availability Index)
- SAIDI (System Average Interruption Duration Index)
- SAIFI (System Average Interruption Frequency Index)
- CAIDI (Customer Average Interruption Duration Index)
- EENS (Expected Energy Not Served)
- LOLP (Loss of Load Probability)

**Statistical Validation:**
- Paired t-tests (Rule-Based vs MPC)
- Wilcoxon signed-rank tests (non-parametric robustness)
- Cohen's d effect size with pooled SD
- Bonferroni correction for multiple comparisons

---

## 📚 Research Reference

> **Placeholder for Citation:**
> [Author Names]. "A Predictive Cyber-Physical Digital Twin for Urban Microgrid Resilience." *Upcoming Thesis / Publication*, 2026.

---

## 📜 License

MIT License — See [LICENSE](LICENSE) for details.
