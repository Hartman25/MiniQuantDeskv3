# tests/patch3/conftest.py
from __future__ import annotations

from dataclasses import dataclass


# Patch3 tests import this symbol:
@dataclass
class FakeRiskResult:
    approved: bool = True
    reason: str = "ok"
    approved_qty: object = None  # Backwards compatibility - optional quantity cap
    
    def to_dict(self):
        """Compatibility method for runtime"""
        return {
            "approved": self.approved,
            "reason": self.reason,
            "approved_qty": self.approved_qty
        }
