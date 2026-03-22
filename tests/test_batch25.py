from __future__ import annotations

import numpy as np
import pandas as pd

from martin_quant.features.weekly_context import WeeklyContext
from martin_quant.regime.martin_market_context import MartinMarketContext


def _daily_df_index_only(n: int = 260, start: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    closes = [start + step * i for i in range(n)]
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_200_000] * n,
        },
        index=dates,
    )


def _make_pullback_df() -> pd.DataFrame:
    rng = np.random.default_rng(99)
    up_prices = 100.0 + np.cumsum(rng.normal(0.8, 0.5, 80))
    down_prices = up_prices[-1] + np.cumsum(rng.normal(-0.3, 0.5, 40))
    close = np.concatenate([up_prices, down_prices])
    close = np.clip(close, 10, None)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2023-01-01", periods=len(close), freq="D"),
            "open": close * 0.999,
            "high": close * 1.015,
            "low": close * 0.985,
            "close": close,
            "volume": rng.integers(300_000, 1_000_000, len(close)),
        }
    )


def _make_short_df() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=100, freq="B")
    close = np.linspace(150, 100, 100)
    close[-1] = 101.5
    close[-2] = 102.0
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": close * 1.002,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": [900_000] * 100,
        }
    )


def _weekly_ctx(trend: str) -> WeeklyContext:
    if trend == "bull":
        return WeeklyContext(
            symbol="TEST",
            week_end="2026-03-20",
            ema_bull_stack=True,
            ema_bear_stack=False,
            close_strength=0.75,
            rs_vs_spy=12.0,
            trend_state="bull",
            close_above_wema9=True,
            close_above_wema21=True,
            close_above_wema50=True,
        )
    if trend == "bear":
        return WeeklyContext(
            symbol="TEST",
            week_end="2026-03-20",
            ema_bull_stack=False,
            ema_bear_stack=True,
            close_strength=0.2,
            rs_vs_spy=-10.0,
            trend_state="bear",
            close_above_wema9=False,
            close_above_wema21=False,
            close_above_wema50=False,
        )
    return WeeklyContext(
        symbol="TEST",
        week_end="2026-03-20",
        ema_bull_stack=False,
        ema_bear_stack=False,
        close_strength=0.5,
        rs_vs_spy=0.0,
        trend_state="neutral",
        close_above_wema9=True,
        close_above_wema21=True,
        close_above_wema50=False,
    )


class TestWeeklyContextUpgrade:
    def test_weekly_context_handles_index_only_daily_df(self):
        from martin_quant.features.weekly_context import get_weekly_context

        df = _daily_df_index_only()
        ctx = get_weekly_context("NVDA", df, df)
        assert ctx is not None
        assert ctx.trend_state in {"bull", "neutral"}
        assert ctx.close_above_wema21 is True


class TestTradeQualityState:
    def test_bull_context_maps_to_go(self):
        from martin_quant.regime import MartinTradeQualityEvaluator

        context = MartinMarketContext(
            regime="BULL",
            breakout_friendly=True,
            trade_less=False,
            short_bias_ok=False,
            avoid_new_shorts_on_open=False,
            qqq_above_ema50=True,
            iwm_above_ema50=True,
            qqq_ema50_slope_pct=1.0,
            iwm_ema50_slope_pct=0.8,
            qqq_day_change_pct=0.5,
            iwm_day_change_pct=0.4,
            notes=[],
        )
        state = MartinTradeQualityEvaluator().evaluate(context)
        assert state.state == "GO"
        assert state.allow_longs is True
        assert state.allow_shorts is False

    def test_hard_gap_choppy_maps_to_observe_only(self):
        from martin_quant.regime import MartinTradeQualityEvaluator

        context = MartinMarketContext(
            regime="CHOPPY",
            breakout_friendly=False,
            trade_less=True,
            short_bias_ok=False,
            avoid_new_shorts_on_open=True,
            qqq_above_ema50=False,
            iwm_above_ema50=False,
            qqq_ema50_slope_pct=-0.5,
            iwm_ema50_slope_pct=-0.3,
            qqq_day_change_pct=-2.2,
            iwm_day_change_pct=-1.5,
            notes=[],
        )
        state = MartinTradeQualityEvaluator().evaluate(context)
        assert state.state == "OBSERVE_ONLY"
        assert state.quality_weight == 0.0


class TestWeeklyGatedSetups:
    def test_pullback_requires_supportive_weekly_context_when_enabled(self):
        from martin_quant.setups.pullback_setup import PullbackConfig, PullbackSetupDetector

        df = _make_pullback_df()
        detector = PullbackSetupDetector(
            PullbackConfig(
                require_weekly_context=True,
                require_close_above_ema50=False,
                require_ema_stack=False,
                min_pullback_depth_pct=2.0,
                max_pullback_depth_pct=50.0,
                max_support_distance_pct=10.0,
            )
        )
        assert detector.detect("TEST", df, weekly_context=_weekly_ctx("bear")) is None
        signal = detector.detect("TEST", df, weekly_context=_weekly_ctx("bull"))
        if signal is not None:
            assert signal.context["weekly_trend_state"] == "bull"

    def test_short_setup_requires_weekly_bear_when_enabled(self):
        from martin_quant.setups.short_setup import ShortSetupConfig, ShortSetupDetector

        df = _make_short_df()
        detector = ShortSetupDetector(
            ShortSetupConfig(
                require_weekly_context=True,
                require_weekly_bear_for_short=True,
                require_bearish_candle=False,
            )
        )
        assert detector.detect("TSLA", df, weekly_context=_weekly_ctx("bull")) is None
        signal = detector.detect("TSLA", df, weekly_context=_weekly_ctx("bear"))
        assert signal is not None
        assert signal.context["weekly_bear"] is True


class TestScannerTradeQuality:
    def test_scanner_observe_only_returns_no_results(self):
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2

        scanner = DailyScannerV2()
        data = {"NVDA": _daily_df_index_only(120)}
        results = scanner.scan(
            watchlist_data=data,
            regime="CHOPPY",
            watchlist_sectors={"NVDA": "semiconductors"},
            watchlist_setup_scores={"NVDA": {"score": 0.9, "type": "pullback", "direction": "long"}},
            trade_quality_state="OBSERVE_ONLY",
            trade_quality_weight=0.0,
            allow_longs=False,
            allow_shorts=False,
        )
        assert results == []
