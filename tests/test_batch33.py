from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace


class _TriggerType:
    def __init__(self, value: str) -> None:
        self.value = value


def _orb_signal():
    return SimpleNamespace(
        trigger_reason="orb_15m_breakout_close_confirm",
        confirmation_mode="bar_close",
        confirmation_bars=1,
        confirmation_reason="1/1 closes > OR_high_breakout(105.10)",
    )


def _short_timing_signal():
    return SimpleNamespace(
        trigger_type=_TriggerType("short_retest_breakdown"),
        context={
            "entry_confirmation": {
                "phase": "entry",
                "trade_direction": "short",
                "relation": "below",
                "required_bars": 1,
                "reason": "1/1 closes < retest_low_break(95.00)",
                "mode": "bar_close",
            }
        },
    )


class TestCandidateExportConfirmationMetadata:
    def test_export_scan_candidates_includes_confirmation_and_context_fields(self, tmp_path):
        from martin_quant.signals import export_scan_candidates

        results = [
            SimpleNamespace(
                symbol="NVDA",
                direction="long",
                setup_type="pullback",
                setup_score=0.72,
                total_score=0.81,
                regime="BULL",
                sector="semiconductors",
                entry_price=910.5,
                stop_price=892.0,
                target_price=966.0,
                avwap_score=0.2,
                sector_bonus=0.15,
                dynamic_sector_bonus=0.05,
                breadth_bonus=0.04,
                weekly_bonus=0.08,
                gap_bonus=0.02,
                regime_weight=1.0,
                trade_quality_state="GO",
                breadth_state="EXPANDING",
                sector_strength_state="STRONG",
                weekly_trend_state="BULL",
                gap_label="gap_down_into_support",
                entry_note="weekly=bull, avwap_support",
                orb_signal=_orb_signal(),
                timing_signal=None,
            ),
            SimpleNamespace(
                symbol="TSLA",
                direction="short",
                setup_type="short_resistance_reversal",
                setup_score=0.69,
                total_score=0.78,
                regime="BEAR",
                sector="consumer_discretionary",
                entry_price=166.2,
                stop_price=171.4,
                target_price=155.8,
                avwap_score=0.1,
                sector_bonus=0.15,
                dynamic_sector_bonus=0.03,
                breadth_bonus=0.06,
                weekly_bonus=0.07,
                gap_bonus=0.08,
                regime_weight=0.3,
                trade_quality_state="GO",
                breadth_state="VERY_WEAK",
                sector_strength_state="WEAK",
                weekly_trend_state="BEAR",
                gap_label="gap_up_into_resistance",
                entry_note="weekly=bear, short_bias",
                orb_signal=None,
                timing_signal=_short_timing_signal(),
            ),
        ]

        paths = export_scan_candidates(results, out_dir=str(tmp_path), as_of="2026-03-21")

        payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
        assert payload["metadata"]["confirmed_entry_count"] == 2
        assert payload["signals"][0]["confirmation_mode"] == "bar_close"
        assert payload["signals"][0]["weekly_trend_state"] == "BULL"
        assert payload["signals"][1]["gap_label"] == "gap_up_into_resistance"
        assert payload["signals"][1]["context"]["entry_confirmation"]["trade_direction"] == "short"

        rows = list(csv.DictReader(Path(paths["csv"]).open("r", encoding="utf-8")))
        assert rows[0]["confirmation_mode"] == "bar_close"
        assert rows[0]["trigger_type"] == "orb_15m_breakout_close_confirm"
        assert rows[1]["confirmation_reason"] == "1/1 closes < retest_low_break(95.00)"
        assert rows[1]["weekly_trend_state"] == "BEAR"
        assert rows[1]["gap_label"] == "gap_up_into_resistance"
