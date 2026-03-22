"""Compatibility aliases for market regime exports."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


class MarketRegime(str, Enum):
    BULL = "bull"
    CAUTION = "neutral"
    BEAR = "bear"


@dataclass
class MarketRegimeConfig:
    ema_fast: int = 21
    ema_slow: int = 50
    new_high_lookback: int = 50
    near_high_pct: float = 5.0
    breadth_bull_min: float = 60.0
    breadth_bear_max: float = 40.0
    min_bars: int = 60


@dataclass
class RegimeState:
    regime: MarketRegime
    spy_trend: str
    iwm_trend: str
    spy_near_high: bool
    iwm_near_high: bool
    breadth_pct: Optional[float]
    description: str

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "spy_trend": self.spy_trend,
            "iwm_trend": self.iwm_trend,
            "spy_near_high": self.spy_near_high,
            "iwm_near_high": self.iwm_near_high,
            "breadth_pct": self.breadth_pct,
            "description": self.description,
        }

    @property
    def is_bull(self) -> bool:
        return self.regime == MarketRegime.BULL

    @property
    def is_bear(self) -> bool:
        return self.regime == MarketRegime.BEAR

    @property
    def position_size_factor(self) -> float:
        return {MarketRegime.BULL: 1.0, MarketRegime.CAUTION: 0.5, MarketRegime.BEAR: 0.0}[self.regime]


# Older code expects MarketRegimeResult. Keep it as an alias.
MarketRegimeResult = RegimeState


def _trend_from_ema(df: pd.DataFrame, fast: int, slow: int) -> tuple[str, bool]:
    close = df["close"]
    ema_f = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_s = close.ewm(span=slow, adjust=False, min_periods=slow).mean()

    last_close = close.iloc[-1]
    last_f = ema_f.iloc[-1]
    last_s = ema_s.iloc[-1]

    if last_close > last_f > last_s:
        trend = "up"
    elif last_close < last_f < last_s:
        trend = "down"
    else:
        trend = "mixed"

    high_52w = close.tail(252).max()
    near_high = (high_52w - last_close) / high_52w * 100 < 5.0
    return trend, near_high


class MarketRegimeFilter:
    def __init__(self, config: Optional[MarketRegimeConfig] = None) -> None:
        self.config = config or MarketRegimeConfig()

    def evaluate(
        self,
        spy_df: pd.DataFrame,
        iwm_df: Optional[pd.DataFrame] = None,
        breadth_pct: Optional[float] = None,
    ) -> RegimeState:
        cfg = self.config

        if len(spy_df) < cfg.min_bars:
            return RegimeState(
                regime=MarketRegime.CAUTION,
                spy_trend="mixed",
                iwm_trend="mixed",
                spy_near_high=False,
                iwm_near_high=False,
                breadth_pct=breadth_pct,
                description="Insufficient SPY history, defaulting to CAUTION.",
            )

        spy_trend, spy_near_high = _trend_from_ema(spy_df, cfg.ema_fast, cfg.ema_slow)

        if iwm_df is not None and len(iwm_df) >= cfg.min_bars:
            iwm_trend, iwm_near_high = _trend_from_ema(iwm_df, cfg.ema_fast, cfg.ema_slow)
        else:
            iwm_trend, iwm_near_high = spy_trend, spy_near_high

        if breadth_pct is not None:
            if breadth_pct < cfg.breadth_bear_max:
                regime = MarketRegime.BEAR
                description = f"Market BEAR: breadth {breadth_pct:.0f}% below threshold."
            elif breadth_pct > cfg.breadth_bull_min:
                regime = MarketRegime.BULL
                description = f"Market BULL: breadth {breadth_pct:.0f}% above threshold."
            else:
                regime = MarketRegime.CAUTION
                description = f"Market CAUTION: breadth {breadth_pct:.0f}% neutral."
        elif spy_trend == "up" and iwm_trend == "up":
            regime = MarketRegime.BULL
            description = "SPY and IWM both trending up."
        elif spy_trend == "down" and iwm_trend == "down":
            regime = MarketRegime.BEAR
            description = "SPY and IWM both trending down."
        else:
            regime = MarketRegime.CAUTION
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
