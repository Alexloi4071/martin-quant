"""Martin transcript-driven market context evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from martin_quant.features.ema import compute_ema


@dataclass
class MartinMarketContextConfig:
    ema_span: int = 50
    slope_lookback: int = 5
    hard_down_day_pct: float = -2.0
    min_bars: int = 60


@dataclass
class MartinMarketContext:
    regime: str
    breakout_friendly: bool
    trade_less: bool
    short_bias_ok: bool
    avoid_new_shorts_on_open: bool
    qqq_above_ema50: bool
    iwm_above_ema50: bool
    qqq_ema50_slope_pct: float
    iwm_ema50_slope_pct: float
    qqq_day_change_pct: float
    iwm_day_change_pct: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "regime": self.regime,
            "breakout_friendly": self.breakout_friendly,
            "trade_less": self.trade_less,
            "short_bias_ok": self.short_bias_ok,
            "avoid_new_shorts_on_open": self.avoid_new_shorts_on_open,
            "qqq_above_ema50": self.qqq_above_ema50,
            "iwm_above_ema50": self.iwm_above_ema50,
            "qqq_ema50_slope_pct": round(self.qqq_ema50_slope_pct, 3),
            "iwm_ema50_slope_pct": round(self.iwm_ema50_slope_pct, 3),
            "qqq_day_change_pct": round(self.qqq_day_change_pct, 3),
            "iwm_day_change_pct": round(self.iwm_day_change_pct, 3),
            "notes": self.notes,
        }


class MartinMarketContextEvaluator:
    def __init__(self, config: Optional[MartinMarketContextConfig] = None) -> None:
        self.config = config or MartinMarketContextConfig()

    def _benchmark_state(self, df: pd.DataFrame) -> tuple[bool, float, float]:
        cfg = self.config
        ema50 = compute_ema(df["close"], cfg.ema_span)
        last_ema = float(ema50.iloc[-1])
        prev_ema = float(ema50.iloc[-cfg.slope_lookback])
        last_close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        above_ema50 = last_close > last_ema
        slope_pct = ((last_ema - prev_ema) / prev_ema * 100.0) if prev_ema else 0.0
        day_change_pct = (last_close / prev_close - 1.0) * 100.0 if prev_close else 0.0
        return above_ema50, float(slope_pct), float(day_change_pct)

    def evaluate(
        self,
        qqq_df: Optional[pd.DataFrame],
        iwm_df: Optional[pd.DataFrame],
    ) -> MartinMarketContext:
        cfg = self.config
        if qqq_df is None or iwm_df is None or len(qqq_df) < cfg.min_bars or len(iwm_df) < cfg.min_bars:
            return MartinMarketContext(
                regime="CHOPPY",
                breakout_friendly=False,
                trade_less=True,
                short_bias_ok=False,
                avoid_new_shorts_on_open=False,
                qqq_above_ema50=False,
                iwm_above_ema50=False,
                qqq_ema50_slope_pct=0.0,
                iwm_ema50_slope_pct=0.0,
                qqq_day_change_pct=0.0,
                iwm_day_change_pct=0.0,
                notes=["insufficient benchmark history"],
            )

        qqq_above, qqq_slope, qqq_day = self._benchmark_state(qqq_df)
        iwm_above, iwm_slope, iwm_day = self._benchmark_state(iwm_df)
        hard_down_day = qqq_day <= cfg.hard_down_day_pct or iwm_day <= cfg.hard_down_day_pct
        strong_count = int(qqq_above and qqq_slope > 0) + int(iwm_above and iwm_slope > 0)
        above_count = int(qqq_above) + int(iwm_above)

        notes: list[str] = []
        if hard_down_day:
            notes.append("benchmark down day >= 2pct, avoid aggressive breakout chasing")

        if strong_count == 2 and not hard_down_day:
            regime = "BULL"
            breakout_friendly = True
            trade_less = False
            short_bias_ok = False
            notes.append("QQQ and IWM both above rising EMA50")
        elif above_count >= 1 and (qqq_slope > 0 or iwm_slope > 0) and not hard_down_day:
            regime = "WEAK_BULL"
            breakout_friendly = True
            trade_less = False
            short_bias_ok = False
            notes.append("one benchmark still carrying while the other is mixed")
        elif not qqq_above and not iwm_above and qqq_slope <= 0 and iwm_slope <= 0:
            regime = "BEAR"
            breakout_friendly = False
            trade_less = True
            short_bias_ok = True
            notes.append("QQQ and IWM both below declining EMA50")
        else:
            regime = "CHOPPY"
            breakout_friendly = False
            trade_less = True
            short_bias_ok = False
            notes.append("mixed benchmark structure, size down and be selective")

        if hard_down_day and regime != "BEAR":
            trade_less = True
            breakout_friendly = False

        return MartinMarketContext(
            regime=regime,
            breakout_friendly=breakout_friendly,
            trade_less=trade_less,
            short_bias_ok=short_bias_ok,
            avoid_new_shorts_on_open=hard_down_day,
            qqq_above_ema50=qqq_above,
            iwm_above_ema50=iwm_above,
            qqq_ema50_slope_pct=qqq_slope,
            iwm_ema50_slope_pct=iwm_slope,
            qqq_day_change_pct=qqq_day,
            iwm_day_change_pct=iwm_day,
            notes=notes,
        )
