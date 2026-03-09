"""tests/unit/test_theme_momentum.py

Unit tests for universe/theme_momentum.py
"""
import pandas as pd
import numpy as np
import pytest

from martin_quant.universe.theme_momentum import (
    ThemeMomentumCalculator,
    ThemeMomentumConfig,
    ThemeStats,
)


def _make_trending_df(
    n: int = 300,
    start: float = 100.0,
    slope: float = 0.5,
) -> pd.DataFrame:
    closes = [start + slope * i for i in range(n)]
    return pd.DataFrame({
        "open":   closes, "high": [c * 1.01 for c in closes],
        "low":    [c * 0.99 for c in closes], "close": closes,
        "volume": [2_000_000] * n,
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))


def _make_flat_df(n: int = 300, price: float = 50.0) -> pd.DataFrame:
    return pd.DataFrame({
        "open":   [price] * n, "high": [price * 1.005] * n,
        "low":    [price * 0.995] * n, "close": [price] * n,
        "volume": [500_000] * n,
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))


class TestThemeMomentumCalculator:
    def setup_method(self):
        self.calc = ThemeMomentumCalculator(config=ThemeMomentumConfig(
            min_stocks_per_theme=2,
            leading_rs_pct_threshold=60.0,
        ))

    def test_hot_theme_ranked_first(self):
        ohlcv = {
            "NVDA":  _make_trending_df(slope=1.0),
            "AMD":   _make_trending_df(slope=0.9),
            "IONQ":  _make_flat_df(price=20.0),
            "RGTI":  _make_flat_df(price=15.0),
        }
        theme_map = {
            "AI":      ["NVDA", "AMD"],
            "Quantum": ["IONQ", "RGTI"],
        }
        rankings = self.calc.rank(ohlcv, theme_map=theme_map)
        assert len(rankings) == 2
        # AI (trending) should rank above Quantum (flat)
        assert rankings[0].theme == "AI"
        assert rankings[0].composite_score > rankings[1].composite_score

    def test_hot_state_on_strong_theme(self):
        ohlcv = {
            "NVDA": _make_trending_df(slope=1.5),
            "AMD":  _make_trending_df(slope=1.2),
        }
        theme_map = {"AI": ["NVDA", "AMD"]}
        rankings = self.calc.rank(ohlcv, theme_map=theme_map)
        assert len(rankings) == 1
        # Both trending strongly → should be hot or cooling
        assert rankings[0].momentum_state in ("hot", "cooling")

    def test_skips_theme_with_insufficient_stocks(self):
        ohlcv = {"NVDA": _make_trending_df()}
        theme_map = {"AI": ["NVDA", "MISSING_SYM"]}  # only 1 available
        rankings = self.calc.rank(ohlcv, theme_map=theme_map)
        # min_stocks_per_theme=2 → should be skipped
        assert len(rankings) == 0

    def test_top_stocks_are_in_correct_theme(self):
        ohlcv = {
            "NVDA": _make_trending_df(slope=1.0),
            "AMD":  _make_trending_df(slope=0.8),
            "SMCI": _make_trending_df(slope=0.6),
        }
        theme_map = {"AI": ["NVDA", "AMD", "SMCI"]}
        rankings = self.calc.rank(ohlcv, theme_map=theme_map)
        assert len(rankings) == 1
        for sym in rankings[0].top_stocks:
            assert sym in ("NVDA", "AMD", "SMCI")
