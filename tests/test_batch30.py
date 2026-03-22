from __future__ import annotations

import numpy as np
import pandas as pd


def _make_15m_df(n_bars: int = 30, or_high: float = 105.0, or_low: float = 103.0, breakout: bool = True) -> pd.DataFrame:
    times = pd.date_range("2026-03-12 09:30", periods=n_bars, freq="15min")
    closes = [or_low + (or_high - or_low) * 0.5] * n_bars
    highs = [or_high * 0.99] * n_bars
    lows = [or_low * 1.01] * n_bars
    vols = [500_000] * n_bars

    if breakout and n_bars >= 3:
        closes[2] = or_high * 1.015
        highs[2] = or_high * 1.02
        vols[2] = 1_500_000

    return pd.DataFrame(
        {
            "open": [or_low + 0.5] * n_bars,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": vols,
        },
        index=times,
    )


def _daily_df_from_closes(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value * 1.03 for value in closes],
            "low": [value * 0.97 for value in closes],
            "close": closes,
            "volume": [2_500_000] * len(closes),
        },
        index=dates,
    )


def _lagging_df() -> pd.DataFrame:
    closes = np.linspace(220, 100, 180)
    return _daily_df_from_closes(closes.tolist())


def _intraday_short_trigger_df() -> pd.DataFrame:
    prev_times = pd.date_range("2026-03-19 14:30", periods=24, freq="15min", tz="UTC")
    prev_closes = np.linspace(103.0, 98.0, 24)
    prev_rows = [
        {
            "timestamp": ts,
            "open": close + 0.2,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close,
            "volume": 1_000_000,
        }
        for ts, close in zip(prev_times, prev_closes)
    ]

    today_rows = [
        {"timestamp": pd.Timestamp("2026-03-20 14:30", tz="UTC"), "open": 95.4, "high": 95.8, "low": 95.0, "close": 95.3, "volume": 1_000_000},
        {"timestamp": pd.Timestamp("2026-03-20 14:45", tz="UTC"), "open": 95.4, "high": 97.7, "low": 95.8, "close": 96.9, "volume": 1_100_000},
        {"timestamp": pd.Timestamp("2026-03-20 15:00", tz="UTC"), "open": 96.7, "high": 96.8, "low": 95.0, "close": 95.2, "volume": 1_800_000},
        {"timestamp": pd.Timestamp("2026-03-20 15:15", tz="UTC"), "open": 95.1, "high": 95.4, "low": 94.8, "close": 95.0, "volume": 1_200_000},
    ]
    return pd.DataFrame(prev_rows + today_rows)


class TestSharedCloseConfirmationPolicy:
    def test_policy_objects_for_entry_and_exit(self):
        from martin_quant.entry import CloseConfirmation

        engine = CloseConfirmation()
        entry = engine.policy_for_entry("long", reference_label="OR_high_breakout")
        exit_policy = engine.policy_for_exit("short", reference_label="EMA9")

        assert entry.phase == "entry"
        assert entry.relation == "above"
        assert entry.required_bars == 1
        assert exit_policy.phase == "exit"
        assert exit_policy.relation == "above"
        assert exit_policy.required_bars == 1

    def test_orb_trigger_uses_shared_entry_confirmation(self):
        from martin_quant.timing.orb_15m_trigger import ORBTrigger

        trigger = ORBTrigger(equity=100_000)
        signal = trigger.check("NVDA", _make_15m_df(breakout=True), daily_setup_score=0.75)

        assert signal is not None
        assert signal.trigger_reason == "orb_15m_breakout_close_confirm"
        assert signal.confirmation_mode == "bar_close"
        assert signal.confirmation_bars == 1
        assert "OR_high_breakout" in signal.confirmation_reason

    def test_short_trigger_exposes_shared_entry_confirmation_metadata(self):
        from martin_quant.timing import ShortRetestBreakdownTrigger

        signal = ShortRetestBreakdownTrigger().detect(
            symbol="TSLA",
            df_15m=_intraday_short_trigger_df(),
            benchmark_df_15m=_intraday_short_trigger_df(),
            daily_df=_lagging_df().tail(120).reset_index(drop=True),
        )

        assert signal is not None
        confirmation = signal.context["entry_confirmation"]
        assert confirmation["phase"] == "entry"
        assert confirmation["trade_direction"] == "short"
        assert confirmation["relation"] == "below"
        assert "retest_low_break" in confirmation["reason"]
