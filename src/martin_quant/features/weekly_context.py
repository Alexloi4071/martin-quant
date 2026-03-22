from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class WeeklyContextConfig:
    ema_fast: int = 9
    ema_mid: int = 21
    ema_slow: int = 50
    rs_lookback_weeks: int = 26
    close_strength_bull_min: float = 0.6
    close_strength_bear_max: float = 0.4
    min_weeks_history: int = 45


@dataclass
class WeeklyContext:
    symbol: str
    week_end: str
    ema_bull_stack: bool
    ema_bear_stack: bool
    close_strength: float
    rs_vs_spy: Optional[float]
    trend_state: str
    close_above_wema9: bool
    close_above_wema21: bool
    close_above_wema50: bool

    def is_long_favourable(self) -> bool:
        return self.trend_state != "bear" and self.close_above_wema21

    def is_short_favourable(self) -> bool:
        return self.ema_bear_stack and not self.close_above_wema21 and self.close_strength <= 0.5

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "week_end": self.week_end,
            "ema_bull_stack": self.ema_bull_stack,
            "ema_bear_stack": self.ema_bear_stack,
            "close_strength": round(self.close_strength, 3),
            "rs_vs_spy": round(self.rs_vs_spy, 2) if self.rs_vs_spy is not None else None,
            "trend_state": self.trend_state,
            "close_above_wema9": self.close_above_wema9,
            "close_above_wema21": self.close_above_wema21,
            "close_above_wema50": self.close_above_wema50,
            "long_ok": self.is_long_favourable(),
            "short_ok": self.is_short_favourable(),
        }


def _normalize_daily_df(daily_df: pd.DataFrame, timestamp_col: str = "timestamp") -> pd.DataFrame:
    df = daily_df.copy()
    if timestamp_col not in df.columns:
        df = df.reset_index().rename(columns={df.index.name or "index": timestamp_col})
    df[timestamp_col] = pd.to_datetime(df[timestamp_col])
    return df.sort_values(timestamp_col).reset_index(drop=True)


def resample_to_weekly(daily_df: pd.DataFrame, timestamp_col: str = "timestamp") -> pd.DataFrame:
    df = _normalize_daily_df(daily_df, timestamp_col=timestamp_col)
    df = df.set_index(timestamp_col)
    weekly = df.resample("W-FRI").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna(subset=["close"])
    weekly.index.name = "week_end"
    return weekly.reset_index()


def _add_weekly_ema(weekly_df: pd.DataFrame, spans: tuple[int, ...]) -> pd.DataFrame:
    out = weekly_df.copy()
    for span in spans:
        out[f"wema_{span}"] = out["close"].ewm(span=span, adjust=False, min_periods=span).mean()
    return out


def compute_weekly_close_strength(weekly_df: pd.DataFrame) -> pd.Series:
    rng = (weekly_df["high"] - weekly_df["low"]).replace(0, np.nan)
    return ((weekly_df["close"] - weekly_df["low"]) / rng).rename("weekly_close_strength")


def compute_weekly_rs(
    weekly_df: pd.DataFrame,
    benchmark_weekly: pd.DataFrame,
    lookback_weeks: int = 26,
) -> pd.Series:
    stock_close = weekly_df.set_index("week_end")["close"]
    bench_close = benchmark_weekly.set_index("week_end")["close"]
    aligned = pd.concat([stock_close, bench_close], axis=1, join="inner")
    aligned.columns = ["stock", "bench"]
    rs = (aligned["stock"].pct_change(lookback_weeks) - aligned["bench"].pct_change(lookback_weeks)) * 100.0
    return rs.rename("weekly_rs_vs_bench")


def get_weekly_context(
    symbol: str,
    daily_df: pd.DataFrame,
    spy_daily_df: Optional[pd.DataFrame] = None,
    config: Optional[WeeklyContextConfig] = None,
) -> Optional[WeeklyContext]:
    cfg = config or WeeklyContextConfig()
    weekly = resample_to_weekly(daily_df)
    if len(weekly) < cfg.min_weeks_history:
        return None

    weekly = _add_weekly_ema(weekly, (cfg.ema_fast, cfg.ema_mid, cfg.ema_slow))
    weekly["close_strength"] = compute_weekly_close_strength(weekly)
    weekly["bull_stack"] = (weekly[f"wema_{cfg.ema_fast}"] > weekly[f"wema_{cfg.ema_mid}"]) & (weekly[f"wema_{cfg.ema_mid}"] > weekly[f"wema_{cfg.ema_slow}"])
    weekly["bear_stack"] = (weekly[f"wema_{cfg.ema_fast}"] < weekly[f"wema_{cfg.ema_mid}"]) & (weekly[f"wema_{cfg.ema_mid}"] < weekly[f"wema_{cfg.ema_slow}"])

    rs_val: Optional[float] = None
    if spy_daily_df is not None:
        spy_weekly = resample_to_weekly(spy_daily_df)
        rs_series = compute_weekly_rs(weekly, spy_weekly, lookback_weeks=cfg.rs_lookback_weeks)
        weekly = weekly.set_index("week_end")
        weekly["weekly_rs"] = rs_series
        weekly = weekly.reset_index()
        if "weekly_rs" in weekly.columns and not pd.isna(weekly["weekly_rs"].iloc[-1]):
            rs_val = float(weekly["weekly_rs"].iloc[-1])

    last = weekly.iloc[-1]
    bull_stack = bool(last["bull_stack"])
    bear_stack = bool(last["bear_stack"])
    if bull_stack and float(last["close_strength"]) >= cfg.close_strength_bull_min:
        trend_state = "bull"
    elif bear_stack and float(last["close_strength"]) <= cfg.close_strength_bear_max:
        trend_state = "bear"
    else:
        trend_state = "neutral"

    wema9 = last.get(f"wema_{cfg.ema_fast}", np.nan)
    wema21 = last.get(f"wema_{cfg.ema_mid}", np.nan)
    wema50 = last.get(f"wema_{cfg.ema_slow}", np.nan)
    close = float(last["close"])

    return WeeklyContext(
        symbol=symbol,
        week_end=str(last["week_end"])[:10],
        ema_bull_stack=bull_stack,
        ema_bear_stack=bear_stack,
        close_strength=float(last["close_strength"]),
        rs_vs_spy=rs_val,
        trend_state=trend_state,
        close_above_wema9=bool(not np.isnan(wema9) and close > float(wema9)),
        close_above_wema21=bool(not np.isnan(wema21) and close > float(wema21)),
        close_above_wema50=bool(not np.isnan(wema50) and close > float(wema50)),
    )
