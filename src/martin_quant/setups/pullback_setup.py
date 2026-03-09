from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from martin_quant.core.datatypes import SetupSignal
from martin_quant.core.enums import SetupType
from martin_quant.features.atr import compute_atr
from martin_quant.features.ema import compute_ema


@dataclass(slots=True)
class PullbackConfig:
    lookback_high_days: int = 20
    min_pullback_depth_pct: float = 5.0
    max_pullback_depth_pct: float = 30.0
    max_support_distance_pct: float = 2.0
    min_history_days: int = 60
    require_close_above_ema50: bool = True
    require_ema_stack: bool = True
    first_pullback_lookback: int = 30


class PullbackSetupDetector:
    """
    Detects healthy pullback setups on a daily OHLCV DataFrame.

    A valid pullback requires:
    - Stock made a 20-day high in recent history (first_pullback_lookback)
    - Price has pulled back at least min_pullback_depth_pct from that high
    - Price is still within max_pullback_depth_pct of that high
    - Price is near a support level (EMA20 or EMA50) within max_support_distance_pct
    - Optionally: price is above EMA50 (require_close_above_ema50)
    - Optionally: EMA stack is bullish (EMA9 > EMA20 > EMA50)
    """

    def __init__(self, config: PullbackConfig | None = None) -> None:
        self.config = config or PullbackConfig()

    def detect(
        self,
        symbol: str,
        df: pd.DataFrame,
        timeframe: str = "1d",
    ) -> SetupSignal | None:
        cfg = self.config

        if len(df) < cfg.min_history_days:
            return None

        df = df.copy().sort_values("timestamp").reset_index(drop=True)
        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        ema9  = compute_ema(close, 9)
        ema20 = compute_ema(close, 20)
        ema50 = compute_ema(close, 50)

        recent = df.tail(cfg.first_pullback_lookback)
        swing_high = float(recent["high"].max())

        current_close = float(close.iloc[-1])
        current_low   = float(low.iloc[-1])

        pullback_depth_pct = (swing_high - current_close) / swing_high * 100.0
        if pullback_depth_pct < cfg.min_pullback_depth_pct:
            return None
        if pullback_depth_pct > cfg.max_pullback_depth_pct:
            return None

        current_ema50 = float(ema50.iloc[-1])
        if cfg.require_close_above_ema50 and current_close < current_ema50:
            return None

        current_ema9  = float(ema9.iloc[-1])
        current_ema20 = float(ema20.iloc[-1])
        if cfg.require_ema_stack:
            if not (current_ema9 > current_ema20 > current_ema50):
                return None

        support_levels = [current_ema20, current_ema50]
        best_support = min(support_levels, key=lambda s: abs(current_close - s))
        support_dist_pct = abs(current_close - best_support) / best_support * 100.0
        if support_dist_pct > cfg.max_support_distance_pct:
            return None

        atr14 = compute_atr(df, period=14)
        atr_val = float(atr14.iloc[-1]) if not atr14.isna().all() else 0.0

        stop = best_support - atr_val * 0.5
        risk = current_close - stop
        target = current_close + risk * 3.0

        score = self._score(pullback_depth_pct, support_dist_pct, cfg)

        context: dict[str, Any] = {
            "swing_high": swing_high,
            "pullback_depth_pct": round(pullback_depth_pct, 2),
            "support_level": round(best_support, 4),
            "support_dist_pct": round(support_dist_pct, 2),
            "ema9": round(current_ema9, 4),
            "ema20": round(current_ema20, 4),
            "ema50": round(current_ema50, 4),
            "atr14": round(atr_val, 4),
        }

        return SetupSignal(
            symbol=symbol,
            timestamp=df["timestamp"].iloc[-1],
            setup_type=SetupType.PULLBACK,
            timeframe=timeframe,
            direction="long",
            score=score,
            trigger_level=swing_high,
            invalidation_level=round(stop, 4),
            support_level=round(best_support, 4),
            resistance_level=round(swing_high, 4),
            context=context,
            notes=[f"Pullback {pullback_depth_pct:.1f}% from swing high, near EMA support"],
        )

    @staticmethod
    def _score(pullback_pct: float, support_dist_pct: float, cfg: PullbackConfig) -> float:
        depth_score = 1.0 - abs(pullback_pct - 10.0) / 10.0
        proximity_score = 1.0 - (support_dist_pct / cfg.max_support_distance_pct)
        return round(max(0.0, (depth_score * 0.5 + proximity_score * 0.5)), 3)

    def scan_universe(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        timeframe: str = "1d",
    ) -> list[SetupSignal]:
        results = []
        for symbol in symbols:
            df = ohlcv_map.get(symbol.upper())
            if df is None or df.empty:
                continue
            sig = self.detect(symbol=symbol, df=df, timeframe=timeframe)
            if sig is not None:
                results.append(sig)
        return sorted(results, key=lambda s: s.score, reverse=True)
