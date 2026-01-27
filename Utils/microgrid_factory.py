"""
Microgrid Factory - Load any of 4 heterogeneous microgrids
"""
import sys
import os
from typing import Tuple, Dict, Any
import logging

logger = logging.getLogger(__name__)


class MicrogridType:
    """Supported microgrid types"""
    HOSPITAL = "hospital"
    UNIVERSITY = "university"
    RESIDENCE = "residence"
    INDUSTRIAL = "industrial"
    ALL = [HOSPITAL, UNIVERSITY, RESIDENCE, INDUSTRIAL]


class MicrogridFactory:
    """Factory pattern to load any microgrid type with consistent interface"""
    
    @staticmethod
    def load_config(microgrid_type: str):
        """Load configuration for specified microgrid"""
        microgrid_type = microgrid_type.lower()
        
        if microgrid_type == MicrogridType.HOSPITAL:
            from Microgrid.Hospital.parameters import create_default_config
            return create_default_config()
            
        elif microgrid_type == MicrogridType.UNIVERSITY:
            from Microgrid.university_microgrid.parameters import create_default_config
            return create_default_config()
            
        elif microgrid_type == MicrogridType.RESIDENCE:
            from Microgrid.residence.residential_parameters import create_default_config
            return create_default_config()
            
        elif microgrid_type == MicrogridType.INDUSTRIAL:
            from Microgrid.Industry_microgrid.industrial_parameters import create_default_config
            return create_default_config()
        
        raise ValueError(f"Unknown microgrid type: {microgrid_type}")
    
    @staticmethod
    def load_simulator(microgrid_type: str, config):
        """Load simulator for specified microgrid"""
        microgrid_type = microgrid_type.lower()
        
        if microgrid_type == MicrogridType.HOSPITAL:
            from Microgrid.Hospital.hospital_simulator import MicrogridSimulator
            return MicrogridSimulator(config)
            
        elif microgrid_type == MicrogridType.UNIVERSITY:
            from Microgrid.university_microgrid.university_simulator import MicrogridSimulator
            return MicrogridSimulator(config)
            
        elif microgrid_type == MicrogridType.RESIDENCE:
            from Microgrid.residence.residential_simulator import MicrogridSimulator
            return MicrogridSimulator(config)
            
        elif microgrid_type == MicrogridType.INDUSTRIAL:
            from Microgrid.Industry_microgrid.industrial_simulator import MicrogridSimulator
            return MicrogridSimulator(config)
        
        raise ValueError(f"Unknown microgrid type: {microgrid_type}")
    
    @staticmethod
    def get_metadata(microgrid_type: str) -> Dict[str, Any]:
        """Get metadata about microgrid type (priority, facility name, etc.)"""
        metadata = {
            MicrogridType.HOSPITAL: {
                'priority': 1,
                'name': 'City Hospital',
                'type_desc': '500-bed Teaching Hospital',
                'peak_load_kw': 900,
                'critical_load_kw': 320,
            },
            MicrogridType.UNIVERSITY: {
                'priority': 2,
                'name': 'City University',
                'type_desc': 'Multi-campus University',
                'peak_load_kw': 1200,
                'critical_load_kw': 200,
            },
            MicrogridType.RESIDENCE: {
                'priority': 3,
                'name': 'Green Valley Residences',
                'type_desc': '400-Apartment Community',
                'peak_load_kw': 650,
                'critical_load_kw': 100,
            },
            MicrogridType.INDUSTRIAL: {
                'priority': 4,
                'name': 'Auto Manufacturing Plant',
                'type_desc': 'Industrial Manufacturing',
                'peak_load_kw': 850,
                'critical_load_kw': 220,
            }
        }
        
        return metadata.get(microgrid_type.lower(), {})
