from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from martin_quant.signals.journal import SignalJournal
from martin_quant.signals.models import CandidateSignal


def _trigger_type(result: object) -> str:
    timing_signal = getattr(result, "timing_signal", None)
    if timing_signal is not None:
        trigger = getattr(timing_signal, "trigger_type", None)
        if trigger is not None:
            return getattr(trigger, "value", str(trigger))
    orb_signal = getattr(result, "orb_signal", None)
    if orb_signal is not None:
        return str(getattr(orb_signal, "trigger_reason", "orb_15m_breakout"))
    return ""


def _entry_confirmation(result: object) -> dict[str, object] | None:
    orb_signal = getattr(result, "orb_signal", None)
    if orb_signal is not None:
        reason = str(getattr(orb_signal, "confirmation_reason", "") or "").strip()
        bars = int(getattr(orb_signal, "confirmation_bars", 0) or 0)
        mode = str(getattr(orb_signal, "confirmation_mode", "") or "").strip()
        if reason or bars:
            return {
                "source": "orb",
                "mode": mode,
                "required_bars": bars,
                "reason": reason,
            }

    timing_signal = getattr(result, "timing_signal", None)
    if timing_signal is not None:
        context = getattr(timing_signal, "context", {}) or {}
        confirmation = context.get("entry_confirmation")
        if isinstance(confirmation, dict):
            payload = dict(confirmation)
            payload.setdefault("source", "timing_signal")
            payload.setdefault("mode", "bar_close")
            return payload
    return None


def signals_from_scan_results(results: Iterable[object], as_of: str, source: str = "scan_v2") -> list[CandidateSignal]:
    signals: list[CandidateSignal] = []
    for result in results:
        notes = [item.strip() for item in str(getattr(result, "entry_note", "")).split(",") if item.strip()]
        confirmation = _entry_confirmation(result)
        signals.append(
            CandidateSignal(
                symbol=str(getattr(result, "symbol", "")).upper(),
                direction=str(getattr(result, "direction", "long")).lower(),
                setup_type=str(getattr(result, "setup_type", "unknown")),
                regime=str(getattr(result, "regime", "")),
                sector=str(getattr(result, "sector", "")),
                score=float(getattr(result, "setup_score", 0.0)),
                total_score=float(getattr(result, "total_score", 0.0)),
                timeframe="1d",
                entry_price=getattr(result, "entry_price", None),
                stop_price=getattr(result, "stop_price", None),
                target_price=getattr(result, "target_price", None),
                trigger_type=_trigger_type(result),
                entry_note=str(getattr(result, "entry_note", "")),
                confirmation_mode=str(confirmation.get("mode", "")) if confirmation else "",
                confirmation_bars=int(confirmation.get("required_bars", 0) or 0) if confirmation else 0,
                confirmation_reason=str(confirmation.get("reason", "")) if confirmation else "",
                weekly_trend_state=str(getattr(result, "weekly_trend_state", "")),
                gap_label=str(getattr(result, "gap_label", "")),
                notes=notes,
                context={
                    "avwap_score": float(getattr(result, "avwap_score", 0.0)),
                    "sector_bonus": float(getattr(result, "sector_bonus", 0.0)),
                    "dynamic_sector_bonus": float(getattr(result, "dynamic_sector_bonus", 0.0)),
                    "breadth_bonus": float(getattr(result, "breadth_bonus", 0.0)),
                    "weekly_bonus": float(getattr(result, "weekly_bonus", 0.0)),
                    "gap_bonus": float(getattr(result, "gap_bonus", 0.0)),
                    "regime_weight": float(getattr(result, "regime_weight", 0.0)),
                    "trade_quality_state": str(getattr(result, "trade_quality_state", "GO")),
                    "breadth_state": str(getattr(result, "breadth_state", "UNKNOWN")),
                    "sector_strength_state": str(getattr(result, "sector_strength_state", "UNKNOWN")),
                    "weekly_trend_state": str(getattr(result, "weekly_trend_state", "")),
                    "gap_label": str(getattr(result, "gap_label", "")),
                    "entry_confirmation": confirmation,
                },
                source=source,
                as_of=as_of,
            )
        )
    return signals


def export_scan_candidates(
    results: Iterable[object],
    out_dir: str = "outputs/signals",
    as_of: str = "",
    source: str = "scan_v2",
    metadata: dict | None = None,
) -> dict[str, str]:
    signals = signals_from_scan_results(results, as_of=as_of, source=source)
    base_dir = Path(out_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    snapshot = as_of or "latest"

    export_metadata = dict(metadata or {})
    export_metadata.setdefault(
        "confirmed_entry_count",
        sum(1 for signal in signals if signal.confirmation_reason),
    )

    journal = SignalJournal(base_dir=str(base_dir))
    json_path = journal.save_candidate_snapshot(signals, snapshot_name=f"candidates_{snapshot}", metadata=export_metadata)

    csv_path = base_dir / f"candidates_{snapshot}.csv"
    fieldnames = [
        "symbol", "direction", "setup_type", "regime", "sector", "score", "total_score",
        "entry_price", "stop_price", "target_price", "trigger_type", "entry_note",
        "confirmation_mode", "confirmation_bars", "confirmation_reason", "weekly_trend_state", "gap_label",
        "source", "as_of",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for signal in signals:
            row = signal.to_dict()
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    long_txt = base_dir / f"long_symbols_{snapshot}.txt"
    short_txt = base_dir / f"short_symbols_{snapshot}.txt"
    long_txt.write_text("\n".join(signal.symbol for signal in signals if signal.direction == "long"), encoding="utf-8")
    short_txt.write_text("\n".join(signal.symbol for signal in signals if signal.direction == "short"), encoding="utf-8")

    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "long_txt": str(long_txt),
        "short_txt": str(short_txt),
    }
