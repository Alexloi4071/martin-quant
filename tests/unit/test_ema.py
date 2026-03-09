from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from martin_quant.features.ema import (
    add_ema_features,
    add_ema_slope_features,
    add_ema_stack_state,
    add_price_vs_ema_distance,
    compute_ema,
)


@pytest.fixture()
def price_series() -> pd.Series:
    rng = np.random.default_rng(42)
    prices = 100.0 + np.cumsum(rng.normal(0, 1, 300))
    return pd.Series(prices, name="close")


@pytest.fixture()
def ohlcv_df(price_series: pd.Series) -> pd.DataFrame:
    n = len(price_series)
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "timestamp": pd.date_range("2023-01-01", periods=n, freq="D"),
        "open":   price_series.values,
        "high":   price_series.values + rng.uniform(0, 2, n),
        "low":    price_series.values - rng.uniform(0, 2, n),
        "close":  price_series.values,
        "volume": rng.integers(100_000, 1_000_000, n),
    })


class TestComputeEma:
    def test_output_length(self, price_series: pd.Series) -> None:
        result = compute_ema(price_series, span=20)
        assert len(result) == len(price_series)

    def test_nan_prefix(self, price_series: pd.Series) -> None:
        span = 20
        result = compute_ema(price_series, span=span)
        assert result.iloc[:span - 1].isna().all(), "First span-1 values should be NaN"
        assert pd.notna(result.iloc[span - 1]), "Value at span-1 should be valid"

    def test_invalid_span(self, price_series: pd.Series) -> None:
        with pytest.raises(ValueError, match="span must be > 0"):
            compute_ema(price_series, span=0)

    def test_monotone_series(self) -> None:
        prices = pd.Series(range(1, 101), dtype=float)
        ema = compute_ema(prices, span=10)
        valid = ema.dropna()
        assert (valid.diff().dropna() > 0).all(), "EMA should increase for monotone rising price"


class TestAddEmaFeatures:
    def test_columns_added(self, ohlcv_df: pd.DataFrame) -> None:
        spans = (9, 20, 50)
        result = add_ema_features(ohlcv_df, price_col="close", spans=spans)
        for span in spans:
            assert f"ema_{span}" in result.columns

    def test_original_df_unchanged(self, ohlcv_df: pd.DataFrame) -> None:
        add_ema_features(ohlcv_df, spans=(9,))
        assert "ema_9" not in ohlcv_df.columns

    def test_missing_price_col_raises(self, ohlcv_df: pd.DataFrame) -> None:
        with pytest.raises(KeyError):
            add_ema_features(ohlcv_df, price_col="nonexistent")


class TestAddEmaSlopeFeatures:
    def test_slope_columns_added(self, ohlcv_df: pd.DataFrame) -> None:
        df = add_ema_features(ohlcv_df, spans=(9, 20, 50))
        result = add_ema_slope_features(df, spans=(9, 20, 50))
        for span in (9, 20, 50):
            assert f"ema_{span}_slope" in result.columns

    def test_missing_ema_col_raises(self, ohlcv_df: pd.DataFrame) -> None:
        with pytest.raises(KeyError):
            add_ema_slope_features(ohlcv_df, spans=(9,))


class TestAddPriceVsEmaDistance:
    def test_distance_columns_added(self, ohlcv_df: pd.DataFrame) -> None:
        df = add_ema_features(ohlcv_df, spans=(20,))
        result = add_price_vs_ema_distance(df, price_col="close", spans=(20,))
        assert "dist_close_to_ema_20_pct" in result.columns

    def test_distance_is_zero_when_price_equals_ema(self) -> None:
        prices = pd.Series([100.0] * 50)
        df = pd.DataFrame({"close": prices})
        df = add_ema_features(df, price_col="close", spans=(9,))
        result = add_price_vs_ema_distance(df, price_col="close", spans=(9,))
        valid = result["dist_close_to_ema_9_pct"].dropna()
        assert (valid.abs() < 1e-6).all()


class TestAddEmaStackState:
    def test_bull_stack_columns_exist(self, ohlcv_df: pd.DataFrame) -> None:
        df = add_ema_features(ohlcv_df, spans=(9, 20, 50))
        result = add_ema_stack_state(df)
        assert "ema_bull_stack" in result.columns
        assert "ema_bear_stack" in result.columns

    def test_bull_bear_mutually_exclusive(self, ohlcv_df: pd.DataFrame) -> None:
        df = add_ema_features(ohlcv_df, spans=(9, 20, 50))
        result = add_ema_stack_state(df)
        both = result["ema_bull_stack"] & result["ema_bear_stack"]
        assert not both.any(), "Cannot be both bull and bear stacked simultaneously"
