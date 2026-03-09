from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from martin_quant.core.datatypes import SetupSignal
from martin_quant.core.enums import SetupType
from martin_quant.features.atr import compute_atr
from martin_quant.features.ema import compute_ema


@dataclass(slots=True)
class BreakoutConfig:
    lookback_high_days: int = 20
    min_base_days: int = 3
    max_base_days: int = 40
    min_rvol_on_breakout: float = 1.5
    tightness_atr_multiplier: float = 0.5
    min_close_above_breakout_pct: float = 0.0


class BreakoutSetupDetector:
    """
    Detects breakout setups on a daily OHLCV DataFrame.

    A valid breakout setup requires:
    - Price is near or above the N-day high (resistance line)
    - Base is tight: daily range within tightness_atr_multiplier * ATR for min_base_days
    - Volume on trigger bar is at least min_rvol_on_breakout x 20d avg volume
    - Optionally: close is above the breakout level by min_close_above_breakout_pct
    """

    def __init__(self, config: BreakoutConfig | None = None) -> None:
        self.config = config or BreakoutConfig()

    def detect(
        self,
        symbol: str,
        df: pd.DataFrame,
        timeframe: str = "1d",
    ) -> SetupSignal | None:
        cfg = self.config

        min_required = cfg.lookback_high_days + cfg.max_base_days + 20
        if len(df) < min_required:
            return None

        df = df.copy().sort_values("timestamp").reset_index(drop=True)
        close  = df["close"]
        high   = df["high"]
        volume = df["volume"]

        resistance = float(high.iloc[-(cfg.lookback_high_days + 1):-1].max())
        current_close  = float(close.iloc[-1])
        current_high   = float(high.iloc[-1])
        current_volume = float(volume.iloc[-1])

        close_above_pct = (current_close - resistance) / resistance * 100.0
        if close_above_pct < cfg.min_close_above_breakout_pct:
            return None

        avg_volume_20d = float(volume.iloc[-21:-1].mean())
        rvol = current_volume / avg_volume_20d if avg_volume_20d > 0 else 0.0
        if rvol < cfg.min_rvol_on_breakout:
            return None

        atr14 = compute_atr(df, period=14)
        atr_val = float(atr14.iloc[-1]) if not atr14.isna().all() else 0.0
        tightness_threshold = atr_val * cfg.tightness_atr_multiplier

        base_window = df.iloc[-(cfg.max_base_days + 1):-1]
        base_ranges = base_window["high"] - base_window["low"]
        tight_bars = int((base_ranges <= tightness_threshold).sum())
        if tight_bars < cfg.min_base_days:
            return None

        ema20 = compute_ema(close, 20)
        ema50 = compute_ema(close, 50)
        current_ema20 = float(ema20.iloc[-1])
        current_ema50 = float(ema50.iloc[-1])

        stop = resistance - atr_val
        risk = current_close - stop
        target = current_close + risk * 3.0

        score = self._score(rvol, tight_bars, close_above_pct, cfg)

        context: dict[str, Any] = {
            "resistance": round(resistance, 4),
            "close_above_resistance_pct": round(close_above_pct, 2),
            "rvol": round(rvol, 2),
            "tight_bars_in_base": tight_bars,
            "atr14": round(atr_val, 4),
            "ema20": round(current_ema20, 4),
            "ema50": round(current_ema50, 4),
            "avg_volume_20d": round(avg_volume_20d, 0),
        }

        return SetupSignal(
            symbol=symbol,
            timestamp=df["timestamp"].iloc[-1],
            setup_type=SetupType.BREAKOUT,
            timeframe=timeframe,
            direction="long",
            score=score,
            trigger_level=round(resistance, 4),
            invalidation_level=round(stop, 4),
            support_level=round(current_ema20, 4),
            resistance_level=round(resistance, 4),
            context=context,
            notes=[
                f"Breakout {close_above_pct:.1f}% above resistance, "
                f"RVOL {rvol:.1f}x, {tight_bars} tight base bars"
            ],
        )

    @staticmethod
    def _score(rvol: float, tight_bars: int, close_above_pct: float, cfg: BreakoutConfig) -> float:
        rvol_score    = min(rvol / 3.0, 1.0)
        base_score    = min(tight_bars / cfg.max_base_days, 1.0)
        breakout_score = min(max(close_above_pct / 2.0, 0.0), 1.0)
        return round(rvol_score * 0.4 + base_score * 0.3 + breakout_score * 0.3, 3)

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
