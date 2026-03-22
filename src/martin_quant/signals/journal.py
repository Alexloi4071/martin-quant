from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from martin_quant.signals.models import CandidateSignal, SignalEvent, utc_now_iso


class SignalJournal:
    def __init__(self, base_dir: str = "outputs/signals") -> None:
        self.base_dir = Path(base_dir)
        self.candidates_dir = self.base_dir / "candidates"
        self.events_path = self.base_dir / "events.jsonl"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.candidates_dir.mkdir(parents=True, exist_ok=True)

    def save_candidate_snapshot(
        self,
        signals: Iterable[CandidateSignal],
        snapshot_name: str,
        metadata: dict | None = None,
    ) -> Path:
        items = [signal.to_dict() for signal in signals]
        payload = {
            "snapshot_name": snapshot_name,
            "created_at": utc_now_iso(),
            "count": len(items),
            "long_count": sum(1 for item in items if item.get("direction") == "long"),
            "short_count": sum(1 for item in items if item.get("direction") == "short"),
            "metadata": metadata or {},
            "signals": items,
        }
        path = self.candidates_dir / f"{snapshot_name}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        return path

    def append_event(self, event: SignalEvent) -> Path:
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=True) + "\n")
        return self.events_path

    def load_events(self, limit: int | None = None) -> list[SignalEvent]:
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text(encoding="utf-8").splitlines()
        if limit is not None:
            lines = lines[-limit:]
        items: list[SignalEvent] = []
        for line in lines:
            if not line.strip():
                continue
            items.append(SignalEvent.from_payload(json.loads(line), source="journal"))
        return items
