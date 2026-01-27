# System-Level Digital Twin for Urban Microgrids

## 1. Digital Twin Architecture

The proposed Digital Twin (DT) framework architecture consists of four distinct layers, designed to mirror the physical and cyber-physical states of the city-level microgrid network.

### Block Diagram

```mermaid
graph TB
    subgraph "Digital Twin Layer (Metacontrol)"
        DT_Core[Digital Twin Manager]
        DT_State[Twin State (Physical + Cyber + Resilience)]
        DT_Scenario[Scenario Engine]
        DT_Metrics[Resilience Calculator]
        
        DT_Scenario -->|Injects Faults| DT_Core
        DT_Core -->|Aggregates| DT_State
        DT_State -->|Feeds| DT_Metrics
    end

    subgraph "Coordination Layer (Global Control)"
        CityEMS[City-Level EMS]
        Policy[Resilience Policy]
        
        CityEMS <-->|Supervisory Commands| LocalEMS
        CityEMS -->|State| DT_Core
    end

    subgraph "Control Layer (Local Optimization)"
        Hospital_EMS
        University_EMS
        Industrial_EMS
        Residential_EMS
    end

    subgraph "Physical Layer (Dynamic Simulation)"
        Hospital_Sim[Hospital Simulator]
        University_Sim[University Simulator]
        Industrial_Sim[Industrial Simulator]
        Residential_Sim[Residential Simulator]
        
        Hospital_Sim <--> Hospital_EMS
        University_Sim <--> University_EMS
        Industrial_Sim <--> Industrial_EMS
        Residential_Sim <--> Residential_EMS
        
        Hospital_Sim -.->|Telemetry| DT_Core
        University_Sim -.->|Telemetry| DT_Core
    end
```

### Layer Definitions

1.  **Physical Layer**: Comprises the high-fidelity simulators (`MicrogridSimulator` instances) for each precinct (Hospital, University, Industry, Residential). These simulators model power flow, component degradation (battery SoC, fuel), and physical dynamics (voltage/frequency droop) at a 1-5 minute resolution.
2.  **Control Layer**: The Local EMS modules (`hospital_ems.py`, etc.) that execute immediate control actions like islanding, load shedding, and generator dispatch based on local measurements and City EMS supervisory commands.
3.  **Coordination Layer**: The `CityEMS` acts as the central coordinator, implementing priority-aware resource allocation logic. It doesn't replace local control but biases it (e.g., by setting `battery_reserve_percent` or `max_shed_limit`).
4.  **Digital Twin Layer**: The new meta-layer (`DigitalTwinManager`) that wraps the entire system. It:
    -   **Replicates State**: Maintains a `TwinState` object fusing telemetry (Physical) and control intent (Cyber).
    -   **Injects Contingencies**: The `ScenarioEngine` introduces outage events (time-bound grid failures).
    -   **Evaluates Resilience**: The `ResilienceMetricCalculator` computes quantifiable metrics (Survivability Index) in real-time.

## 2. Module Breakdown

### `digital_twin_manager.py`
The orchestration engine.
-   **Responsibility**: Instantiates all simulators and the City EMS. Runs the master simulation loop. Synchronizes time across all agents. Adapts heterogeneous simulator outputs into a standardized `MicrogridStatus` format for the City EMS.
-   **Inputs**: `ScenarioConfig` (defining the outage timeline).
-   **Outputs**: A history of `TwinState` and a final `ResilienceScorecard`.

### `twin_state.py`
The data model.
-   **Responsibility**: Defines the schema for the Digital Twin's knowledge.
-   **Components**:
    -   `PhysicalState`: Aggregated load, generation, SoC, fuel.
    -   `CyberState`: Active EMS modes, shed commands, communication health.
    -   `ResilienceState`: Real-time computed risk indices.

### `scenario_engine.py`
The chaos monkey.
-   **Responsibility**: Models grid outages (`OutageEvent`) and environmental shifts.
-   **Features**: Supports Partial, Full Blackout, and Cascading failure definitions. Determines `grid_available` status for each microgrid at every timestep.

### `resilience_metrics.py`
The evaluator.
-   **Responsibility**: Implements the mathematical formulas for the key performance indicators.
-   **Metrics**:
    -   **City Survivability Index**: Exponentially decaying score based on unserved energy and critical load survivability.
    -   **Critical Load Preservation Ratio**: Percentage of critical demand served during outages.
    -   **Priority Violation Penalty**: Weighted penalty for high-priority microgrids being forced to shed load while resources exist elsewhere (or low-priority ones are consuming grid power).

### `outage_event_model.py`
The definitions.
-   **Responsibility**: Simple dataclasses/enums defining `OutageType`, `OutageEvent`, and `ScenarioConfig`.

## 3. Coordinated Outage Simulation Flow

**Scenario**: 4-Hour City-Wide Blackout (Peak Load)
**Config**: `start_time=14:00`, `duration=4h`, `affected=[all]`

1.  **T=14:00 (Event Start)**: `ScenarioEngine` flags `grid_available=False` for all simulators.
2.  **Physical Response**: Simulators detect voltage/frequency loss. `LocalEMS` transition to `ISLANDED` mode.
3.  **Digital Twin Update**: `DigitalTwinManager` collects islanded status. Updates `TwinState.is_outage_active = True`.
4.  **City EMS Coordination**:
    -   Detects `microgrids_islanded = 4` (WIDESPREAD_OUTAGE mode).
    -   Applies **Priority Logic**:
        -   **Hospital (Crit)**: Command `max_shed_percent=0`.
        -   **University (High)**: Command `max_shed_percent=30`.
        -   **Residential (Low)**: Command `max_shed_percent=90`.
5.  **T=15:00 (Mid-Outage)**:
    -   **Hospital**: Running on Generator1 + PV. Critical loads 100% served.
    -   **Residential**: Battery draining fast. `LocalEMS` sheds AC/EV loads (per City command).
    -   **Twin Metrics**: `ResilienceCalculator` records 0 critical unserved energy, but high non-critical shedding (which is acceptable/penalized lightly).
6.  **T=18:00 (Restoration)**: `ScenarioEngine` flags `grid_available=True`.
7.  **Recovery**: Detects grid voltage. `LocalEMS` synchronize and reconnect. City EMS switches to `NORMAL` mode.

## 4. Methodology for Research Qualification

This implementation qualifies as a system-level Digital Twin based on the **Grieves definition** and **IEEE standards** for the following reasons:

1.  **Virtual Representation**: It maintains a high-fidelity virtual model (`TwinState`) that mathematically mirrors the physical assets (batteries, generators) and control logic (EMS) of the real system.
2.  **Shadow Simulation Capability**: Unlike a static simulator, the architecture supports "look-ahead" capability (via the `predict_impact` placeholder) and scenario injection that allows operators to test "what-if" resilience strategies non-intrusively.
3.  **Bi-Directional Data Flow (Emulated)**: The Manager mimics the SCADA/IoT data aggregation (Simulator -> Twin) and Supervisory Control (Twin -> CityEMS -> LocalEMS), representing the closed-loop feedback required for a Level-3 Digital Twin (Adaptive).
4.  **Priority-Aware State Estimation**: The state definition explicitly separates "Physical" and "Cyber" states, allowing the system to model cyber-physical resilience (e.g., checking if the EMS *decided* to shed load vs. if the *breaker* physically opened).
