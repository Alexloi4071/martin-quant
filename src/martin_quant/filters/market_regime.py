"""market_regime.py

市場健康度過濾 — Market Regime Filter

Martin 影片 3:46:33 重點:
  - 用 IWM (Russell 2000 小型股 ETF) 衡量市場廣度
  - IWM 趨勢 + SPY 趨勢 雙重確認
  - Bull  : SPY 和 IWM 都在上升趨勢 → 全力做多
  - Caution: 混合信號       → 減半倉位
  - Bear  : 雙雙下跌        → 只做空或現金

輸出 RegimeState 供 EquityCurveSizer 和 daily_scan.py 使用。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MarketRegime(str, Enum):
    BULL     = "bull"       # full-size longs
    CAUTION  = "neutral"    # half-size, selective
    BEAR     = "bear"       # cash / shorts only


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class MarketRegimeConfig:
    # EMA spans for trend direction
    ema_fast: int = 21
    ema_slow: int = 50

    # New high lookback
    new_high_lookback: int = 50      # Is SPY/IWM within N bars of 52w high?
    near_high_pct: float = 5.0       # Within 5% of 52-week high = "near high"

    # Breadth: % of stocks above 50 DMA (if provided)
    breadth_bull_min: float = 60.0   # > 60% stocks above 50dma = bull
    breadth_bear_max: float = 40.0   # < 40% stocks above 50dma = bear

    # Minimum bars needed
    min_bars: int = 60


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class RegimeState:
    regime: MarketRegime
    spy_trend: str          # "up" | "down" | "mixed"
    iwm_trend: str          # "up" | "down" | "mixed"
    spy_near_high: bool
    iwm_near_high: bool
    breadth_pct: Optional[float]   # % stocks above 50dma (if provided)
    description: str

    def to_dict(self) -> dict:
        return {
            "regime":       self.regime.value,
            "spy_trend":    self.spy_trend,
            "iwm_trend":    self.iwm_trend,
            "spy_near_high": self.spy_near_high,
            "iwm_near_high": self.iwm_near_high,
            "breadth_pct":  self.breadth_pct,
            "description":  self.description,
        }

    @property
    def is_bull(self) -> bool:
        return self.regime == MarketRegime.BULL

    @property
    def is_bear(self) -> bool:
        return self.regime == MarketRegime.BEAR

    @property
    def position_size_factor(self) -> float:
        """Multiply base position size by this factor based on regime."""
        return {MarketRegime.BULL: 1.0, MarketRegime.CAUTION: 0.5, MarketRegime.BEAR: 0.0}[self.regime]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _trend_from_ema(
    df: pd.DataFrame,
    fast: int,
    slow: int,
) -> tuple[str, bool]:
    """
    Returns (trend_str, near_high) for the last bar.
    trend_str: "up" | "down" | "mixed"
    near_high:  close within 5% of 52-week high
    """
    close = df["close"]
    ema_f = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_s = close.ewm(span=slow, adjust=False, min_periods=slow).mean()

    last_close = close.iloc[-1]
    last_f     = ema_f.iloc[-1]
    last_s     = ema_s.iloc[-1]

    if last_close > last_f > last_s:
        trend = "up"
    elif last_close < last_f < last_s:
        trend = "down"
    else:
        trend = "mixed"

    high_52w  = close.tail(252).max()
    near_high = (high_52w - last_close) / high_52w * 100 < 5.0

    return trend, near_high


# ---------------------------------------------------------------------------
# Main filter
# ---------------------------------------------------------------------------

class MarketRegimeFilter:
    """
    Classifies the current market regime using SPY and IWM daily data.

    Usage:
        filt  = MarketRegimeFilter()
        state = filt.evaluate(spy_df=spy_daily, iwm_df=iwm_daily)
        if state.is_bear:
            # skip all long setups
    """

    def __init__(self, config: Optional[MarketRegimeConfig] = None) -> None:
        self.config = config or MarketRegimeConfig()

    def evaluate(
        self,
        spy_df: pd.DataFrame,
        iwm_df: Optional[pd.DataFrame] = None,
        breadth_pct: Optional[float] = None,
    ) -> RegimeState:
        """
        Parameters
        ----------
        spy_df : pd.DataFrame
            Daily OHLCV for SPY (must have 'close' column).
        iwm_df : pd.DataFrame, optional
            Daily OHLCV for IWM. If None, only SPY is used.
        breadth_pct : float, optional
            Percentage of stocks currently above their 50-day MA.
            Supply from a breadth data provider if available.

        Returns
        -------
        RegimeState
        """
        cfg = self.config

        if len(spy_df) < cfg.min_bars:
            return RegimeState(
                regime=MarketRegime.CAUTION,
                spy_trend="mixed",
                iwm_trend="mixed",
                spy_near_high=False,
                iwm_near_high=False,
                breadth_pct=breadth_pct,
                description="Insufficient SPY history — defaulting to CAUTION.",
            )

        spy_trend, spy_near_high = _trend_from_ema(spy_df, cfg.ema_fast, cfg.ema_slow)

        if iwm_df is not None and len(iwm_df) >= cfg.min_bars:
            iwm_trend, iwm_near_high = _trend_from_ema(iwm_df, cfg.ema_fast, cfg.ema_slow)
        else:
            iwm_trend, iwm_near_high = spy_trend, spy_near_high

        # --- Breadth override ---
        if breadth_pct is not None:
            if breadth_pct < cfg.breadth_bear_max:
                regime      = MarketRegime.BEAR
                description = f"Market BEAR: breadth {breadth_pct:.0f}% below {cfg.breadth_bear_max:.0f}% threshold."
            elif breadth_pct > cfg.breadth_bull_min:
                regime      = MarketRegime.BULL
                description = f"Market BULL: breadth {breadth_pct:.0f}% above {cfg.breadth_bull_min:.0f}% threshold."
            else:
                regime      = MarketRegime.CAUTION
                description = f"Market CAUTION: breadth {breadth_pct:.0f}% in neutral zone."

        # --- EMA trend logic ---
        elif spy_trend == "up" and iwm_trend == "up":
            regime      = MarketRegime.BULL
            description = "SPY ↑  IWM ↑  — full bull regime."
        elif spy_trend == "down" and iwm_trend == "down":
            regime      = MarketRegime.BEAR
            description = "SPY ↓  IWM ↓  — bear regime, avoid longs."
        else:
            regime      = MarketRegime.CAUTION
            description = f"Mixed signals: SPY={spy_trend}, IWM={iwm_trend}."

        return RegimeState(
            regime=regime,
            spy_trend=spy_trend,
            iwm_trend=iwm_trend,
            spy_near_high=spy_near_high,
            iwm_near_high=iwm_near_high,
            breadth_pct=breadth_pct,
            description=description,
        )
