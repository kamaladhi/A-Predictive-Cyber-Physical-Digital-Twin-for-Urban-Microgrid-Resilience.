"""
Digital Twin Framework for Heterogeneous Urban Microgrids

Thesis: "We propose a Digital Twin–based coordination framework for heterogeneous 
urban microgrids that enforces priority-aware resilience policies and demonstrably 
improves city-level survivability during grid outages."

Architecture:
- MicrogridFactory: Load any of 4 microgrids (Hospital, University, Residence, Industrial)
- DigitalTwin: Virtual model of each microgrid
- Coordinator: City-level coordination with priority enforcement
- CityMetrics: Track survivability and resilience metrics
"""

__version__ = "1.0"
__author__ = "Digital Twin Framework"
