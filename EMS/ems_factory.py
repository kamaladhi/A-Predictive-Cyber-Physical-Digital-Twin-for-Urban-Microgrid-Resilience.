"""
EMS Factory - Connects each microgrid type to its respective EMS

This factory provides centralized access to all 4 local EMS implementations:
- Hospital EMS
- Industrial EMS  
- Residential EMS
- University EMS

Each microgrid gets its own specialized EMS instance with appropriate
control logic tailored to its load profile and criticality.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class EMSType:
    """Supported EMS types matching microgrid types"""
    HOSPITAL = "hospital"
    INDUSTRIAL = "industrial"
    RESIDENCE = "residence"
    UNIVERSITY = "university"
    ALL = [HOSPITAL, INDUSTRIAL, RESIDENCE, UNIVERSITY]


class EMSFactory:
    """Factory to create appropriate EMS for each microgrid type"""
    
    @staticmethod
    def create_ems(microgrid_type: str, config, battery, pv, generator1, generator2, load):
        """
        Create EMS instance for specified microgrid type
        
        Args:
            microgrid_type: Type of microgrid (hospital, industrial, residence, university)
            config: Microgrid configuration object
            battery: Battery component instance
            pv: PV array component instance
            generator1: Primary generator instance
            generator2: Secondary generator instance (may be dummy for some types)
            load: Load component instance
            
        Returns:
            EnergyManagementSystem instance configured for the microgrid type
        """
        microgrid_type = microgrid_type.lower()
        
        if microgrid_type == EMSType.HOSPITAL:
            from EMS.hospital_ems import HospitalEMS
            ems = HospitalEMS(config, battery, pv, generator1, generator2, load)
            logger.info(f"✓ Created Hospital EMS - Priority: Critical load protection (320 kW)")
            return ems
            
        elif microgrid_type == EMSType.INDUSTRIAL:
            from EMS.industry_ems import IndustryEMS
            ems = IndustryEMS(config, battery, pv, generator1, generator2, load)
            logger.info(f"✓ Created Industrial EMS - Priority: Manufacturing continuity")
            return ems
            
        elif microgrid_type == EMSType.RESIDENCE:
            from EMS.residence_ems import ResidenceEMS
            ems = ResidenceEMS(config, battery, pv, generator1, generator2, load)
            logger.info(f"✓ Created Residential EMS - Priority: Survival over comfort")
            return ems
            
        elif microgrid_type == EMSType.UNIVERSITY:
            from EMS.university_ems import UniversityEMS
            ems = UniversityEMS(config, battery, pv, generator1, generator2, load)
            logger.info(f"✓ Created University EMS - Priority: Education & research continuity")
            return ems
        
        raise ValueError(f"Unknown microgrid type: {microgrid_type}. "
                        f"Supported types: {EMSType.ALL}")
    
    @staticmethod
    def get_ems_metadata(microgrid_type: str) -> dict:
        """Get metadata about EMS configuration for each microgrid type"""
        metadata = {
            EMSType.HOSPITAL: {
                'name': 'Hospital EMS',
                'priority': 1,
                'critical_load_protection': True,
                'critical_load_kw': 320,
                'generator_strategy': 'dual_tier',
                'load_shedding': 'conservative',
            },
            EMSType.UNIVERSITY: {
                'name': 'University EMS',
                'priority': 2,
                'critical_load_protection': True,
                'critical_load_kw': 150,
                'generator_strategy': 'dual_tier',
                'load_shedding': 'moderate',
            },
            EMSType.RESIDENCE: {
                'name': 'Residential EMS',
                'priority': 3,
                'critical_load_protection': False,
                'critical_load_kw': 0,
                'generator_strategy': 'single',
                'load_shedding': 'aggressive',
            },
            EMSType.INDUSTRIAL: {
                'name': 'Industrial EMS',
                'priority': 4,
                'critical_load_protection': True,
                'critical_load_kw': 200,
                'generator_strategy': 'dual_tier',
                'load_shedding': 'moderate',
            },
        }
        
        microgrid_type = microgrid_type.lower()
        if microgrid_type not in metadata:
            raise ValueError(f"Unknown microgrid type: {microgrid_type}")
        
        return metadata[microgrid_type]


# Convenience function for quick EMS creation
def create_ems_for_microgrid(microgrid_type: str, config, battery, pv, 
                              generator1, generator2, load):
    """
    Convenience function to create EMS instance
    
    Usage:
        from EMS.ems_factory import create_ems_for_microgrid
        
        ems = create_ems_for_microgrid('hospital', config, battery, pv, gen1, gen2, load)
    """
    return EMSFactory.create_ems(microgrid_type, config, battery, pv, 
                                  generator1, generator2, load)
