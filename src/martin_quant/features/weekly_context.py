"""weekly_context.py

週線圖過濾層 — Martin Luk 策略第二層確認

功能:
  1. 從日線 OHLCV 重採樣到週線
  2. 計算週線 EMA stack 狀態 (9/20/40)
  3. 計算週線 Relative Strength vs 基準 (SPY)
  4. 計算週線收盤強度 (close 在週 range 的位置)
  5. 判斷週線趨勢狀態 (bull / neutral / bear)

Martin 影片 3:19:15 重點:
  - 不做空週線向上突破的股票
  - 週線 EMA stack 多頭 = 優先 long setup
  - 週線收盤在高位 (> 70% of week range) = 強勢確認
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class WeeklyContextConfig:
    ema_fast: int = 9
    ema_mid: int = 20
    ema_slow: int = 40
    rs_lookback_weeks: int = 52      # 週線 RS 回看週數 (≈ 1年)
    close_strength_bull_min: float = 0.6   # 收盤在週 range >= 60% = 強
    close_strength_bear_max: float = 0.4   # 收盤在週 range <= 40% = 弱
    min_weeks_history: int = 45             # 最少需要 45 週歷史


# ---------------------------------------------------------------------------
# Weekly resampling
# ---------------------------------------------------------------------------

def resample_to_weekly(daily_df: pd.DataFrame, timestamp_col: str = "timestamp") -> pd.DataFrame:
    """
    Convert daily OHLCV to weekly OHLCV (week ending Friday).

    Parameters
    ----------
    daily_df : pd.DataFrame
        Daily bars with columns: timestamp, open, high, low, close, volume.
    timestamp_col : str

    Returns
    -------
    pd.DataFrame
        Weekly OHLCV, indexed by week-end date.
    """
    df = daily_df.copy()
    df[timestamp_col] = pd.to_datetime(df[timestamp_col])
    df = df.set_index(timestamp_col).sort_index()

    weekly = df.resample("W-FRI").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["close"])

    weekly.index.name = "week_end"
    return weekly.reset_index()


# ---------------------------------------------------------------------------
# EMA helpers on weekly data
# ---------------------------------------------------------------------------

def _add_weekly_ema(weekly_df: pd.DataFrame, spans: tuple[int, ...]) -> pd.DataFrame:
    df = weekly_df.copy()
    for span in spans:
        df[f"wema_{span}"] = (
            df["close"]
            .ewm(span=span, adjust=False, min_periods=span)
            .mean()
        )
    return df


def _weekly_ema_stack(weekly_df: pd.DataFrame, fast: int, mid: int, slow: int) -> pd.Series:
    """True when wema_fast > wema_mid > wema_slow (bull stack)."""
    f = weekly_df[f"wema_{fast}"]
    m = weekly_df[f"wema_{mid}"]
    s = weekly_df[f"wema_{slow}"]
    return (f > m) & (m > s)


def _weekly_ema_bear_stack(weekly_df: pd.DataFrame, fast: int, mid: int, slow: int) -> pd.Series:
    """True when wema_fast < wema_mid < wema_slow (bear stack)."""
    f = weekly_df[f"wema_{fast}"]
    m = weekly_df[f"wema_{mid}"]
    s = weekly_df[f"wema_{slow}"]
    return (f < m) & (m < s)


# ---------------------------------------------------------------------------
# Relative Strength on weekly data
# ---------------------------------------------------------------------------

def compute_weekly_rs(
    weekly_df: pd.DataFrame,
    benchmark_weekly: pd.DataFrame,
    lookback_weeks: int = 52,
) -> pd.Series:
    """
    Weekly RS ratio = stock close / benchmark close, normalised to 100 at lookback start.
    Higher = stock outperforming benchmark (SPY).
    """
    stock_close     = weekly_df.set_index("week_end")["close"]
    benchmark_close = benchmark_weekly.set_index("week_end")["close"]

    aligned = pd.concat([stock_close, benchmark_close], axis=1, join="inner")
    aligned.columns = ["stock", "bench"]

    # Percentage performance over rolling window
    rs = (
        aligned["stock"].pct_change(lookback_weeks)
        - aligned["bench"].pct_change(lookback_weeks)
    ) * 100  # in pct points

    return rs.rename("weekly_rs_vs_bench")


# ---------------------------------------------------------------------------
# Close strength
# ---------------------------------------------------------------------------

def compute_weekly_close_strength(weekly_df: pd.DataFrame) -> pd.Series:
    """
    Where did close land inside the weekly range?
    0.0 = closed at the low, 1.0 = closed at the high.
    """
    rng = (weekly_df["high"] - weekly_df["low"]).replace(0, np.nan)
    strength = (weekly_df["close"] - weekly_df["low"]) / rng
    return strength.rename("weekly_close_strength")


# ---------------------------------------------------------------------------
# Main context builder
# ---------------------------------------------------------------------------

@dataclass
class WeeklyContext:
    symbol: str
    week_end: str
    ema_bull_stack: bool          # wema9 > wema20 > wema40
    ema_bear_stack: bool          # wema9 < wema20 < wema40
    close_strength: float         # 0-1, position of close in weekly range
    rs_vs_spy: Optional[float]    # relative strength vs SPY in pct pts
    trend_state: str              # "bull" | "neutral" | "bear"
    close_above_wema20: bool
    close_above_wema40: bool

    def is_long_favourable(self) -> bool:
        """Martin rule: only take long setups if weekly is NOT bear stacked."""
        return not self.ema_bear_stack

    def is_short_favourable(self) -> bool:
        """Only short when weekly is bear stacked AND close is weak."""
        return self.ema_bear_stack and self.close_strength < 0.4

    def to_dict(self) -> dict:
        return {
            "symbol":            self.symbol,
            "week_end":          self.week_end,
            "ema_bull_stack":    self.ema_bull_stack,
            "ema_bear_stack":    self.ema_bear_stack,
            "close_strength":    round(self.close_strength, 3),
            "rs_vs_spy":         round(self.rs_vs_spy, 2) if self.rs_vs_spy is not None else None,
            "trend_state":       self.trend_state,
            "close_above_wema20": self.close_above_wema20,
            "close_above_wema40": self.close_above_wema40,
            "long_ok":           self.is_long_favourable(),
            "short_ok":          self.is_short_favourable(),
        }


def get_weekly_context(
    symbol: str,
    daily_df: pd.DataFrame,
    spy_daily_df: Optional[pd.DataFrame] = None,
    config: Optional[WeeklyContextConfig] = None,
) -> Optional[WeeklyContext]:
    """
    Compute weekly context for the most recent completed week.

    Parameters
    ----------
    symbol : str
    daily_df : pd.DataFrame
        Daily OHLCV for the stock.
    spy_daily_df : pd.DataFrame, optional
        Daily OHLCV for SPY (benchmark). If None, RS is not computed.
    config : WeeklyContextConfig, optional

    Returns
    -------
    WeeklyContext or None if insufficient history.
    """
    cfg = config or WeeklyContextConfig()
    weekly = resample_to_weekly(daily_df)

    if len(weekly) < cfg.min_weeks_history:
        return None

    weekly = _add_weekly_ema(weekly, (cfg.ema_fast, cfg.ema_mid, cfg.ema_slow))
    weekly["bull_stack"]     = _weekly_ema_stack(weekly, cfg.ema_fast, cfg.ema_mid, cfg.ema_slow)
    weekly["bear_stack"]     = _weekly_ema_bear_stack(weekly, cfg.ema_fast, cfg.ema_mid, cfg.ema_slow)
    weekly["close_strength"] = compute_weekly_close_strength(weekly)

    rs_val: Optional[float] = None
    if spy_daily_df is not None:
        spy_weekly = resample_to_weekly(spy_daily_df)
        rs_series = compute_weekly_rs(
            weekly.rename(columns={"week_end": "week_end"}),
            spy_weekly,
            lookback_weeks=cfg.rs_lookback_weeks,
        )
        weekly = weekly.set_index("week_end")
        weekly["weekly_rs"] = rs_series
        weekly = weekly.reset_index()
        rs_val = float(weekly["weekly_rs"].iloc[-1]) if "weekly_rs" in weekly.columns else None

    last       = weekly.iloc[-1]
    bull_stack = bool(last["bull_stack"])
    bear_stack = bool(last["bear_stack"])

    if bull_stack:
        trend_state = "bull"
    elif bear_stack:
        trend_state = "bear"
    else:
        trend_state = "neutral"

    wema20 = last.get(f"wema_{cfg.ema_mid}", np.nan)
    wema40 = last.get(f"wema_{cfg.ema_slow}", np.nan)

    return WeeklyContext(
        symbol=symbol,
        week_end=str(last["week_end"])[:10],
        ema_bull_stack=bull_stack,
        ema_bear_stack=bear_stack,
        close_strength=float(last["close_strength"]),
        rs_vs_spy=rs_val,
        trend_state=trend_state,
        close_above_wema20=bool(last["close"] > wema20) if not np.isnan(wema20) else False,
        close_above_wema40=bool(last["close"] > wema40) if not np.isnan(wema40) else False,
    )
