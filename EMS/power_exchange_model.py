"""
Power Exchange Model
Handles inter-microgrid power flows with transmission losses and constraints.
"""
from dataclasses import dataclass
from typing import Dict, Tuple
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


@dataclass
class TransmissionLine:
    """Represents a transmission line between two microgrids."""
    from_mg: str
    to_mg: str
    capacity_kw: float
    loss_percent: float = 2.0  # Typical 2% loss per transfer
    
    def apply_loss(self, power_kw: float) -> float:
        """Calculate received power after transmission loss."""
        return power_kw * (1 - self.loss_percent / 100)


class PowerExchangeModel:
    """
    Models power exchanges between microgrids with:
    - Transmission line constraints (capacity)
    - Losses (typically 1-5%)
    - Voltage support bounds
    """
    
    def __init__(self):
        """Initialize exchange model."""
        self.lines: Dict[Tuple[str, str], TransmissionLine] = {}
        self.active_transfers: Dict[Tuple[str, str], float] = {}
        logger.info("✓ Power Exchange Model initialized")
    
    def add_transmission_line(self, from_mg: str, to_mg: str, 
                             capacity_kw: float, loss_percent: float = 2.0):
        """Register a transmission line between two microgrids."""
        key = (from_mg, to_mg)
        self.lines[key] = TransmissionLine(from_mg, to_mg, capacity_kw, loss_percent)
        logger.info(f"✓ Line {from_mg} → {to_mg}: {capacity_kw} kW, {loss_percent}% loss")
    
    def validate_transfer(self, from_mg: str, to_mg: str, power_kw: float) -> Tuple[bool, str]:
        """
        Validate if a power transfer is feasible.
        
        Returns:
            (is_feasible, reason)
        """
        key = (from_mg, to_mg)
        
        if key not in self.lines:
            return False, f"No transmission line from {from_mg} to {to_mg}"
        
        line = self.lines[key]
        
        if power_kw > line.capacity_kw:
            return False, f"Power {power_kw:.1f} kW exceeds capacity {line.capacity_kw} kW"
        
        if power_kw < 0:
            return False, "Power must be non-negative"
        
        return True, "OK"
    
    def execute_transfer(self, from_mg: str, to_mg: str, power_kw: float) -> Dict[str, float]:
        """
        Execute a power transfer and return actual flows (with losses).
        
        Returns:
            {
                'requested_kw': power_kw,
                'sent_kw': power_kw (from source),
                'received_kw': power_kw * (1 - loss%),
                'loss_kw': loss_amount
            }
        """
        is_valid, reason = self.validate_transfer(from_mg, to_mg, power_kw)
        
        if not is_valid:
            logger.warning(f"❌ Transfer invalid: {reason}")
            return {
                'requested_kw': power_kw,
                'sent_kw': 0,
                'received_kw': 0,
                'loss_kw': 0,
                'valid': False,
                'reason': reason
            }
        
        line = self.lines[(from_mg, to_mg)]
        received = line.apply_loss(power_kw)
        loss = power_kw - received
        
        self.active_transfers[(from_mg, to_mg)] = power_kw
        
        logger.info(f"✓ Transfer {from_mg} → {to_mg}: Sent {power_kw:.1f} kW, "
                   f"Received {received:.1f} kW, Loss {loss:.1f} kW ({line.loss_percent}%)")
        
        return {
            'requested_kw': power_kw,
            'sent_kw': power_kw,
            'received_kw': received,
            'loss_kw': loss,
            'valid': True,
            'reason': 'OK'
        }
    
    def clear_transfer(self, from_mg: str, to_mg: str):
        """Stop a power transfer."""
        key = (from_mg, to_mg)
        if key in self.active_transfers:
            del self.active_transfers[key]
    
    def get_active_transfers(self) -> Dict[Tuple[str, str], float]:
        """Get all active transfers."""
        return self.active_transfers.copy()
    
    def get_exchange_summary(self) -> Dict:
        """Get summary of power exchanges."""
        total_sent = sum(self.active_transfers.values())
        
        summary = {
            'active_transfers': len(self.active_transfers),
            'total_power_sent_kw': total_sent,
            'transfers': {
                f"{k[0]}_to_{k[1]}": v for k, v in self.active_transfers.items()
            }
        }
        
        return summary
