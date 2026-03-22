from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class CandidateSignal:
    symbol: str
    direction: str
    setup_type: str
    regime: str
    total_score: float
    as_of: str
    source: str = "scan_v2"
    score: float = 0.0
    sector: str = ""
    timeframe: str = "1d"
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    trigger_type: str = ""
    entry_note: str = ""
    confirmation_mode: str = ""
    confirmation_bars: int = 0
    confirmation_reason: str = ""
    weekly_trend_state: str = ""
    gap_label: str = ""
    notes: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "setup_type": self.setup_type,
            "regime": self.regime,
            "sector": self.sector,
            "score": round(float(self.score), 3),
            "total_score": round(float(self.total_score), 3),
            "timeframe": self.timeframe,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "trigger_type": self.trigger_type,
            "entry_note": self.entry_note,
            "confirmation_mode": self.confirmation_mode,
            "confirmation_bars": self.confirmation_bars,
            "confirmation_reason": self.confirmation_reason,
            "weekly_trend_state": self.weekly_trend_state,
            "gap_label": self.gap_label,
            "notes": self.notes,
            "context": self.context,
            "source": self.source,
            "as_of": self.as_of,
        }


@dataclass
class SignalEvent:
    event_id: str
    received_at: str
    source: str
    symbol: str
    direction: str
    setup_type: str
    trigger_type: str = ""
    timeframe: str = ""
    price: float | None = None
    status: str = "received"
    notes: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any], source: str = "webhook") -> "SignalEvent":
        price = payload.get("price") or payload.get("close") or payload.get("entry_price")
        try:
            parsed_price = float(price) if price not in (None, "") else None
        except (TypeError, ValueError):
            parsed_price = None
        return cls(
            event_id=str(payload.get("event_id") or uuid4()),
            received_at=str(payload.get("received_at") or utc_now_iso()),
            source=str(payload.get("source") or source),
            symbol=str(payload.get("symbol") or payload.get("ticker") or "").upper(),
            direction=str(payload.get("direction") or "long").lower(),
            setup_type=str(payload.get("setup") or payload.get("setup_type") or "").lower(),
            trigger_type=str(payload.get("trigger") or payload.get("trigger_type") or "").lower(),
            timeframe=str(payload.get("timeframe") or payload.get("interval") or ""),
            price=parsed_price,
            status=str(payload.get("status") or "received"),
            notes=[str(item) for item in payload.get("notes", [])] if isinstance(payload.get("notes"), list) else [],
            payload=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "received_at": self.received_at,
            "source": self.source,
            "symbol": self.symbol,
            "direction": self.direction,
            "setup_type": self.setup_type,
            "trigger_type": self.trigger_type,
            "timeframe": self.timeframe,
            "price": self.price,
            "status": self.status,
            "notes": self.notes,
            "payload": self.payload,
        }
