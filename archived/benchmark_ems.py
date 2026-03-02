import subprocess
import os
import sys
import time
import logging

# Configure logic-level logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

def run_benchmark(trials=30, days=30, seed=42):
    """
    Executes the comprehensive 3x30 benchmark suite for the Digital Twin EMS research.
    Runs: Rule-Based, MPC (Statistical), and MPC (LSTM) configurations.
    """
    results_dir = "results/final_benchmark"
    os.makedirs(results_dir, exist_ok=True)
    
    # Define experimental configurations
    configs = [
        {
            "name": "Statistical_MPC",
            "cmd": [
                sys.executable, "scripts/run_experiment.py",
                "--trials", str(trials),
                "--days", str(days),
                "--seed", str(seed),
                "--policy", "critical_first",
                "--no-lstm",
                "--outdir", results_dir
            ]
        },
        {
            "name": "LSTM_MPC",
            "cmd": [
                sys.executable, "scripts/run_experiment.py",
                "--trials", str(trials),
                "--days", str(days),
                "--seed", str(seed),
                "--policy", "critical_first",
                "--outdir", results_dir
            ]
        }
    ]
    
    logger.info("================================================================================")
    logger.info("DIGITAL TWIN EMS: PREDICTIVE RESILIENCE BENCHMARK")
    logger.info(f"Trials: {trials} | Duration: {days} days | Seed: {seed}")
    logger.info("================================================================================")
    
    for config in configs:
        name = config["name"]
        cmd = config["cmd"]
        
        log_file = os.path.join(results_dir, f"benchmark_{name.lower()}.log")
        logger.info(f"Starting experiment: {name}")
        logger.info(f"Command: {' '.join(cmd)}")
        logger.info(f"Logging to: {log_file}")
        
        start_time = time.time()
        try:
            with open(log_file, "w") as f:
                process = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
                process.wait()
            
            elapsed = (time.time() - start_time) / 60
            if process.returncode == 0:
                logger.info(f"SUCCESS: {name} completed in {elapsed:.2f} minutes")
            else:
                logger.error(f"FAILURE: {name} exited with code {process.returncode}")
        except Exception as e:
            logger.error(f"CRITICAL ERROR running {name}: {str(e)}")

    logger.info("================================================================================")
    logger.info("BENCHMARK COMPLETE. Analysis files saved in results/final_benchmark/")
    logger.info("================================================================================")

if __name__ == "__main__":
    # Default to 1 day/1 trial for quick verification unless passed via args
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Run full 30x30 benchmark")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()
    
    if args.full:
        run_benchmark(trials=30, days=30)
    else:
        run_benchmark(trials=args.trials, days=args.days)
