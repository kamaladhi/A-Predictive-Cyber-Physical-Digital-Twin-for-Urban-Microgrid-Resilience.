# Research Methodology: Co-optimized Digital Twin IEMS

This document formalizes the mathematical and algorithmic framework for the urban microgrid coordination system. It serves as the primary technical reference for publication-grade documentation.

## 1. System Architecture: The Triple-State Digital Twin

The framework is modeled as a **Triple-State Digital Twin (TS-DT)**, which tracks and synchronizes three distinct layers across the city-level coordination hierarchy:

1.  **Physical State ($\mathcal{S}_p$):** Real-time generation, storage, and load measurements (SOC, Power, Voltage).
2.  **Cyber State ($\mathcal{S}_c$):** Communication network availability (stochastic link failures), forecast uncertainty ($\sigma$), and sensor noise levels.
3.  **Decision State ($\mathcal{S}_d$):** Current control logic (Rule-based vs MPC), active resilience policies, and dynamic reserve margins.

### Synchronization Algorithm
The Virtual Twin ($\mathcal{V}$) is updated every cycle ($t$) using an **Active State Estimator (EKF)** to recover the latent state $x$ from noisy measurements $z$:
$$x_{t+1|t} = \Phi x_t + \Gamma u_t + \omega_t$$
$$z_t = H x_t + v_t$$
where $v_t$ represents the stochastic measurement noise injected to simulate real-world sensor inaccuracy.

---

## 2. Integrated Optimization Formulation

The core of the **Intelligent EMS (IEMS)** is a multi-objective Rolling-Horizon LP solved over horizon $H=8$ steps (2 hours).

### 2.1 Objective Function
$$\min \sum_{t=0}^{H-1} \sum_{i \in \mathcal{M}} \left[ \alpha C_{fuel} \cdot P_{gen, i, t} + \beta W_{i} \cdot P_{shed, i, t} + \gamma C_{batt} \cdot |P_{batt, i, t}| - I_{dr} \cdot P_{dr, i, t} + \lambda_{slack, i} \cdot S_{slack, i, t} \right]$$

**Term Priority Hierachy (Calibrated for Extreme Resilience):**
1. **Critical Load Preservation** ($\lambda_{slack}$ penalty = $10^5$ for Hospital)
2. **Priority-Aware Shedding** ($W_{hospital}=50$, $W_{univ}=10$, $W_{res}=1$)
3. **Economic Efficiency** Fuel cost vs DR Incentive

### 2.2 Key Constraints
*   **Power Balance**: $P_{pv} + P_{gen} + P_{batt} + P_{shared} = P_{load} - P_{shed} - P_{dr}$
*   **Cyber-Aware Sharing**: $P_{shared, i} = 0$ if $|Cyber\_State.link_i| = 0$ (Simulates comms outage)
*   **Uncertainty-Aware Reserve**: $SOC_{min, i} \geq f(Priority_i, \sigma_{forecast, i})$
    *   The margin scales linearly with forecast uncertainty $\sigma$ to provide a "Chance-Constrained" buffer against prediction error.
*   **Voluntary DR Limit**: $0 \leq P_{dr, i, t} \leq Target_{dr, i}$

---

## 3. Algorithmic Workflow

### Algorithm 1: IEMS-DR Co-optimization
1. **Perception**: Extract filtered states ($\hat{x}$) from the Digital Twin.
2. **Forecast**: Build $H$-step predictions for Solar ($P_{pv}$) and Load ($P_{load}$) using persistence + MC-Dropout uncertainty.
3. **DR Allocation**: Retrieve and allocate city-wide DR targets to microgrid voluntary bounds.
4. **Solve**: Execute Rolling-Horizon LP with slack-enabled feasibility.
5. **Dispatch**: Apply step $t=0$ commands to the reactive simulator.
6. **Recede**: Shift horizon and repeat.

---

## 4. Experimental Design (Monte Carlo)

To ensure statistical robustness and publication-grade validation:
* **Population**: 100+ Matched trials (same random seeds per configuration).
* **Duration**: 7-day to 30-day "Urban Stress" profiles.
* **Stressors**: Coordinated outages + Stochastic Cyber-Faults (10% prob).
* **Metrics**: ASAI, SAIDI, CAIDI, and EENS (IEEE 1366 compliant).

## 5. Sensitivity Analysis Protocol

Automated sweeps evaluate the robustness of the system across two critical dimensions:
1. **Resource Scarcity Sweep**: Scaling battery capacity (0.5x to 2.0x) to determine the threshold for critical load violation.
2. **Forecast Reliability Sweep**: Scaling $\sigma_{forecast}$ (0.05 to 0.40) to test the efficiency of the Chance-Constrained margin logic.
