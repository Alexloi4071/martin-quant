"""volume_quality.py  (Batch 15 — New module)

Volume analysis helpers for Martin Luk strategy:
  - VUD (Volume Under Dryness): volume drying up during pullback
  - RVOL: relative volume vs moving average
  - Accumulation / Distribution detection

Martin core rule: "回調不可怕，放量回調才可怕"
VUD confirms that sellers are NOT present — healthy pullback.

Public API:
  calc_vol_ratio(volume, avg_window)              → pd.Series (ratio per bar)
  is_volume_dry(volume, avg_window, lookback, threshold) → bool
  calc_rvol(volume, avg_window)                   → pd.Series
  add_volume_features(df)                         → pd.DataFrame
"""
from __future__ import annotations

import pandas as pd


def calc_vol_ratio(
    volume: pd.Series,
    avg_window: int = 20,
) -> pd.Series:
    """
    Return per-bar volume ratio = volume / rolling_avg.

    Values < 1.0 = below average (drying up)
    Values > 1.0 = above average (expanding)
    """
    avg = volume.rolling(avg_window, min_periods=max(1, avg_window // 2)).mean()
    return (volume / avg).rename("vol_ratio")


def is_volume_dry(
    volume: pd.Series,
    avg_window: int = 20,
    lookback: int = 5,
    threshold: float = 0.80,
    min_dry_bars: int = 3,
) -> bool:
    """
    Check if volume is drying up over the last `lookback` bars.

    VUD condition (Martin Luk):
    - At least `min_dry_bars` of the last `lookback` bars must have
      volume < `threshold` * 20-day average volume
    - Default: 3 of last 5 bars below 80% of avg volume

    Parameters
    ----------
    volume      : pd.Series  OHLCV volume column
    avg_window  : int        rolling window for avg volume (default 20)
    lookback    : int        recent bars to check (default 5)
    threshold   : float      ratio below which volume is "dry" (default 0.80)
    min_dry_bars: int        min bars that must be dry (default 3)

    Returns
    -------
    bool  True = volume is drying up (Martin VUD confirmed)
    """
    if len(volume) < avg_window + lookback:
        return False

    ratio       = calc_vol_ratio(volume, avg_window)
    recent_ratio = ratio.iloc[-lookback:]
    dry_count   = int((recent_ratio < threshold).sum())
    return dry_count >= min_dry_bars


def calc_rvol(
    volume: pd.Series,
    avg_window: int = 20,
) -> pd.Series:
    """
    Relative Volume (RVOL) = today's volume / N-day avg.

    Alias for calc_vol_ratio with a more intuitive name for breakout analysis.
    RVOL > 1.5 on breakout day = institutional participation confirmed.
    """
    return calc_vol_ratio(volume, avg_window).rename("rvol")


def add_volume_features(
    df: pd.DataFrame,
    avg_window: int = 20,
    vud_lookback: int = 5,
    vud_threshold: float = 0.80,
) -> pd.DataFrame:
    """
    Add volume quality columns to a DataFrame copy:
      - vol_ratio       : volume / 20d avg (per bar)
      - rvol            : alias of vol_ratio (breakout context)
      - vol_dry_up      : bool — volume drying (Martin VUD signal)
      - vol_20d_avg     : rolling 20-day average volume

    Parameters
    ----------
    df            : pd.DataFrame  must contain 'volume' column
    avg_window    : int            rolling window (default 20)
    vud_lookback  : int            lookback for VUD check (default 5)
    vud_threshold : float          VUD threshold ratio (default 0.80)

    Returns
    -------
    pd.DataFrame  copy with added columns
    """
    if "volume" not in df.columns:
        raise KeyError("DataFrame must have a 'volume' column")

    out             = df.copy()
    vol             = out["volume"]
    avg_vol         = vol.rolling(avg_window, min_periods=max(1, avg_window // 2)).mean()
    out["vol_20d_avg"] = avg_vol
    out["vol_ratio"]   = vol / avg_vol
    out["rvol"]        = out["vol_ratio"]   # same value, different semantic name

    # VUD per bar: True if this bar's volume is "dry"
    out["vol_dry_bar"] = out["vol_ratio"] < vud_threshold

    # VUD rolling: True if the last vud_lookback bars are mostly dry
    out["vol_dry_up"] = (
        out["vol_dry_bar"]
        .rolling(vud_lookback, min_periods=1)
        .sum()
        .ge(3)   # at least 3 of last 5 bars dry
    )

    return out
