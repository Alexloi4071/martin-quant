"""tests/unit/test_candlestick.py

Unit tests for features/candlestick.py
"""
import numpy as np
import pandas as pd
import pytest

from martin_quant.features.candlestick import (
    is_inside_day,
    is_nr7,
    is_tight_base,
    is_parabolic_move,
    is_engulfing_bull,
    is_engulfing_bear,
    get_candlestick_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(highs, lows, opens=None, closes=None) -> pd.DataFrame:
    n = len(highs)
    if opens is None:
        opens  = [(h + l) * 0.45 for h, l in zip(highs, lows)]
    if closes is None:
        closes = [(h + l) * 0.55 for h, l in zip(highs, lows)]
    return pd.DataFrame({
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": [1_000_000] * n,
    })


def _flat_df(n: int = 20, base_price: float = 100.0, range_pct: float = 1.0) -> pd.DataFrame:
    """Uniform-range bars for baseline tests."""
    half = base_price * range_pct / 200
    highs  = [base_price + half] * n
    lows   = [base_price - half] * n
    closes = [base_price] * n
    opens  = [base_price] * n
    return _make_df(highs, lows, opens, closes)


# ---------------------------------------------------------------------------
# Inside Day
# ---------------------------------------------------------------------------

class TestInsideDay:
    def test_true_when_inside(self):
        df = _make_df([105, 103], [95, 97])
        assert is_inside_day(df) is True

    def test_false_when_outside_high(self):
        df = _make_df([105, 106], [95, 97])
        assert is_inside_day(df) is False

    def test_false_when_outside_low(self):
        df = _make_df([105, 103], [95, 94])
        assert is_inside_day(df) is False

    def test_insufficient_data(self):
        df = _make_df([105], [95])
        assert is_inside_day(df) is False


# ---------------------------------------------------------------------------
# NR7
# ---------------------------------------------------------------------------

class TestNR7:
    def test_true_when_narrowest(self):
        # Last bar has the smallest range
        highs  = [110, 109, 108, 107, 106, 105, 102]
        lows   = [90,  91,  92,  93,  94,  95,  99]
        df = _make_df(highs, lows)
        assert is_nr7(df) is True

    def test_false_when_not_narrowest(self):
        highs  = [102, 109, 108, 107, 106, 105, 110]
        lows   = [99,  91,  92,  93,  94,  95,  90]
        df = _make_df(highs, lows)
        assert is_nr7(df) is False

    def test_insufficient_data_returns_false(self):
        df = _flat_df(n=5)
        assert is_nr7(df) is False


# ---------------------------------------------------------------------------
# Tight Base
# ---------------------------------------------------------------------------

class TestTightBase:
    def test_true_when_tight(self):
        # All bars within 0.5% range
        df = _flat_df(n=15, base_price=100.0, range_pct=0.4)
        assert is_tight_base(df, lookback=10) is True

    def test_false_when_wide(self):
        # Wide-ranging bars
        df = _flat_df(n=15, base_price=100.0, range_pct=5.0)
        assert is_tight_base(df, lookback=10) is False


# ---------------------------------------------------------------------------
# Parabolic
# ---------------------------------------------------------------------------

class TestParabolic:
    def test_true_on_large_gain(self):
        closes = list(range(100, 140, 2))  # +38% in 20 bars
        highs  = [c + 1 for c in closes]
        lows   = [c - 1 for c in closes]
        df = _make_df(highs, lows, closes=closes)
        # 20-bar return is > 30%, should flag parabolic
        assert is_parabolic_move(df, lookback=20, threshold_pct=30.0) is True

    def test_false_on_normal_trend(self):
        df = _flat_df(n=25, range_pct=0.5)
        assert is_parabolic_move(df, lookback=20, threshold_pct=30.0) is False


# ---------------------------------------------------------------------------
# Engulfing
# ---------------------------------------------------------------------------

class TestEngulfing:
    def test_bull_engulfing(self):
        # Bar 0: bearish (open > close), Bar 1: bull engulfs
        df = _make_df(
            highs  = [105, 107],
            lows   = [95,  93],
            opens  = [104, 94],
            closes = [96,  106],
        )
        assert is_engulfing_bull(df) is True
        assert is_engulfing_bear(df) is False

    def test_bear_engulfing(self):
        df = _make_df(
            highs  = [107, 105],
            lows   = [93,  95],
            opens  = [94,  106],
            closes = [106, 96],
        )
        assert is_engulfing_bear(df) is True
        assert is_engulfing_bull(df) is False


# ---------------------------------------------------------------------------
# Full summary
# ---------------------------------------------------------------------------

class TestCandlestickSummary:
    def test_squeeze_signal_when_inside_and_nr7(self):
        # Build 7 bars where last is narrowest AND inside prev
        highs  = [110, 109, 108, 107, 106, 105, 104]
        lows   = [90,  91,  92,  93,  94,  95,  96]
        df = _make_df(highs, lows)
        cs = get_candlestick_summary("TEST", df)
        assert cs.nr7 is True
        assert cs.inside_day is True
        assert cs.squeeze_signal is True

    def test_no_signal_on_flat(self):
        df = _flat_df(n=20, range_pct=1.0)
        cs = get_candlestick_summary("TEST", df)
        # flat bars: inside_day could vary, but parabolic = False
        assert cs.parabolic is False
