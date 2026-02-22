"""
EMS Package - Energy Management Systems for all microgrids

This package contains local EMS implementations for each microgrid type:
- HospitalEMS - Critical load protection (320 kW always protected)
- IndustryEMS - Manufacturing continuity  
- ResidenceEMS - Survival over comfort (aggressive load shedding)
- UniversityEMS - Education & research continuity

Usage:
    from src.ems.ems_factory import create_ems_for_microgrid
    
    ems = create_ems_for_microgrid('hospital', config, battery, pv, gen1, gen2, load)
    
Or import specific EMS:
    from src.ems.hospital_ems import HospitalEMS
    from src.ems.industry_ems import IndustryEMS
    from src.ems.residence_ems import ResidenceEMS
    from src.ems.university_ems import UniversityEMS
"""

from src.ems.ems_factory import EMSFactory, EMSType, create_ems_for_microgrid

__all__ = [
    'EMSFactory',
    'EMSType', 
    'create_ems_for_microgrid',
]
