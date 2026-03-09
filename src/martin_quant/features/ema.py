"""ema.py  (v2 — corrected EMA spans per Martin Luk strategy)

IMPORTANT FIX: Martin Luk uses 9 / 21 / 50 / 150 EMA — NOT 9/20/50.
  - 21 EMA = short-term trend / immediate support
  - 50 EMA = intermediate trend
  - 150 EMA = long-term trend (代替 200 MA，更靈敏)

Public API:
  compute_ema(series, span)                  → pd.Series
  add_ema_features(df, spans, price_col)     → pd.DataFrame (copy)
  add_ema_slope_features(df, spans)          → pd.DataFrame (copy)
  add_price_vs_ema_distance(df, spans)       → pd.DataFrame (copy)
  add_ema_stack_state(df)                    → pd.DataFrame (copy)
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

# Martin Luk canonical spans
DEFAULT_SPANS: tuple[int, ...] = (9, 21, 50, 150)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def compute_ema(series: pd.Series, span: int) -> pd.Series:
    """
    Compute exponential moving average.

    First (span - 1) values are NaN (min_periods = span).

    Parameters
    ----------
    series : pd.Series
    span   : int  > 0

    Returns
    -------
    pd.Series  named 'ema_{span}'
    """
    if span <= 0:
        raise ValueError("span must be > 0")
    return (
        series
        .ewm(span=span, adjust=False, min_periods=span)
        .mean()
        .rename(f"ema_{span}")
    )


# ---------------------------------------------------------------------------
# DataFrame helpers
# ---------------------------------------------------------------------------

def add_ema_features(
    df: pd.DataFrame,
    price_col: str = "close",
    spans: Sequence[int] = DEFAULT_SPANS,
) -> pd.DataFrame:
    """
    Add ema_9, ema_21, ema_50, ema_150 columns (or custom spans).
    Returns a copy — original df is NOT mutated.
    """
    if price_col not in df.columns:
        raise KeyError(f"Column '{price_col}' not found. Available: {list(df.columns)}")
    out = df.copy()
    for span in spans:
        out[f"ema_{span}"] = compute_ema(df[price_col], span)
    return out


def add_ema_slope_features(
    df: pd.DataFrame,
    spans: Sequence[int] = DEFAULT_SPANS,
    lookback: int = 5,
) -> pd.DataFrame:
    """
    Add ema_{span}_slope = (ema_today - ema_N_bars_ago) / ema_N_bars_ago * 100.
    Returns a copy.
    """
    out = df.copy()
    for span in spans:
        col = f"ema_{span}"
        if col not in out.columns:
            raise KeyError(f"'{col}' not found. Run add_ema_features first.")
        out[f"{col}_slope"] = (
            (out[col] - out[col].shift(lookback))
            / out[col].shift(lookback)
            * 100
        )
    return out


def add_price_vs_ema_distance(
    df: pd.DataFrame,
    price_col: str = "close",
    spans: Sequence[int] = DEFAULT_SPANS,
) -> pd.DataFrame:
    """
    Add dist_close_to_ema_{span}_pct = (close - ema) / ema * 100.
    Positive = above EMA, negative = below EMA.
    Returns a copy.
    """
    out = df.copy()
    for span in spans:
        col = f"ema_{span}"
        if col not in out.columns:
            raise KeyError(f"'{col}' not found. Run add_ema_features first.")
        out[f"dist_{price_col}_to_ema_{span}_pct"] = (
            (out[price_col] - out[col]) / out[col] * 100
        )
    return out


def add_ema_stack_state(
    df: pd.DataFrame,
    fast: int = 9,
    mid: int = 21,
    slow: int = 50,
    trend: int = 150,
) -> pd.DataFrame:
    """
    Add boolean columns:
      ema_bull_stack      : fast > mid > slow  (fully aligned up)
      ema_bear_stack      : fast < mid < slow  (fully aligned down)
      ema_above_150       : close > ema_150  (long-term uptrend)
      ema_bull_full       : bull_stack AND close > ema_150 (Martin's ideal long)

    Returns a copy.
    """
    out = df.copy()
    for span in (fast, mid, slow, trend):
        if f"ema_{span}" not in out.columns:
            raise KeyError(
                f"ema_{span} not found. Run add_ema_features with spans including "
                f"({fast}, {mid}, {slow}, {trend})."
            )
    f_col = out[f"ema_{fast}"]
    m_col = out[f"ema_{mid}"]
    s_col = out[f"ema_{slow}"]
    t_col = out[f"ema_{trend}"]

    out["ema_bull_stack"] = (f_col > m_col) & (m_col > s_col)
    out["ema_bear_stack"] = (f_col < m_col) & (m_col < s_col)
    out["ema_above_150"]  = out["close"] > t_col
    out["ema_bull_full"]  = out["ema_bull_stack"] & out["ema_above_150"]
    return out
