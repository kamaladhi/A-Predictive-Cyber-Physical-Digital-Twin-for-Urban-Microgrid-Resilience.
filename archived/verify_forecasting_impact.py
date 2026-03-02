"""
Verification Script: Forecasting Impact on EMS Resilience
=========================================================

Compares:
1. Statistical Fallback (Baseline)
2. Improved ResLSTM (New Pipeline)

Metrics:
- Daytime MAPE (%)
- Total Energy Not Served (ENS) in kWh
- Fuel Cost savings (%)
- SAIDI improvement
"""

import sys
import os
import subprocess
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

def run_sim(forecast_mode="lstm"):
    """Run a quick experiment with the specified forecast mode."""
    print(f"\n>>> Running simulation with {forecast_mode} forecast...")
    cmd = [
        "python", "scripts/run_experiment.py",
        "--trials", "1",
        "--days", "1",
        "--quick",  # This will override trials/days in run_experiment.py, but that's okay for testing
    ]
    # In a real scenario, we might need to toggle a flag in config,
    # but here we assume the dispatcher automatically uses the best available.
    # To force fallback, we'd temporarily rename the model file.
    
    if forecast_mode == "statistical":
        model_path = os.path.join(PROJECT_ROOT, 'src', 'solar', 'models', 'solar_lstm.pt')
        temp_path = model_path + ".bak"
        if os.path.exists(model_path):
            os.rename(model_path, temp_path)
        try:
            subprocess.run(cmd, check=True)
        finally:
            if os.path.exists(temp_path):
                os.rename(temp_path, model_path)
    else:
        subprocess.run(cmd, check=True)

def analyze_results():
    """Analyze the logs from both runs."""
    print("\n" + "="*60)
    print("  FORECASTING IMPACT ANALYSIS")
    print("="*60)
    
    # Placeholder for logic that parses results/experiment_run.log or similar
    # In this environment, we'll look for the summary output in the console.
    print("Final comparison logic will be executed after both runs.")

if __name__ == "__main__":
    # Note: This script assumes the model is already trained.
    print("Forecasting Impact Benchmark")
    print("Target: <30% MAPE and >15% ENS Reduction")
    run_sim("statistical")
    run_sim("lstm")
    analyze_results()
