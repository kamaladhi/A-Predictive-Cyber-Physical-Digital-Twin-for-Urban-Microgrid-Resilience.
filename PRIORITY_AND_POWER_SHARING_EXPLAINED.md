# Priority System & Power Sharing Explained

## 1. PRIORITY HIERARCHY

Your system uses a **4-tier priority hierarchy** based on criticality:

```
Tier 1: CRITICAL   ← Hospital (Life safety - cannot fail)
Tier 2: HIGH       ← University (Education, research continuity)
Tier 3: MEDIUM     ← Industrial (Economic activity, employment)
Tier 4: LOW        ← Residential (Comfort, convenience)
```

**Key Principle:** Higher-priority microgrids are protected at the expense of lower-priority ones during energy shortages.

---

## 2. HOW PRIORITY IS GIVEN (Load Shedding Algorithm)

### Step 1: Trigger Detection

The City-EMS **monitors the health of CRITICAL and HIGH priority microgrids**:

```python
# Check battery SOC of Hospital + University (highest priorities)
critical_high_min_soc = min(hospital.battery_soc, university.battery_soc)

# Based on their health, decide how much to shed citywide:
if critical_high_min_soc < 35%:
    required_shedding = 60%  # Severe - shed 60% of city load
elif critical_high_min_soc < 45%:
    required_shedding = 40%  # Moderate
elif critical_high_min_soc < 55%:
    required_shedding = 25%  # Light
else:
    required_shedding = 0%   # No shedding needed
```

**Why this approach?** 
- Don't shed based on a single microgrid's health
- Protect the highest-priority microgrids (Hospital + University)
- Force lower-priority ones to sacrifice for the city's critical infrastructure

---

### Step 2: Strict Tier-by-Tier Allocation

Once shedding is triggered, the City-EMS allocates it **from low priority to high priority**, never skipping tiers:

```
Required Shedding = 400 kW (example)

┌─────────────────────────────────┐
│ Try to get 400 kW from LOW tier  │  (Residential: 200 kW available)
└─────────────────────────────────┘
        ↓
    Got: 200 kW
    Remaining: 200 kW needed

┌─────────────────────────────────┐
│ Try to get 200 kW from MEDIUM    │  (Industrial: 250 kW available)
└─────────────────────────────────┘
        ↓
    Got: 200 kW
    Remaining: 0 kW
    ✅ DONE - NO need to touch HIGH or CRITICAL

If 400 kW was needed but MEDIUM had only 150 kW:
    Then 250 kW would be shed from LOW
    Then 150 kW would be shed from MEDIUM
    Then 0 kW needed from HIGH ← Protected!
```

### Step 3: Proportional Distribution Within Same Priority

If multiple microgrids share the same priority and that tier must be tapped:

```python
# For example, if multiple residential areas are at LOW priority:
Residential A: 300 kW load
Residential B: 200 kW load
Total LOW tier: 500 kW load

# If we need 100 kW from LOW tier, distribute proportionally:
Residential A: 100 kW × (300/500) = 60 kW shed
Residential B: 100 kW × (200/500) = 40 kW shed
```

---

## 3. EXCESS POWER SHARING

### Current Architecture: STAR TOPOLOGY (Centralized)

Your system uses a **centralized star topology** where the City-EMS coordinates all power sharing:

```
                    ┌─────────────┐
                    │  CITY-EMS   │  (Centralized Coordinator)
                    │  (Supervisor)
                    └──────┬──────┘
          ┌─────────────────┼─────────────────┐
          │                 │                 │
     ┌────▼────┐       ┌───▼────┐       ┌──▼─────┐
     │ Hospital │       │University    │ Industrial
     │ (CRITICAL)       │ (HIGH)       │ (MEDIUM)
     └──────────┘       └─────────┘   └──────────┘
          │
     ┌────▼─────┐
     │ Residential│
     │ (LOW)     │
     └───────────┘
```

### How Excess Power Flows:

**Scenario: Hospital has excess solar power at noon**

```
Hospital Status:
├─ Solar generation: 500 kW ☀️
├─ Current load: 300 kW
└─ Excess power: 200 kW

City-EMS Decision Logic:
1. Check if other microgrids need power
2. Prioritize by: University > Industrial > Residential
3. Only if University/Industrial/Residential are under-resourced
```

**Example Allocation:**

```
Hospital has 200 kW excess

┌─────────────────────┐
│ Can University use? │  Yes, needs 100 kW
│ (Highest after me)  │  ✓ Send 100 kW
└─────────────────────┘
        Remaining: 100 kW

┌─────────────────────┐
│ Can Industrial use? │  Yes, needs 50 kW
│ (Next highest)      │  ✓ Send 50 kW
└─────────────────────┘
        Remaining: 50 kW

┌─────────────────────┐
│ Can Residential use?│  Yes, needs 50 kW
│ (Lowest priority)   │  ✓ Send 50 kW
└─────────────────────┘
        Remaining: 0 kW ✅ All distributed
```

---

## 4. KEY FEATURES OF YOUR PRIORITY SYSTEM

### ✅ What Your System Enforces

| Feature | Implementation |
|---------|-----------------|
| **Never shed critical loads** | CRITICAL/HIGH priorities always keep 100% critical load |
| **Strict tier ordering** | Always shed LOW→MEDIUM→HIGH, NEVER skip |
| **Protect CRITICAL first** | Hospital (CRITICAL) cannot have load shed unless unavoidable |
| **Proportional within tier** | If multiple microgrids at same priority, distribute shed proportionally |
| **Smart trigger** | Shedding triggered by health of CRITICAL/HIGH, not just any shortage |
| **Quantified allocation** | Specific kW targets, not vague "reduce usage" commands |

### 📊 Example: Why Your System Has 0 Violations

**Violation Definition:** "Lower priority MG sheds while higher priority MG has spare capacity"

```
Your algorithm prevents this:

CRITICAL (Hospital) at 35% SOC ← Needs protection
  ↓
Trigger shedding from LOW tier
  ↓
Check if LOW has 60% shedding capacity → Yes
  ↓
Shed from LOW first, ONLY then from MEDIUM
  ↓
CRITICAL protected while lower priorities sacrifice
  ↓
Result: 0 violations ✅
```

---

## 5. RESILIENCE POLICY MODES

Your City-EMS supports different **priority weighting strategies**:

### CRITICAL_FIRST (Current: 3 scenarios tested)
```
Hospital (CRITICAL): Weight 1.0  ← Fully protected
University (HIGH):   Weight 0.7  ← 70% as important
Industrial (MEDIUM): Weight 0.4  ← 40% as important  
Residential (LOW):   Weight 0.2  ← 20% as important

Effect: Hospital gets max resources, residents shed first
```

### BALANCED
```
Hospital (CRITICAL): Weight 1.0
University (HIGH):   Weight 0.85 ← More balanced distribution
Industrial (MEDIUM): Weight 0.70
Residential (LOW):   Weight 0.55

Effect: More equitable, but still hospital-first
```

### EQUITABLE
```
All microgrids: Weight 1.0 ← Equal treatment
Effect: Fair sharing, but loses critical infrastructure protection
```

### ECONOMIC
```
Hospital (CRITICAL): Weight 1.0
Industrial (MEDIUM): Weight 0.9  ← Economic activity prioritized
University (HIGH):   Weight 0.7
Residential (LOW):   Weight 0.3

Effect: Maximize economic output while protecting lives
```

---

## 6. POWER SHARING IN YOUR RESULTS

### Normal Operation Scenario
```
All microgrids running on grid + renewables
├─ Hospital: Solar + Grid-powered
├─ University: Solar + Grid-powered
├─ Industrial: Grid-powered
└─ Residential: Grid-powered
    
Power Flow: Minimal inter-microgrid sharing (connected to main grid)
City Survivability Index: 1.0000 ✅ Perfect
```

### 6-Hour Outage Scenario
```
Grid goes down (6h outage) → All microgrids islanded

Time 0-2h:
├─ Hospital: Running on battery (critical load maintained)
├─ University: Running on battery (critical load maintained)
├─ Industrial: Running on battery + reduced load
└─ Residential: Running on battery + reduced load
    
Power Flows: Within-microgrid only (no inter-grid sharing)

Time 2-4h (Battery depleting):
├─ Hospital: <40% SOC detected
│   ↓
│   City-EMS triggers: Shed 60% from LOW+MEDIUM tier
├─ Industrial: 60% shedding allocated
└─ Residential: 40% shedding allocated
    
Result: Hospital's battery preserved, industrial/residential sacrifice
City Survivability Index: 1.0000 ✅ All critical loads survived

Time 4-6h:
├─ Hospital: Still ~30% SOC (survived with critical load 100%)
├─ University: Still ~45% SOC (survived with critical load 100%)
├─ Industrial: ~15% SOC (shed non-critical loads)
└─ Residential: ~5% SOC (shed non-critical loads)

City Critical Load Preservation: 100% ✅ All critical loads intact
```

### 12-Hour Outage (Stress Test)
```
Same logic applies for 12h - even longer stress.

City-EMS continuously monitors SOC health:
├─ If Hospital/University drop below 55% → start moderate shedding
├─ If they drop below 45% → increase shedding
├─ If they drop below 35% → maximum shedding
│
Result: Critical loads survive, lower-priority loads bear the burden
Priority Violations: 0 ✅ No lower-priority MG shedding while CRITICAL has spare capacity
```

---

## 7. ACTUAL CODE LOCATIONS IN YOUR PROJECT

| Concept | File | Lines |
|---------|------|-------|
| Priority definitions | `EMS/city_ems.py` | 56-62 |
| Shedding trigger logic | `EMS/city_ems.py` | 820-835 |
| Tier-by-tier allocation | `EMS/city_ems.py` | 836-880 |
| City operation modes | `EMS/city_ems.py` | 65-72 |
| Microgrid registration | `EMS/city_ems.py` | 368-375 |
| Main update loop | `EMS/city_ems.py` | 380-415 |

---

## 8. SUMMARY

### Priority System
- **4 tiers**: CRITICAL (Hospital) → HIGH (University) → MEDIUM (Industrial) → LOW (Residential)
- **Trigger**: Based on SOC health of CRITICAL/HIGH priority microgrids
- **Allocation**: Strict LOW→MEDIUM→HIGH, never skip, proportional within tier

### Power Sharing
- **Architecture**: Centralized City-EMS coordinator (star topology)
- **Direction**: Hospital excess → University → Industrial → Residential (priority order)
- **Constraint**: Higher priorities always protected by shedding lower priorities
- **Result**: 0 priority violations, 100% critical load preservation, perfect resilience metrics

### Why It Works
1. **Monitoring** → City-EMS watches health of critical microgrids
2. **Triggering** → When they struggle, shedding is invoked
3. **Allocating** → Shed from low priorities first, respect tier ordering
4. **Protecting** → Critical loads (hospital) never sacrificed for comfort loads (residential)
5. **Sharing** → Excess from high-value sources flows in priority order to those who need it

This is the **essence of resilience engineering**: Protect the most critical functions by sacrificing less critical ones during stress.
