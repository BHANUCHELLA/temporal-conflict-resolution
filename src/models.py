"""
Core data models used throughout the reconciliation engine.

- TransactionEvent   : a single incoming record, as parsed from JSON input.
- TransactionState   : the current, authoritative, versioned state of a transaction.
- ReconciliationResult: everything that happened while processing one event.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any


@dataclass
class TransactionEvent:
    transaction_id: str
    source: Optional[str] = None
    timestamp: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    status: Optional[str] = None
    reconciliation_notes: Optional[str] = None
    confidence_score: Optional[float] = None
    event_sequence: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TransactionState:
    transaction_id: str
    version: int
    source: Optional[str] = None
    timestamp: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    status: Optional[str] = None
    reconciliation_notes: Optional[str] = None
    confidence_score: Optional[float] = None
    event_sequence: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReconciliationResult:
    transaction_id: str
    final_state: Optional[TransactionState]
    conflicts_detected: List[Dict[str, Any]] = field(default_factory=list)
    resolutions_applied: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    is_duplicate: bool = False
    is_new: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "final_state": self.final_state.to_dict() if self.final_state else None,
            "conflicts_detected": self.conflicts_detected,
            "resolutions_applied": self.resolutions_applied,
            "warnings": self.warnings,
            "is_duplicate": self.is_duplicate,
            "is_new": self.is_new,
        }
