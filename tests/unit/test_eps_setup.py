"""tests/unit/test_eps_setup.py

Unit tests for setups/eps_setup.py
"""
import pandas as pd
import numpy as np
import pytest

from martin_quant.setups.eps_setup import EpsSetupDetector, EpsSetupConfig


def _make_daily(n_before: int = 30, gap_pct: float = 15.0, days_ago: int = 2) -> pd.DataFrame:
    """
    Build a daily DataFrame with a gap-up bar 'days_ago' bars from the end.
    """
    rows = []
    price = 100.0
    for i in range(n_before + days_ago + 1):
        # Normal bar
        rows.append({
            "open":   price,
            "high":   price * 1.01,
            "low":    price * 0.99,
            "close":  price,
            "volume": 1_000_000,
        })
        price += 0.1

    # Insert gap bar at correct position
    gap_bar_idx = n_before
    prev_close  = rows[gap_bar_idx - 1]["close"]
    gap_open    = prev_close * (1 + gap_pct / 100)
    gap_close   = gap_open * 1.02     # closes strong
    gap_high    = gap_close * 1.01
    gap_low     = gap_open * 0.98
    avg_vol     = 1_000_000
    rows[gap_bar_idx] = {
        "open":   gap_open,
        "high":   gap_high,
        "low":    gap_low,
        "close":  gap_close,
        "volume": avg_vol * 5,   # RVOL = 5x
    }

    df = pd.DataFrame(rows)
    df.index = pd.date_range("2025-01-01", periods=len(df), freq="B")
    return df


class TestEpsSetupDetector:
    def setup_method(self):
        self.det = EpsSetupDetector(config=EpsSetupConfig(
            min_gap_pct=5.0,
            min_rvol_gap_day=3.0,
            min_close_strength=0.5,
            max_days_since_gap=5,
        ))

    def test_detects_fresh_gap(self):
        df = _make_daily(gap_pct=15.0, days_ago=1)
        signals = self.det.scan(["TEST"], {"TEST": df}, {"TEST"})
        assert len(signals) == 1
        assert signals[0].symbol == "TEST"
        assert signals[0].gap_pct >= 5.0
        assert signals[0].rvol_gap_day >= 3.0
        assert signals[0].has_eps_catalyst is True

    def test_no_signal_if_gap_too_small(self):
        df = _make_daily(gap_pct=2.0, days_ago=1)
        signals = self.det.scan(["TEST"], {"TEST": df})
        assert len(signals) == 0

    def test_no_signal_if_stale(self):
        df = _make_daily(gap_pct=15.0, days_ago=8)   # > max_days_since_gap=5
        signals = self.det.scan(["TEST"], {"TEST": df})
        assert len(signals) == 0

    def test_score_higher_with_eps_catalyst(self):
        df = _make_daily(gap_pct=15.0, days_ago=1)
        with_eps    = self.det.scan(["TEST"], {"TEST": df}, {"TEST"})
        without_eps = self.det.scan(["TEST"], {"TEST": df}, set())
        if with_eps and without_eps:
            assert with_eps[0].score > without_eps[0].score
