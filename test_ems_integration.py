"""
Test EMS Integration - Verify all 4 microgrids connect to their EMS correctly

This script tests that:
1. Each microgrid type can load its configuration
2. Each EMS can be instantiated correctly
3. All imports work properly
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Utils.microgrid_factory import MicrogridFactory, MicrogridType
from EMS.ems_factory import EMSFactory, create_ems_for_microgrid
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_microgrid_ems_integration():
    """Test that all 4 microgrids connect properly to their EMS"""
    
    print("\n" + "="*70)
    print("TESTING MICROGRID-EMS INTEGRATION")
    print("="*70 + "\n")
    
    results = {}
    
    for mg_type in MicrogridType.ALL:
        print(f"\n{'─'*70}")
        print(f"Testing: {mg_type.upper()} Microgrid")
        print(f"{'─'*70}")
        
        try:
            # Step 1: Load microgrid configuration
            print(f"  [1/3] Loading {mg_type} configuration...")
            config = MicrogridFactory.load_config(mg_type)
            print(f"        ✓ Configuration loaded")
            
            # Step 2: Create simulator (includes components)
            print(f"  [2/3] Creating {mg_type} simulator with components...")
            simulator = MicrogridFactory.load_simulator(mg_type, config)
            print(f"        ✓ Simulator created")
            
            # Step 3: Verify EMS connection
            print(f"  [3/3] Verifying EMS connection...")
            if hasattr(simulator, 'ems'):
                ems = simulator.ems
                print(f"        ✓ EMS connected: {type(ems).__name__}")
                
                # Check EMS metadata
                metadata = EMSFactory.get_ems_metadata(mg_type)
                print(f"        → EMS Name: {metadata['name']}")
                print(f"        → Priority Level: {metadata['priority']}")
                print(f"        → Critical Load Protection: {metadata['critical_load_protection']}")
                print(f"        → Generator Strategy: {metadata['generator_strategy']}")
                print(f"        → Load Shedding: {metadata['load_shedding']}")
                
                results[mg_type] = "✓ PASS"
            else:
                print(f"        ✗ No EMS attribute found in simulator")
                results[mg_type] = "✗ FAIL - No EMS"
                
        except Exception as e:
            print(f"        ✗ ERROR: {str(e)}")
            results[mg_type] = f"✗ FAIL - {str(e)[:50]}"
    
    # Summary
    print("\n" + "="*70)
    print("INTEGRATION TEST RESULTS")
    print("="*70 + "\n")
    
    for mg_type, result in results.items():
        status = "✓" if "PASS" in result else "✗"
        print(f"  {status} {mg_type.upper():12} - {result}")
    
    total = len(results)
    passed = sum(1 for r in results.values() if "PASS" in r)
    
    print(f"\n{'─'*70}")
    print(f"  Total: {passed}/{total} microgrids successfully connected to EMS")
    print(f"{'─'*70}\n")
    
    if passed == total:
        print("✅ ALL MICROGRIDS SUCCESSFULLY CONNECTED TO THEIR EMS!\n")
        return True
    else:
        print("⚠️  SOME MICROGRIDS FAILED TO CONNECT TO EMS\n")
        return False


if __name__ == "__main__":
    success = test_microgrid_ems_integration()
    sys.exit(0 if success else 1)
