from __future__ import annotations

import numpy as np
import pandas as pd


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


def _leading_df() -> pd.DataFrame:
    closes = np.linspace(100, 220, 180)
    return _daily_df_from_closes(closes.tolist())


def _lagging_df() -> pd.DataFrame:
    closes = np.linspace(220, 100, 180)
    return _daily_df_from_closes(closes.tolist())


def _mediocre_df() -> pd.DataFrame:
    first = np.linspace(100, 220, 140)
    second = np.linspace(220, 175, 40)
    closes = np.concatenate([first, second])
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


def _intraday_first_15_fail_df() -> pd.DataFrame:
    df = _intraday_short_trigger_df().copy()
    df.loc[df["timestamp"] == pd.Timestamp("2026-03-20 14:45", tz="UTC"), ["high", "close", "low"]] = [95.9, 95.4, 95.1]
    df.loc[df["timestamp"] == pd.Timestamp("2026-03-20 15:00", tz="UTC"), ["open", "high", "close", "low", "volume"]] = [95.3, 95.5, 95.2, 94.9, 900_000]
    return df


class TestTranscriptWatchlistBuckets:
    def test_classify_trend_bucket_rules(self):
        from martin_quant.universe.watchlist_builder import classify_trend_bucket

        assert classify_trend_bucket(120.0, 110.0, 100.0) == "leading"
        assert classify_trend_bucket(90.0, 100.0, 110.0) == "lagging"
        assert classify_trend_bucket(95.0, 100.0, 90.0) == "mediocre"

    def test_build_transcript_buckets(self):
        from martin_quant.universe.watchlist_builder import WatchlistBuilder

        builder = WatchlistBuilder()
        buckets = builder.build_transcript_buckets(
            symbols=["LEAD", "MID", "LAG"],
            ohlcv_map={
                "LEAD": _leading_df(),
                "MID": _mediocre_df(),
                "LAG": _lagging_df(),
            },
        )

        assert [item.symbol for item in buckets["leading"]] == ["LEAD"]
        assert [item.symbol for item in buckets["mediocre"]] == ["MID"]
        assert [item.symbol for item in buckets["lagging"]] == ["LAG"]


class TestMartinMarketContext:
    def test_bull_context_is_breakout_friendly(self):
        from martin_quant.regime import MartinMarketContextEvaluator

        qqq = _leading_df().tail(120).reset_index(drop=True)
        iwm = _leading_df().tail(120).reset_index(drop=True)
        context = MartinMarketContextEvaluator().evaluate(qqq_df=qqq, iwm_df=iwm)

        assert context.regime == "BULL"
        assert context.breakout_friendly is True
        assert context.trade_less is False
        assert context.short_bias_ok is False

    def test_hard_down_context_flags_trade_less(self):
        from martin_quant.regime import MartinMarketContextEvaluator

        qqq = _lagging_df().tail(120).reset_index(drop=True)
        iwm = _lagging_df().tail(120).reset_index(drop=True)
        context = MartinMarketContextEvaluator().evaluate(qqq_df=qqq, iwm_df=iwm)

        assert context.regime == "BEAR"
        assert context.breakout_friendly is False
        assert context.trade_less is True
        assert context.short_bias_ok is True


class TestShortRetestBreakdownTrigger:
    def test_detects_short_retest_breakdown(self):
        from martin_quant.core.enums import TriggerType
        from martin_quant.timing import ShortRetestBreakdownTrigger

        df = _intraday_short_trigger_df()
        trigger = ShortRetestBreakdownTrigger()
        signal = trigger.detect(symbol="TSLA", df_15m=df, benchmark_df_15m=df)

        assert signal is not None
        assert signal.trigger_type == TriggerType.SHORT_RETEST_BREAKDOWN
        assert signal.direction == "short"
        assert signal.entry_price < signal.stop_price
        assert signal.context["cover_if_close_above_ema9"] is True
        assert "gap_fill" in signal.context["retest_references"]

    def test_skips_first_15_minutes_pattern(self):
        from martin_quant.timing import ShortRetestBreakdownTrigger

        df = _intraday_first_15_fail_df()
        trigger = ShortRetestBreakdownTrigger()
        signal = trigger.detect(symbol="TSLA", df_15m=df, benchmark_df_15m=df)

        assert signal is None


class TestDailyScannerV2ShortTiming:
    def test_scan_attaches_short_timing_signal(self):
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2

        daily_df = _lagging_df().tail(120).reset_index(drop=True)
        intraday_df = _intraday_short_trigger_df()
        scanner = DailyScannerV2()
        results = scanner.scan(
            watchlist_data={"TSLA": daily_df},
            regime="CHOPPY",
            watchlist_sectors={"TSLA": "semiconductors"},
            watchlist_setup_scores={"TSLA": {"score": 1.0, "type": "short_resistance_reversal", "direction": "short"}},
            df_15m_map={"TSLA": intraday_df},
            benchmark_15m_map={"QQQ": intraday_df},
        )

        assert results
        assert results[0].timing_signal is not None
        assert "short_trigger@" in results[0].entry_note
