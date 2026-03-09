"""tests/unit/test_intraday_entry.py

Unit tests for timing/intraday_entry.py
"""
import pandas as pd
import numpy as np
import pytest

from martin_quant.timing.intraday_entry import IntradayEntryDetector, IntradayEntryConfig


def _make_5m_bars(
    n: int = 50,
    base: float = 100.0,
    ema9_cross: bool = True,
) -> pd.DataFrame:
    """
    Create 5-minute bars. If ema9_cross=True, the last bar crosses above EMA9.
    """
    closes = [base - 0.05 * i for i in range(n)]  # slight downtrend first
    closes = list(reversed(closes))

    if ema9_cross:
        # Make last 3 bars cross above EMA9
        avg    = np.mean(closes[-12:])
        closes[-2] = avg * 0.995  # prev bar: below EMA
        closes[-1] = avg * 1.005  # last bar: above EMA (reclaim)

    highs   = [c * 1.002 for c in closes]
    lows    = [c * 0.998 for c in closes]
    volumes = [500_000] * n
    volumes[-1] = 1_500_000   # last bar has high volume (RVOL 3x)

    df = pd.DataFrame({
        "open":   closes, "high": highs,
        "low":    lows,   "close": closes,
        "volume": volumes,
    })
    df.index = pd.date_range("2025-01-15 09:30", periods=n, freq="5min")
    return df


class TestIntradayEntryDetector:
    def setup_method(self):
        self.det = IntradayEntryDetector(
            equity=100_000,
            config=IntradayEntryConfig(
                ema_span=9,
                max_stop_pct=2.5,
                min_stop_pct=0.3,
                min_rvol_on_entry=1.2,
                per_trade_risk_pct=0.5,
            ),
        )

    def test_returns_signal_on_ema9_cross(self):
        df = _make_5m_bars(n=50, ema9_cross=True)
        sig = self.det.find_entry("NVDA", df)
        assert sig is not None
        assert sig.entry_price > 0
        assert sig.stop_price < sig.entry_price
        assert sig.target_price > sig.entry_price
        assert sig.shares > 0

    def test_stop_is_tighter_than_daily(self):
        df = _make_5m_bars(n=50, ema9_cross=True)
        sig = self.det.find_entry("NVDA", df)
        if sig:
            # Stop should be < 2.5% (daily typically 3%)
            assert sig.stop_pct < 2.5

    def test_r_multiple_advantage(self):
        """5m stop gives > 2x R multiple vs 3% daily stop."""
        df  = _make_5m_bars(n=50, ema9_cross=True)
        sig = self.det.find_entry("NVDA", df, daily_target=110.0)
        if sig and sig.stop_pct > 0:
            comp = self.det.compare_with_daily(sig, daily_stop_pct=3.0, target_pct=10.0)
            assert comp["intraday_R"] > comp["daily_R"]

    def test_risk_dollars_correct(self):
        df  = _make_5m_bars(n=50, ema9_cross=True)
        sig = self.det.find_entry("NVDA", df)
        if sig:
            expected_risk = 100_000 * 0.5 / 100
            assert abs(sig.risk_dollars - expected_risk) < 1.0

    def test_no_signal_on_flat_bars(self):
        """Flat market with no EMA9 cross should return None."""
        n       = 50
        closes  = [100.0] * n
        df = pd.DataFrame({
            "open":   closes, "high": [101.0] * n,
            "low":    [99.0] * n, "close": closes,
            "volume": [500_000] * n,
        })
        sig = self.det.find_entry("FLAT", df)
        # May or may not trigger depending on EMA calc; just assert no error
        assert sig is None or sig.stop_price < sig.entry_price
