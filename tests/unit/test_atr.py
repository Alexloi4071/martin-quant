from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from martin_quant.features.atr import (
    add_atr_features,
    compute_adr,
    compute_atr,
    compute_true_range,
)


@pytest.fixture()
def ohlcv_df() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = 100
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({
        "timestamp": pd.date_range("2023-01-01", periods=n, freq="D"),
        "open":   close - rng.uniform(0, 1, n),
        "high":   close + rng.uniform(0.5, 2.0, n),
        "low":    close - rng.uniform(0.5, 2.0, n),
        "close":  close,
        "volume": rng.integers(100_000, 500_000, n),
    })


class TestComputeTrueRange:
    def test_length(self, ohlcv_df: pd.DataFrame) -> None:
        tr = compute_true_range(ohlcv_df)
        assert len(tr) == len(ohlcv_df)

    def test_tr_non_negative(self, ohlcv_df: pd.DataFrame) -> None:
        tr = compute_true_range(ohlcv_df)
        assert (tr.dropna() >= 0).all()

    def test_missing_col_raises(self, ohlcv_df: pd.DataFrame) -> None:
        df = ohlcv_df.drop(columns=["high"])
        with pytest.raises(KeyError):
            compute_true_range(df)

    def test_first_bar_nan(self, ohlcv_df: pd.DataFrame) -> None:
        tr = compute_true_range(ohlcv_df)
        assert pd.isna(tr.iloc[0])


class TestComputeAtr:
    def test_invalid_period(self, ohlcv_df: pd.DataFrame) -> None:
        with pytest.raises(ValueError):
            compute_atr(ohlcv_df, period=0)

    def test_output_positive(self, ohlcv_df: pd.DataFrame) -> None:
        atr = compute_atr(ohlcv_df, period=14)
        assert (atr.dropna() > 0).all()

    def test_name(self, ohlcv_df: pd.DataFrame) -> None:
        atr = compute_atr(ohlcv_df, period=14)
        assert atr.name == "atr_14"


class TestComputeAdr:
    def test_invalid_period(self, ohlcv_df: pd.DataFrame) -> None:
        with pytest.raises(ValueError):
            compute_adr(ohlcv_df, period=0)

    def test_output_positive(self, ohlcv_df: pd.DataFrame) -> None:
        adr = compute_adr(ohlcv_df, period=20)
        assert (adr.dropna() > 0).all()

    def test_name(self, ohlcv_df: pd.DataFrame) -> None:
        adr = compute_adr(ohlcv_df, period=20)
        assert adr.name == "adr_20"


class TestAddAtrFeatures:
    def test_columns_present(self, ohlcv_df: pd.DataFrame) -> None:
        result = add_atr_features(ohlcv_df, atr_periods=(14,), adr_periods=(20,))
        assert "atr_14" in result.columns
        assert "atr_14_pct" in result.columns
        assert "adr_20" in result.columns
        assert "adr_20_pct" in result.columns

    def test_original_unchanged(self, ohlcv_df: pd.DataFrame) -> None:
        add_atr_features(ohlcv_df)
        assert "atr_14" not in ohlcv_df.columns
