from __future__ import annotations

import pandas as pd


def compute_ema(series: pd.Series, span: int) -> pd.Series:
    if span <= 0:
        raise ValueError("span must be > 0")
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def add_ema_features(
    df: pd.DataFrame,
    price_col: str = "close",
    spans: tuple[int, ...] = (9, 20, 50, 200),
) -> pd.DataFrame:
    out = df.copy()

    if price_col not in out.columns:
        raise KeyError(f"Missing price column: {price_col}")

    for span in spans:
        out[f"ema_{span}"] = compute_ema(out[price_col], span)

    return out


def add_ema_slope_features(
    df: pd.DataFrame,
    spans: tuple[int, ...] = (9, 20, 50),
    periods: int = 3,
) -> pd.DataFrame:
    out = df.copy()

    for span in spans:
        ema_col = f"ema_{span}"
        if ema_col not in out.columns:
            raise KeyError(f"Missing EMA column: {ema_col}")
        out[f"{ema_col}_slope"] = out[ema_col].diff(periods)

    return out


def add_price_vs_ema_distance(
    df: pd.DataFrame,
    price_col: str = "close",
    spans: tuple[int, ...] = (9, 20, 50, 200),
) -> pd.DataFrame:
    out = df.copy()

    if price_col not in out.columns:
        raise KeyError(f"Missing price column: {price_col}")

    for span in spans:
        ema_col = f"ema_{span}"
        if ema_col not in out.columns:
            raise KeyError(f"Missing EMA column: {ema_col}")

        out[f"dist_{price_col}_to_{ema_col}_pct"] = (
            (out[price_col] - out[ema_col]) / out[ema_col]
        ) * 100.0

    return out


def add_ema_stack_state(
    df: pd.DataFrame,
    short_col: str = "ema_9",
    mid_col: str = "ema_20",
    long_col: str = "ema_50",
) -> pd.DataFrame:
    out = df.copy()

    for col in (short_col, mid_col, long_col):
        if col not in out.columns:
            raise KeyError(f"Missing EMA column: {col}")

    out["ema_bull_stack"] = (
        (out[short_col] > out[mid_col]) &
        (out[mid_col] > out[long_col])
    )

    out["ema_bear_stack"] = (
        (out[short_col] < out[mid_col]) &
        (out[mid_col] < out[long_col])
    )

    return out
