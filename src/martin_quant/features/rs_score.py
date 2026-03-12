"""rs_score.py  (Batch 15 — New module)

Relative Strength scoring for Martin Luk universe scanning.

Fixes vs original (single 252-day RS):
  - Weighted multi-period RS: 3m×0.40 + 6m×0.30 + 9m×0.20 + 12m×0.10
  - Heavily weights RECENT momentum (Martin focuses on last 3 months)
  - This finds current leaders, not last year's winners

Martin philosophy: "我要的是最近剛冒頭的真正領頭羊"

Public API:
  calc_rs_weighted(close)                       → float
  calc_rs_percentile(close_map, symbols)        → pd.DataFrame  (ranked universe)
  add_rs_features(df)                           → pd.DataFrame
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

# Period weights: emphasise recent momentum
RS_PERIODS = {
    63:  0.40,   # ~3 months  — highest weight
    126: 0.30,   # ~6 months
    189: 0.20,   # ~9 months
    252: 0.10,   # ~12 months — lowest weight
}


def calc_rs_weighted(close: pd.Series) -> float:
    """
    Calculate multi-period weighted Relative Strength score.

    Each component = (close_today / close_N_bars_ago) - 1
    Weighted: 3m × 0.40 + 6m × 0.30 + 9m × 0.20 + 12m × 0.10

    Returns 0.0 if insufficient data.

    Parameters
    ----------
    close : pd.Series  daily close prices (sorted ascending)

    Returns
    -------
    float  weighted RS return score (not ranked yet)
    """
    total_weight = 0.0
    weighted_sum = 0.0

    for period, weight in RS_PERIODS.items():
        if len(close) >= period + 1:
            pct = float(close.iloc[-1] / close.iloc[-period] - 1)
            weighted_sum += pct * weight
            total_weight += weight

    if total_weight == 0:
        return 0.0

    # Re-normalise if some periods unavailable
    return weighted_sum / total_weight


def calc_rs_percentile(
    close_map: dict[str, pd.Series],
    symbols: Sequence[str] | None = None,
) -> pd.DataFrame:
    """
    Rank a universe of stocks by weighted RS score.

    Parameters
    ----------
    close_map : dict[ticker → pd.Series of daily closes]
    symbols   : optional subset; defaults to all keys in close_map

    Returns
    -------
    pd.DataFrame with columns:
      symbol | rs_raw | rs_percentile | rs_rank
    Sorted by rs_percentile descending (rank 1 = strongest).
    """
    syms   = list(symbols) if symbols is not None else list(close_map.keys())
    rows   = []

    for sym in syms:
        s = close_map.get(sym)
        if s is None or len(s) < 63:
            continue
        raw = calc_rs_weighted(s)
        rows.append({"symbol": sym, "rs_raw": raw})

    if not rows:
        return pd.DataFrame(columns=["symbol", "rs_raw", "rs_percentile", "rs_rank"])

    result_df = pd.DataFrame(rows)
    result_df["rs_percentile"] = (
        result_df["rs_raw"]
        .rank(pct=True)
        .mul(100)
        .round(1)
    )
    result_df["rs_rank"] = result_df["rs_raw"].rank(ascending=False).astype(int)
    return result_df.sort_values("rs_rank").reset_index(drop=True)


def add_rs_features(
    df: pd.DataFrame,
    price_col: str = "close",
) -> pd.DataFrame:
    """
    Add rs_3m, rs_6m, rs_9m, rs_12m, rs_weighted columns to a DataFrame.

    Parameters
    ----------
    df        : pd.DataFrame  must have price_col column
    price_col : str

    Returns
    -------
    pd.DataFrame  copy with RS columns added (single-stock context only,
                  not percentile-ranked — use calc_rs_percentile for ranking)
    """
    if price_col not in df.columns:
        raise KeyError(f"Column '{price_col}' not found")

    out   = df.copy()
    close = out[price_col]

    period_labels = {63: "3m", 126: "6m", 189: "9m", 252: "12m"}
    for period, label in period_labels.items():
        out[f"rs_{label}"] = close / close.shift(period) - 1

    # Rolling weighted RS (trailing)
    def _weighted_row(idx: int) -> float:
        subset = close.iloc[: idx + 1]
        return calc_rs_weighted(subset)

    # Vectorised: only compute for rows with enough history
    min_rows = min(RS_PERIODS.keys())
    rs_vals  = pd.Series(
        [
            calc_rs_weighted(close.iloc[: i + 1]) if i + 1 >= min_rows else float("nan")
            for i in range(len(close))
        ],
        index=close.index,
        name="rs_weighted",
    )
    out["rs_weighted"] = rs_vals
    return out
