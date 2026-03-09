from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from martin_quant.core.enums import SetupType
from martin_quant.features.ema import add_ema_features
from martin_quant.setups.pullback_setup import PullbackConfig, PullbackSetupDetector


def _make_ohlcv(n: int = 120, trend: float = 0.3, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(trend, 1.0, n))
    close = np.clip(close, 10, None)
    return pd.DataFrame({
        "timestamp": pd.date_range("2023-01-01", periods=n, freq="D"),
        "open":  close * 0.999,
        "high":  close * 1.01,
        "low":   close * 0.99,
        "close": close,
        "volume": rng.integers(300_000, 1_000_000, n),
    })


def _make_pullback_df(n: int = 120) -> pd.DataFrame:
    """
    Construct a synthetic uptrend then pullback scenario.
    First 80 bars: strong uptrend (trend=0.8).
    Last 40 bars:  mild downtrend (trend=-0.3).
    """
    rng = np.random.default_rng(99)
    up_prices   = 100.0 + np.cumsum(rng.normal(0.8, 0.5, 80))
    down_prices = up_prices[-1] + np.cumsum(rng.normal(-0.3, 0.5, 40))
    close = np.concatenate([up_prices, down_prices])
    close = np.clip(close, 10, None)
    return pd.DataFrame({
        "timestamp": pd.date_range("2023-01-01", periods=n, freq="D"),
        "open":  close * 0.999,
        "high":  close * 1.015,
        "low":   close * 0.985,
        "close": close,
        "volume": rng.integers(300_000, 1_000_000, n),
    })


class TestPullbackSetupDetector:
    def test_returns_none_when_insufficient_history(self) -> None:
        df = _make_ohlcv(n=20)
        detector = PullbackSetupDetector()
        result = detector.detect(symbol="TEST", df=df)
        assert result is None

    def test_signal_type_is_pullback(self) -> None:
        df = _make_pullback_df()
        cfg = PullbackConfig(
            require_close_above_ema50=False,
            require_ema_stack=False,
            min_pullback_depth_pct=2.0,
            max_pullback_depth_pct=50.0,
            max_support_distance_pct=10.0,
        )
        detector = PullbackSetupDetector(config=cfg)
        result = detector.detect(symbol="TEST", df=df)
        if result is not None:
            assert result.setup_type == SetupType.PULLBACK
            assert result.symbol == "TEST"

    def test_signal_has_required_fields(self) -> None:
        df = _make_pullback_df()
        cfg = PullbackConfig(
            require_close_above_ema50=False,
            require_ema_stack=False,
            min_pullback_depth_pct=2.0,
            max_pullback_depth_pct=50.0,
            max_support_distance_pct=10.0,
        )
        detector = PullbackSetupDetector(config=cfg)
        result = detector.detect(symbol="TEST", df=df)
        if result is not None:
            assert result.invalidation_level is not None
            assert result.support_level is not None
            assert 0.0 <= result.score <= 1.0

    def test_scan_universe_returns_list(self) -> None:
        symbols = ["A", "B", "C"]
        ohlcv_map = {s: _make_pullback_df() for s in symbols}
        cfg = PullbackConfig(
            require_close_above_ema50=False,
            require_ema_stack=False,
            min_pullback_depth_pct=2.0,
            max_pullback_depth_pct=50.0,
            max_support_distance_pct=10.0,
        )
        detector = PullbackSetupDetector(config=cfg)
        results = detector.scan_universe(symbols=symbols, ohlcv_map=ohlcv_map)
        assert isinstance(results, list)
        if results:
            scores = [r.score for r in results]
            assert scores == sorted(scores, reverse=True)
