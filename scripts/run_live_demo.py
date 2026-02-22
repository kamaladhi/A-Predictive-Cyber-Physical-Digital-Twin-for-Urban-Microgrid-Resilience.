"""
Live Demonstration Orchestrator: IoT + Digital Twin Dashboard
This script launches the MQTT broker, the Streamlit UI, and the 
real-time simulation loop simultaneously.
"""

import subprocess
import time
import sys
import os
import signal

def main():
    print("="*60)
    print("  DIGITAL TWIN LIVE DEMONSTRATION ORCHESTRATOR  ")
    print("="*60)

    # 1. Start MQTT Broker
    print("\n[1/3] Starting MQTT Broker...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    try:
        mqtt_path = os.path.join(script_dir, "start_mqtt.ps1")
        mqtt_proc = subprocess.Popen(["powershell.exe", "-File", mqtt_path])
        time.sleep(3) # Wait for broker to initialize
    except Exception as e:
        print(f"Error starting MQTT: {e}")

    # 2. Start Streamlit Dashboard
    print("\n[2/3] Launching Live Dashboard (Streamlit)...")
    try:
        # We start it in a separate process
        app_path = os.path.join(root_dir, "dashboard", "app.py")
        ui_proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", app_path, "--server.port", "8501"])
        print("Dashboard available at: http://localhost:8501")
        time.sleep(5) # Wait for UI to bundle
    except Exception as e:
        print(f"Error starting Dashboard: {e}")

    # 3. Start Live Simulation
    print("\n[3/3] Starting Simulation with IoT Sync (1 sim min = 1 real sec)...")
    try:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--outage', action='store_true')
        parser.add_argument('--shortage', action='store_true')
        args, _ = parser.parse_known_args()

        # Run a 3-day simulation in real-time mode
        exp_path = os.path.join(script_dir, "run_experiment.py")
        sim_cmd = [
            sys.executable, 
            exp_path, 
            "--trials", "1", 
            "--days", "3.0",
            "--mqtt",
            "--realtime", "60.0" # 60x speed (1 min per sec)
        ]
        
        if args.outage:
            sim_cmd.append("--force-outage")
            print(">>> DEMO MODE: FORCED PERMANENT BLACKOUT ENABLED <<<")
        if args.shortage:
            sim_cmd.append("--force-shortage")
            print(">>> DEMO MODE: FORCED POWER SHORTAGE ENABLED <<<")
        
        print(f"Executing: {' '.join(sim_cmd)}")
        sim_proc = subprocess.Popen(sim_cmd)
        
        print("\n" + "!"*40)
        print(" LIVE SYSTEM RUNNING ")
        print(" Press Ctrl+C to terminate all components ")
        print("!"*40 + "\n")
        
        # Keep alive until interrupted
        sim_proc.wait()

    except KeyboardInterrupt:
        print("\nShutdown requested...")
    finally:
        print("Cleaning up processes...")
        try:
            sim_proc.terminate()
            ui_proc.terminate()
            mqtt_proc.terminate()
        except:
            pass
        print("Done.")

if __name__ == "__main__":
    main()
