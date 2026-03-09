"""tests/unit/test_market_regime.py

Unit tests for filters/market_regime.py
"""
import pandas as pd
import numpy as np
import pytest

from martin_quant.filters.market_regime import (
    MarketRegimeFilter, MarketRegime,
)


def _trending_up(n: int = 100, start: float = 100.0, slope: float = 0.3) -> pd.DataFrame:
    """Create a steadily rising DataFrame."""
    closes = [start + slope * i for i in range(n)]
    return pd.DataFrame({
        "open":   closes, "high": [c * 1.01 for c in closes],
        "low":    [c * 0.99 for c in closes], "close": closes,
        "volume": [1_000_000] * n,
    })


def _trending_down(n: int = 100, start: float = 100.0, slope: float = 0.3) -> pd.DataFrame:
    closes = [start - slope * i for i in range(n)]
    closes = [max(c, 1.0) for c in closes]
    return pd.DataFrame({
        "open":   closes, "high": [c * 1.01 for c in closes],
        "low":    [c * 0.99 for c in closes], "close": closes,
        "volume": [1_000_000] * n,
    })


class TestMarketRegimeFilter:
    def setup_method(self):
        self.filt = MarketRegimeFilter()

    def test_bull_regime_on_uptrend(self):
        spy = _trending_up(200)
        iwm = _trending_up(200)
        result = self.filt.evaluate(spy, iwm)
        assert result.regime == MarketRegime.BULL
        assert result.position_size_factor == 1.0

    def test_bear_regime_on_downtrend(self):
        spy = _trending_down(200, start=150, slope=0.4)
        iwm = _trending_down(200, start=120, slope=0.3)
        result = self.filt.evaluate(spy, iwm)
        # Deep downtrend should be BEAR or CAUTION
        assert result.regime in (MarketRegime.BEAR, MarketRegime.CAUTION)

    def test_insufficient_data_returns_caution(self):
        spy = _trending_up(20)   # too short for EMA50
        iwm = _trending_up(20)
        result = self.filt.evaluate(spy, iwm)
        assert result.regime == MarketRegime.CAUTION

    def test_position_size_factor_reduced_in_caution(self):
        spy = _trending_down(200, start=120, slope=0.1)
        iwm = _trending_up(200)
        result = self.filt.evaluate(spy, iwm)
        if result.regime == MarketRegime.CAUTION:
            assert result.position_size_factor < 1.0
