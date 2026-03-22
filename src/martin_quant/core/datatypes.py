from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from martin_quant.core.enums import ReviewLabelType, SetupType, TriggerType


@dataclass
class SetupSignal:
    symbol: str
    setup_type: SetupType
    timestamp: Any = None
    timeframe: str = "1d"

    direction: str = "long"
    score: float = 0.0

    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None

    trigger_level: float | None = None
    invalidation_level: float | None = None
    support_level: float | None = None
    resistance_level: float | None = None

    context: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "setup_type": self.setup_type.value,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "score": self.score,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "trigger_level": self.trigger_level,
            "invalidation_level": self.invalidation_level,
            "support_level": self.support_level,
            "resistance_level": self.resistance_level,
            "context": self.context,
            "notes": self.notes,
        }


@dataclass
class TriggerSignal:
    symbol: str
    timestamp: Any
    trigger_type: TriggerType
    timeframe: str

    direction: str = "long"
    score: float = 0.0

    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None

    linked_setup_type: SetupType | None = None
    context: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "trigger_type": self.trigger_type.value,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "score": self.score,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "linked_setup_type": self.linked_setup_type.value if self.linked_setup_type else None,
            "context": self.context,
            "notes": self.notes,
        }


@dataclass
class ReviewLabel:
    symbol: str
    timestamp: Any
    label: ReviewLabelType
    reason: str = ""
    reviewer: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "label": self.label.value,
            "reason": self.reason,
            "reviewer": self.reviewer,
            "metadata": self.metadata,
        }

