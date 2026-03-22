from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from martin_quant.core.datatypes import SetupSignal
from martin_quant.core.enums import SetupType
from martin_quant.features.candlestick import detect_engulfing_bear, detect_inside_day, detect_nr7
from martin_quant.features.ema import add_ema_features
from martin_quant.features.weekly_context import WeeklyContext


@dataclass
class ShortSetupConfig:
    ema_fast: int = 9
    ema_mid: int = 21
    ema_slow: int = 50
    max_bounce_dist_pct: float = 3.0
    require_bearish_candle: bool = True
    require_negative_rs: bool = False
    require_ema_mid_declining: bool = True
    ema_mid_slope_lookback: int = 5
    stop_buffer_pct: float = 0.5
    min_rr_ratio: float = 2.0
    min_history_bars: int = 60
    min_score: float = 0.3
    require_weekly_context: bool = False
    require_weekly_bear_for_short: bool = False


class ShortSetupDetector:
    def __init__(self, config: Optional[ShortSetupConfig] = None) -> None:
        self.config = config or ShortSetupConfig()

    def detect(
        self,
        symbol: str,
        df: pd.DataFrame,
        weekly_bear: bool = False,
        weekly_context: Optional[WeeklyContext] = None,
    ) -> Optional[SetupSignal]:
        cfg = self.config
        if len(df) < cfg.min_history_bars:
            return None
        if cfg.require_weekly_context and weekly_context is None:
            return None

        weekly_bear_active = weekly_bear or (weekly_context.is_short_favourable() if weekly_context is not None else False)
        if cfg.require_weekly_bear_for_short and not weekly_bear_active:
            return None

        df = self._normalize(df)
        df = add_ema_features(df, spans=(cfg.ema_fast, cfg.ema_mid, cfg.ema_slow))
        last = df.iloc[-1]

        ema9 = last.get(f"ema_{cfg.ema_fast}")
        ema21 = last.get(f"ema_{cfg.ema_mid}")
        ema50 = last.get(f"ema_{cfg.ema_slow}")
        close = last.get("close")
        if any(pd.isna([ema9, ema21, ema50, close])):
            return None
        if not (ema9 < ema21 < ema50):
            return None
        if close >= ema9:
            return None

        dist_pct = (ema9 - close) / ema9 * 100
        if dist_pct > cfg.max_bounce_dist_pct:
            return None
        if cfg.require_ema_mid_declining:
            ema_mid_series = df[f"ema_{cfg.ema_mid}"].tail(cfg.ema_mid_slope_lookback)
            if len(ema_mid_series) < cfg.ema_mid_slope_lookback or ema_mid_series.iloc[-1] >= ema_mid_series.iloc[0]:
                return None

        score = 0.4
        candle_tags: list[str] = []
        if cfg.require_bearish_candle:
            engulf = bool(detect_engulfing_bear(df).iloc[-1])
            inside = bool(detect_inside_day(df).iloc[-1])
            nr7 = bool(detect_nr7(df).iloc[-1])
            if engulf:
                score += 0.3
                candle_tags.append("bearish_engulfing")
            elif inside or nr7:
                score += 0.2
                candle_tags.append("inside_or_nr7")
            else:
                return None
        else:
            score += 0.2

        if weekly_bear_active:
            score += 0.2
        if score < cfg.min_score:
            return None

        entry_price = float(close)
        stop_price = float(ema21) * (1 + cfg.stop_buffer_pct / 100)
        risk_per_share = stop_price - entry_price
        if risk_per_share <= 0:
            return None
        target_price = entry_price - risk_per_share * cfg.min_rr_ratio
        if target_price <= 0:
            return None

        timestamp = last.get("timestamp", df["timestamp"].iloc[-1])
        notes = [
            f"Bear EMA stack {cfg.ema_fast}<{cfg.ema_mid}<{cfg.ema_slow}",
            f"Bounce into EMA{cfg.ema_fast} resistance at {dist_pct:.1f}% below",
        ]
        if weekly_bear_active:
            notes.append("Weekly bear context confirmed")
        if weekly_context is not None:
            notes.append(f"Weekly context: {weekly_context.trend_state}, close_strength={weekly_context.close_strength:.2f}")
        notes.extend(candle_tags)

        context = {
            "ema_fast": round(float(ema9), 4),
            "ema_mid": round(float(ema21), 4),
            "ema_slow": round(float(ema50), 4),
            "bounce_distance_pct": round(float(dist_pct), 2),
            "weekly_bear": weekly_bear_active,
            "weekly_trend_state": weekly_context.trend_state if weekly_context else None,
            "weekly_close_strength": round(weekly_context.close_strength, 3) if weekly_context else None,
            "candle_tags": candle_tags,
        }

        return SetupSignal(
            symbol=symbol,
            setup_type=SetupType.SHORT_RESISTANCE_REVERSAL,
            timestamp=timestamp,
            timeframe="1d",
            direction="short",
            score=round(min(score, 1.0), 3),
            entry_price=round(entry_price, 4),
            stop_price=round(stop_price, 4),
            target_price=round(float(target_price), 4),
            invalidation_level=round(stop_price, 4),
            resistance_level=round(float(ema9), 4),
            context=context,
            notes=notes,
        )

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if "timestamp" not in out.columns:
            out = out.reset_index().rename(columns={out.index.name or "index": "timestamp"})
        return out.sort_values("timestamp").reset_index(drop=True)

    def scan_universe(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        weekly_bear_map: Optional[dict[str, bool]] = None,
        weekly_context_map: Optional[dict[str, WeeklyContext]] = None,
    ) -> list[SetupSignal]:
        results: list[SetupSignal] = []
        weekly_map = weekly_bear_map or {}
        weekly_ctx_map = weekly_context_map or {}
        for symbol in symbols:
            df = ohlcv_map.get(symbol)
            if df is None:
                continue
            try:
                signal = self.detect(
                    symbol=symbol,
                    df=df,
                    weekly_bear=weekly_map.get(symbol, False),
                    weekly_context=weekly_ctx_map.get(symbol),
                )
            except Exception:
                continue
            if signal is not None:
                results.append(signal)
        return sorted(results, key=lambda item: item.score, reverse=True)
