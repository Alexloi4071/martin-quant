"""orb_15m_trigger.py

15 分鐘 Opening Range Breakout (ORB) Trigger
=============================================
Martin Luk 策略：
  - 開市首 15 分鐘形成「Opening Range" (High / Low)
  - 股價突破 OR High 且成交量確認 → 觸發入場
  - 結合日線 setup（必須已在 watchlist）才發出信號
  - Stop = Opening Range Low
  - Target = OR High + (OR High - OR Low) × 2

使用方式:
    from martin_quant.timing.orb_15m_trigger import ORBTrigger

    trigger = ORBTrigger(equity=100_000)
    signal = trigger.check(
        symbol="NVDA",
        df_15m=nvda_15m,   # 15分鐘 OHLCV (今日)
        daily_setup_score=0.75,
    )
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class ORBConfig:
    or_bars: int = 1               # 開市首 N 根 15m bar 作為 OR（預設 1 根 = 15m)
    min_rvol: float = 1.5          # 突破柱需 RVOL >= 1.5x
    max_or_range_pct: float = 4.0  # OR range 超過 4% 不交易（risk too wide）
    min_or_range_pct: float = 0.3  # OR range 小於 0.3% 不交易（noise）
    close_above_or_pct: float = 0.1  # 收盤需站上 OR High × (1 + x%)
    per_trade_risk_pct: float = 0.5  # % of equity to risk
    r_target_multiple: float = 2.0   # Target = stop_dist × 2R
    lookback_rvol: int = 20          # bars for avg volume
    min_daily_score: float = 0.5     # daily setup score threshold


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass
class ORBSignal:
    symbol: str
    timeframe: str = "15m"
    trigger_bar_time: str = ""     # timestamp of trigger bar
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    or_high: float = 0.0
    or_low: float = 0.0
    or_range_pct: float = 0.0
    stop_pct: float = 0.0
    shares: int = 0
    risk_dollars: float = 0.0
    r_potential: float = 0.0
    rvol: float = 0.0
    trigger_reason: str = "orb_15m_breakout"

    def to_dict(self) -> dict:
        return {
            "symbol":       self.symbol,
            "timeframe":    self.timeframe,
            "trigger_time": self.trigger_bar_time,
            "entry":        round(self.entry_price, 4),
            "stop":         round(self.stop_price, 4),
            "target":       round(self.target_price, 4),
            "or_high":      round(self.or_high, 4),
            "or_low":       round(self.or_low, 4),
            "or_range_pct": round(self.or_range_pct, 2),
            "stop_pct":     round(self.stop_pct, 2),
            "shares":       self.shares,
            "risk_$":       round(self.risk_dollars, 2),
            "r_potential":  round(self.r_potential, 1),
            "rvol":         round(self.rvol, 2),
            "trigger":      self.trigger_reason,
        }


# ---------------------------------------------------------------------------
# ORB Trigger
# ---------------------------------------------------------------------------

class ORBTrigger:
    """
    15-minute Opening Range Breakout Trigger.

    Parameters
    ----------
    equity : float
    config : ORBConfig, optional
    """

    def __init__(
        self,
        equity: float = 100_000.0,
        config: Optional[ORBConfig] = None,
    ) -> None:
        self.equity = equity
        self.config = config or ORBConfig()

    def check(
        self,
        symbol: str,
        df_15m: pd.DataFrame,
        daily_setup_score: float = 0.0,
    ) -> Optional[ORBSignal]:
        """
        Check if today's 15m data has triggered an ORB breakout.

        Parameters
        ----------
        symbol : str
        df_15m : pd.DataFrame
            必須是「今日」的 15 分鐘 OHLCV，index 為時間序列。
            欄位需有: open, high, low, close, volume
        daily_setup_score : float
            來自 DailyScanner 的 setup 評分 (0~1)；低於 min_daily_score 不觸發

        Returns
        -------
        ORBSignal or None
        """
        cfg = self.config

        # 日線 setup 分數門檻
        if daily_setup_score < cfg.min_daily_score:
            return None

        if df_15m is None or len(df_15m) < cfg.or_bars + 2:
            return None

        df = df_15m.copy()
        df.columns = [c.lower() for c in df.columns]
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(set(df.columns)):
            return None

        # ── Opening Range：前 or_bars 根 ─────────────────────────────────────
        or_df    = df.iloc[:cfg.or_bars]
        or_high  = float(or_df["high"].max())
        or_low   = float(or_df["low"].min())
        or_range = or_high - or_low
        or_range_pct = or_range / or_low * 100 if or_low > 0 else 0.0

        if or_range_pct > cfg.max_or_range_pct or or_range_pct < cfg.min_or_range_pct:
            return None

        # ── 掃描後續每一根是否突破 OR High ────────────────────────────────────
        post_or = df.iloc[cfg.or_bars:].reset_index(drop=False)
        avg_vol = float(df["volume"].iloc[:cfg.lookback_rvol].mean())
        if avg_vol <= 0:
            return None

        for _, row in post_or.iterrows():
            bar_close = float(row["close"])
            bar_vol   = float(row["volume"])
            rvol      = bar_vol / avg_vol

            # 條件：收盤站上 OR High + RVOL 確認
            breakout_threshold = or_high * (1 + cfg.close_above_or_pct / 100)
            if bar_close >= breakout_threshold and rvol >= cfg.min_rvol:
                entry = bar_close
                stop  = or_low
                stop_pct = (entry - stop) / entry * 100
                if stop_pct <= 0:
                    continue

                stop_dist    = entry - stop
                target       = entry + stop_dist * cfg.r_target_multiple
                risk_dollars = self.equity * cfg.per_trade_risk_pct / 100
                shares       = max(1, int(risk_dollars / stop_dist))
                r_potential  = (target - entry) / stop_dist

                # 取觸發柱的時間戳
                bar_time = str(row.get("index", ""))

                return ORBSignal(
                    symbol=symbol,
                    timeframe="15m",
                    trigger_bar_time=bar_time,
                    entry_price=round(entry, 4),
                    stop_price=round(stop, 4),
                    target_price=round(target, 4),
                    or_high=round(or_high, 4),
                    or_low=round(or_low, 4),
                    or_range_pct=round(or_range_pct, 2),
                    stop_pct=round(stop_pct, 2),
                    shares=shares,
                    risk_dollars=round(risk_dollars, 2),
                    r_potential=round(r_potential, 1),
                    rvol=round(rvol, 2),
                    trigger_reason="orb_15m_breakout",
                )

        return None  # no breakout yet

    def get_or_levels(
        self,
        df_15m: pd.DataFrame,
    ) -> dict:
        """
        僅計算 Opening Range 高低點（用於 alert / chart）
        Returns dict with or_high, or_low, or_range_pct
        """
        if df_15m is None or len(df_15m) < self.config.or_bars:
            return {}
        df = df_15m.copy()
        df.columns = [c.lower() for c in df.columns]
        or_df   = df.iloc[:self.config.or_bars]
        or_high = float(or_df["high"].max())
        or_low  = float(or_df["low"].min())
        or_pct  = (or_high - or_low) / or_low * 100 if or_low > 0 else 0.0
        return {
            "or_high":      round(or_high, 4),
            "or_low":       round(or_low, 4),
            "or_range_pct": round(or_pct, 2),
            "or_target":    round(or_high + (or_high - or_low) * 2, 4),
            "or_stop":      round(or_low, 4),
        }
