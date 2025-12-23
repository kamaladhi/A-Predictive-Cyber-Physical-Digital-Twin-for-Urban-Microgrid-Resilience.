# Digital Twin Core – Implementation Documentation

## 1. Introduction

This document describes **what has been implemented so far** in the Digital Twin Core for the microgrid project.  
It is intended as **technical documentation**, not a README.

The purpose of this document is:
- To clearly explain the work completed by **Person 3 (Digital Twin Core Engineer)**
- To help team members understand the architecture and logic before making changes
- To serve as implementation evidence for reports and reviews

This document focuses on **what exists, why it exists, and how components interact**.

---

## 2. Scope of Work Completed

The following capabilities have been fully implemented:

- Real-time data ingestion using MQTT
- Unified virtual microgrid state modeling
- Appliance-level integration using NILM output
- Forecast integration for solar, wind, load, and price
- Uncertainty-aware data fusion
- Demand response decision logic
- Alert generation and publishing
- REST and WebSocket APIs for frontend dashboards
- End-to-end automated testing

This implementation converts the Digital Twin from a **theoretical concept** into a **working backend system**.

---

## 3. Digital Twin Architecture

### 3.1 Conceptual Flow

```
NILM / Forecast Models
        |
        | (MQTT)
        v
Ingestion Layer
        |
        v
State Synchronization
        |
        v
Virtual Microgrid State
        |
        v
Data Fusion & Validation
        |
        v
Demand Response Logic
        |
        +--> Alerts (MQTT)
        |
        +--> APIs (Dashboard)
```

Each block is implemented as an independent module to ensure clarity and scalability.

---

## 4. Component-Level Implementation

### 4.1 Digital Twin Orchestrator (`main.py`)

**Purpose:**  
Acts as the central coordinator of the Digital Twin.

**Responsibilities:**
- Initializes all core components
- Receives streaming data callbacks from MQTT
- Updates the virtual state
- Triggers data fusion and DR analysis
- Publishes alerts
- Runs periodic background analysis
- Starts the API server

This file defines **how the Digital Twin operates as a single system**.

---

### 4.2 MQTT Ingestion Layer (`mqtt_subscribe.py`)

**Purpose:**  
Handles real-time data ingestion from external sources.

**Key Features:**
- Subscribes to `/microgrid/nilm` and `/microgrid/forecast`
- Validates incoming messages
- Buffers asynchronous messages
- Handles timestamp alignment
- Prevents malformed data from entering the Digital Twin

This module ensures **robust, fault-tolerant data ingestion**.

---

### 4.3 Virtual State Definition (`statemodel.py`)

**Purpose:**  
Defines the structure of the Digital Twin’s internal state.

**State Elements:**
- Total load and appliance-level consumption
- Renewable generation (solar, wind, fuel cell)
- Battery state of charge and power flow
- Grid import/export and pricing
- Forecasted values
- Uncertainty parameters
- Performance metrics
- Emissions tracking

This file defines **what the Digital Twin knows about the microgrid**.

---

### 4.4 State Synchronization Engine (`state_sync.py`)

**Purpose:**  
Maintains synchronization between real-world data and the virtual microgrid.

**Functions:**
- Converts NILM data into appliance states
- Converts forecast data into future-aware state variables
- Handles uncertainty from confidence scores
- Recalculates power balance
- Updates performance metrics
- Validates physical consistency
- Persists state in Redis

This module ensures the **virtual microgrid remains consistent and realistic**.

---

### 4.5 Data Fusion Engine (`datafusion.py`)

**Purpose:**  
Evaluates data reliability and propagates uncertainty.

**Implemented Capabilities:**
- Data quality scoring
- NILM vs appliance sum validation
- Anomaly detection
- Forecast accuracy checks
- UT-inspired uncertainty propagation
- Overall uncertainty scoring

Note:  
The Unscented Transform is implemented in a **simplified, real-time-friendly form** suitable for Digital Twin operation.

This module answers:
> “Can the current data and predictions be trusted?”

---

### 4.6 Demand Response Engine (`dr_logic.py`)

**Purpose:**  
Generates decisions and alerts based on system state.

**Implemented Logic:**
- Peak price detection
- Critical load detection
- Battery charge/discharge recommendations
- Hybrid GA-based load shifting (prototype)
- Renewable utilization improvement
- Net-zero emissions alerts

The optimization logic is **demonstrative and prototype-level**, designed to show DT-driven decision-making rather than industrial-grade optimization.

This module answers:
> “What actions should be taken now?”

---

### 4.7 API Layer (`api.py`)

**Purpose:**  
Exposes Digital Twin data to external systems.

**Interfaces Provided:**
- REST APIs for:
  - State
  - Metrics
  - Appliances
  - Forecasts
  - Alerts
- WebSocket endpoint for real-time streaming

This layer enables **frontend dashboards and system integration**.

---

### 4.8 Validation & Testing (`test.py`)

**Purpose:**  
Provides automated end-to-end verification.

**Test Coverage:**
- NILM ingestion
- Appliance uncertainty handling
- Solar and wind forecasts
- Price forecasting for RTP/TOU
- Demand response triggers
- Emissions alerts

Passing all tests confirms the Digital Twin backend is **operational and consistent**.

---

## 5. Design Principles Followed

- Single source of truth (backend state)
- Clear separation of concerns
- No business logic in frontend
- Real-time operation focus
- Explicit uncertainty modeling
- Explainable decision logic

These principles align with **industry Digital Twin best practices**.

---

## 6. Current Status Summary

| Feature | Status |
|------|------|
| Digital Twin Core | Implemented |
| Real-time Ingestion | Implemented |
| Forecast Integration | Implemented |
| Data Fusion | Implemented |
| Demand Response | Implemented |
| API Layer | Implemented |
| Frontend Dashboard | Pending (Person 4) |

---

## 7. Next Steps (Out of Scope for This Document)

- Frontend dashboard implementation
- Hardware-in-the-loop integration
- Closed-loop control execution
- Long-term historical analytics

---

## 8. Conclusion

This document captures the **current state of implementation** of the Digital Twin Core.  
The work completed so far demonstrates a **functional, real-time, uncertainty-aware Digital Twin backend** suitable for research, demonstration, and further extension by the team.
