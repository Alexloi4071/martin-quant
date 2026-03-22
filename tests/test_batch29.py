from __future__ import annotations

import numpy as np
import pandas as pd


def _daily_frame(closes: list[float], opens: list[float] | None = None) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=len(closes), freq="B")
    opens = opens or closes
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) * 1.01 for o, c in zip(opens, closes)],
            "low": [min(o, c) * 0.99 for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [1_500_000] * len(closes),
        },
        index=dates,
    )


def _uptrend_df() -> pd.DataFrame:
    closes = np.linspace(100, 180, 120).tolist()
    return _daily_frame(closes)


def _gap_up_into_resistance_daily() -> pd.DataFrame:
    closes = np.linspace(130, 100, 60).tolist()
    prev_close = closes[-2]
    opens = closes[:-1] + [111.4]
    closes[-1] = 109.2
    assert opens[-1] > prev_close
    return _daily_frame(closes, opens=opens)


def _gap_down_into_support_daily() -> pd.DataFrame:
    closes = np.linspace(100, 130, 60).tolist()
    prev_close = closes[-2]
    opens = closes[:-1] + [118.4]
    closes[-1] = 120.0
    assert opens[-1] < prev_close
    return _daily_frame(closes, opens=opens)


class TestScannerWeeklyContextOverlay:
    def test_weekly_context_is_first_class_in_long_ranking(self):
        from martin_quant.features.weekly_context import WeeklyContext
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2

        scanner = DailyScannerV2()
        bull_weekly = WeeklyContext(
            symbol="NVDA",
            week_end="2026-03-20",
            ema_bull_stack=True,
            ema_bear_stack=False,
            close_strength=0.82,
            rs_vs_spy=14.0,
            trend_state="bull",
            close_above_wema9=True,
            close_above_wema21=True,
            close_above_wema50=True,
        )
        neutral_weekly = WeeklyContext(
            symbol="LLY",
            week_end="2026-03-20",
            ema_bull_stack=False,
            ema_bear_stack=False,
            close_strength=0.55,
            rs_vs_spy=1.0,
            trend_state="neutral",
            close_above_wema9=True,
            close_above_wema21=True,
            close_above_wema50=True,
        )

        results = scanner.scan(
            watchlist_data={"NVDA": _uptrend_df(), "LLY": _uptrend_df()},
            regime="BULL",
            watchlist_sectors={"NVDA": "Semiconductors", "LLY": "Health Care"},
            watchlist_setup_scores={
                "NVDA": {"score": 0.8, "type": "pullback", "direction": "long"},
                "LLY": {"score": 0.8, "type": "pullback", "direction": "long"},
            },
            weekly_context_map={"NVDA": bull_weekly, "LLY": neutral_weekly},
        )

        assert len(results) == 2
        assert results[0].symbol == "NVDA"
        assert results[0].weekly_bonus > results[1].weekly_bonus
        assert results[0].weekly_trend_state == "BULL"
        assert "weekly=bull" in results[0].entry_note

    def test_weekly_context_map_is_mandatory_when_provided(self):
        from martin_quant.features.weekly_context import WeeklyContext
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2

        scanner = DailyScannerV2()
        only_one = WeeklyContext(
            symbol="NVDA",
            week_end="2026-03-20",
            ema_bull_stack=True,
            ema_bear_stack=False,
            close_strength=0.8,
            rs_vs_spy=10.0,
            trend_state="bull",
            close_above_wema9=True,
            close_above_wema21=True,
            close_above_wema50=True,
        )

        results = scanner.scan(
            watchlist_data={"NVDA": _uptrend_df(), "AMD": _uptrend_df()},
            regime="BULL",
            watchlist_sectors={"NVDA": "Semiconductors", "AMD": "Semiconductors"},
            watchlist_setup_scores={
                "NVDA": {"score": 0.8, "type": "pullback", "direction": "long"},
                "AMD": {"score": 0.8, "type": "pullback", "direction": "long"},
            },
            weekly_context_map={"NVDA": only_one},
        )

        assert [item.symbol for item in results] == ["NVDA"]


class TestScannerGapOverlay:
    def test_short_gap_up_into_resistance_ranks_above_gap_down_into_support(self):
        from martin_quant.features.weekly_context import WeeklyContext
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2

        scanner = DailyScannerV2()
        short_weekly = WeeklyContext(
            symbol="TSLA",
            week_end="2026-03-20",
            ema_bull_stack=False,
            ema_bear_stack=True,
            close_strength=0.22,
            rs_vs_spy=-12.0,
            trend_state="bear",
            close_above_wema9=False,
            close_above_wema21=False,
            close_above_wema50=False,
        )

        results = scanner.scan(
            watchlist_data={
                "TSLA": _gap_up_into_resistance_daily(),
                "SMCI": _gap_down_into_support_daily(),
            },
            regime="CHOPPY",
            watchlist_sectors={"TSLA": "Technology", "SMCI": "Technology"},
            watchlist_setup_scores={
                "TSLA": {"score": 1.0, "type": "short_resistance_reversal", "direction": "short"},
                "SMCI": {"score": 1.0, "type": "short_resistance_reversal", "direction": "short"},
            },
            weekly_context_map={"TSLA": short_weekly, "SMCI": short_weekly},
        )

        assert len(results) == 2
        assert results[0].symbol == "TSLA"
        assert results[0].gap_label == "gap_up_into_resistance"
        assert results[1].gap_label == "gap_down_into_support"
        assert results[0].gap_bonus > results[1].gap_bonus
        assert "gap_up_into_resistance" in results[0].entry_note
