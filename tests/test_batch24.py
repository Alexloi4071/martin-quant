from __future__ import annotations

from datetime import date, timedelta

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


def _close_failure_df(direction: str) -> pd.DataFrame:
    if direction == "long":
        closes = [100, 101, 102, 103, 104, 105, 106, 104, 102, 100]
    else:
        closes = [110, 109, 108, 107, 106, 105, 104, 103, 104, 106]
    return _daily_frame(closes)


def _short_intraday_df() -> pd.DataFrame:
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


class TestCloseConfirmation:
    def test_long_trade_failure_needs_two_closes_below_ema9(self):
        from martin_quant.entry import CloseConfirmation

        evaluator = CloseConfirmation()
        result = evaluator.confirm_trade_failure(_close_failure_df("long"), trade_direction="long")

        assert result.confirmed is True
        assert result.confirmed_bars == 2
        assert result.relation == "below"

    def test_short_trade_failure_can_trigger_on_one_close_above_ema9(self):
        from martin_quant.entry import CloseConfirmation

        evaluator = CloseConfirmation()
        result = evaluator.confirm_trade_failure(_close_failure_df("short"), trade_direction="short")

        assert result.confirmed is True
        assert result.required_bars == 1
        assert result.relation == "above"


class TestGapContext:
    def test_gap_up_into_resistance(self):
        from martin_quant.features import analyze_gap_context

        ctx = analyze_gap_context(daily_df=_gap_up_into_resistance_daily())
        assert ctx is not None
        assert ctx.direction == "gap_up"
        assert ctx.label == "gap_up_into_resistance"
        assert ctx.nearest_resistance is not None

    def test_gap_down_into_support(self):
        from martin_quant.features import analyze_gap_context

        ctx = analyze_gap_context(daily_df=_gap_down_into_support_daily())
        assert ctx is not None
        assert ctx.direction == "gap_down"
        assert ctx.label == "gap_down_into_support"
        assert ctx.nearest_support is not None


class TestExitManagerMartinRules:
    def test_short_exit_uses_one_close_above_ema9(self):
        from martin_quant.risk.exit_manager import ExitManager, Position

        manager = ExitManager()
        position = Position(
            symbol="TSLA",
            entry_price=100.0,
            stop_price=109.0,
            target_price=91.0,
            shares=100,
            entry_date=str(date.today() - timedelta(days=2)),
            direction="short",
        )
        signal = manager.evaluate(position=position, df=_close_failure_df("short"), current_price=106.0, regime="choppy")

        assert signal.should_exit is True
        assert signal.exit_type == "ema9_close_confirm"
        assert "close(s) above EMA9" in signal.reason

    def test_long_exit_does_not_fire_on_only_one_close_below_ema9(self):
        from martin_quant.risk.exit_manager import ExitManager, Position

        manager = ExitManager()
        df = _daily_frame([100, 101, 102, 103, 104, 105, 106, 107, 105, 106])
        position = Position(
            symbol="NVDA",
            entry_price=100.0,
            stop_price=96.0,
            target_price=112.0,
            shares=100,
            entry_date=str(date.today() - timedelta(days=2)),
            direction="long",
        )
        signal = manager.evaluate(position=position, df=df, current_price=106.0, regime="bull")

        assert signal.should_exit is False


class TestShortTriggerGapIntegration:
    def test_short_trigger_includes_gap_context(self):
        from martin_quant.timing import ShortRetestBreakdownTrigger

        daily_df = _gap_up_into_resistance_daily()
        intraday_df = _short_intraday_df()
        signal = ShortRetestBreakdownTrigger().detect(
            symbol="TSLA",
            df_15m=intraday_df,
            benchmark_df_15m=intraday_df,
            daily_df=daily_df,
        )

        assert signal is not None
        assert "gap_context" in signal.context
        assert signal.context["gap_context"]["label"] != "flat_open"
        assert any("Symbol gap context" in note for note in signal.notes)
