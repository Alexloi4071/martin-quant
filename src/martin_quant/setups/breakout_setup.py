from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from martin_quant.core.datatypes import SetupSignal
from martin_quant.core.enums import SetupType
from martin_quant.features.atr import compute_atr
from martin_quant.features.ema import compute_ema
from martin_quant.features.weekly_context import WeeklyContext


@dataclass
class BreakoutConfig:
    lookback_high_days: int = 20
    min_base_days: int = 3
    max_base_days: int = 40
    min_rvol_on_breakout: float = 1.5
    tightness_atr_multiplier: float = 0.5
    min_close_above_breakout_pct: float = 0.0
    vcp_lookback: int = 60
    vcp_min_waves: int = 2
    vcp_max_final_pullback: float = 0.10
    require_vcp: bool = True
    require_weekly_context: bool = False
    weekly_score_bonus: float = 0.10


class BreakoutSetupDetector:
    def __init__(self, config: BreakoutConfig | None = None) -> None:
        self.cfg = config or BreakoutConfig()

    def detect(
        self,
        symbol: str,
        df: pd.DataFrame,
        timeframe: str = "1d",
        weekly_context: Optional[WeeklyContext] = None,
    ) -> SetupSignal | None:
        cfg = self.cfg
        min_required = cfg.lookback_high_days + cfg.max_base_days + 20
        if len(df) < min_required:
            return None
        if cfg.require_weekly_context and weekly_context is None:
            return None
        if weekly_context is not None and not weekly_context.is_long_favourable():
            return None

        df = self._normalize(df)
        close = df["close"]
        high = df["high"]
        volume = df["volume"]

        resistance = float(high.iloc[-(cfg.lookback_high_days + 1):-1].max())
        current_close = float(close.iloc[-1])
        current_volume = float(volume.iloc[-1])
        close_above_pct = (current_close - resistance) / resistance * 100.0
        if close_above_pct < cfg.min_close_above_breakout_pct:
            return None

        avg_volume_20d = float(volume.iloc[-21:-1].mean())
        rvol = current_volume / avg_volume_20d if avg_volume_20d > 0 else 0.0
        if rvol < cfg.min_rvol_on_breakout:
            return None

        vcp_result = self._detect_vcp(df, cfg)
        if cfg.require_vcp and not vcp_result["is_vcp"]:
            return None

        ema9 = compute_ema(close, 9)
        ema21 = compute_ema(close, 21)
        ema50 = compute_ema(close, 50)
        current_ema9 = float(ema9.iloc[-1])
        current_ema21 = float(ema21.iloc[-1])
        current_ema50 = float(ema50.iloc[-1])
        ema_stack_ok = current_ema9 > current_ema21 > current_ema50

        atr14 = compute_atr(df, period=14)
        atr_val = float(atr14.iloc[-1]) if not atr14.isna().all() else 0.0
        stop = resistance - atr_val
        risk = current_close - stop
        target = current_close + risk * 3.0
        score = self._score(rvol, vcp_result, close_above_pct, ema_stack_ok, cfg)

        weekly_bonus = 0.0
        if weekly_context is not None:
            if weekly_context.trend_state == "bull":
                weekly_bonus += cfg.weekly_score_bonus * 0.6
            if weekly_context.close_strength >= 0.6:
                weekly_bonus += cfg.weekly_score_bonus * 0.4
            score = min(1.0, round(score + weekly_bonus, 3))

        context: dict[str, Any] = {
            "resistance": round(resistance, 4),
            "close_above_resistance_pct": round(close_above_pct, 2),
            "rvol": round(rvol, 2),
            "atr14": round(atr_val, 4),
            "ema9": round(current_ema9, 4),
            "ema21": round(current_ema21, 4),
            "ema50": round(current_ema50, 4),
            "ema_stack_ok": ema_stack_ok,
            "avg_volume_20d": round(avg_volume_20d, 0),
            "vcp_detected": vcp_result["is_vcp"],
            "vcp_waves": vcp_result["contractions"],
            "vcp_tightness_pct": round(vcp_result["tightness"] * 100, 2),
            "weekly_trend_state": weekly_context.trend_state if weekly_context else None,
            "weekly_close_strength": round(weekly_context.close_strength, 3) if weekly_context else None,
        }

        notes = [
            f"Breakout {close_above_pct:.1f}% above resistance, RVOL {rvol:.1f}x",
            f"VCP: {len(vcp_result['contractions'])} waves" if vcp_result["is_vcp"] else "VCP: not detected",
            f"EMA stack: {'yes' if ema_stack_ok else 'no'}",
        ]
        if weekly_context is not None:
            notes.append(f"Weekly context: {weekly_context.trend_state}, close_strength={weekly_context.close_strength:.2f}")

        return SetupSignal(
            symbol=symbol,
            timestamp=df["timestamp"].iloc[-1],
            setup_type=SetupType.BREAKOUT,
            timeframe=timeframe,
            direction="long",
            score=score,
            trigger_level=round(resistance, 4),
            invalidation_level=round(stop, 4),
            support_level=round(current_ema21, 4),
            resistance_level=round(resistance, 4),
            context=context,
            notes=notes,
        )

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if "timestamp" not in out.columns:
            out = out.reset_index().rename(columns={out.index.name or "index": "timestamp"})
        return out.sort_values("timestamp").reset_index(drop=True)

    @staticmethod
    def _detect_vcp(df: pd.DataFrame, cfg: BreakoutConfig) -> dict:
        lookback = min(cfg.vcp_lookback, len(df) - 5)
        sub = df.iloc[-lookback:].copy().reset_index(drop=True)
        highs = sub["high"]
        lows = sub["low"]
        n = len(sub)
        pivot_window = 4
        swing_points: list[tuple[str, int, float]] = []
        for i in range(pivot_window, n - pivot_window):
            h_window = highs.iloc[i - pivot_window:i + pivot_window + 1]
            l_window = lows.iloc[i - pivot_window:i + pivot_window + 1]
            if float(highs.iloc[i]) == float(h_window.max()):
                swing_points.append(("H", i, float(highs.iloc[i])))
            elif float(lows.iloc[i]) == float(l_window.min()):
                swing_points.append(("L", i, float(lows.iloc[i])))

        clean: list[tuple[str, int, float]] = []
        for point in swing_points:
            if not clean or clean[-1][0] != point[0]:
                clean.append(point)
            else:
                if point[0] == "H" and point[2] > clean[-1][2]:
                    clean[-1] = point
                elif point[0] == "L" and point[2] < clean[-1][2]:
                    clean[-1] = point

        contractions: list[float] = []
        for idx in range(len(clean) - 1):
            if clean[idx][0] == "H" and clean[idx + 1][0] == "L":
                pct = (clean[idx][2] - clean[idx + 1][2]) / clean[idx][2]
                contractions.append(round(pct, 4))
        if len(contractions) < cfg.vcp_min_waves:
            return {"is_vcp": False, "contractions": contractions, "tightness": 0.0}

        is_contracting = all(contractions[i] < contractions[i - 1] for i in range(1, len(contractions)))
        final_wave = contractions[-1]
        is_tight = final_wave < cfg.vcp_max_final_pullback
        return {
            "is_vcp": is_contracting and is_tight,
            "contractions": [round(item * 100, 2) for item in contractions],
            "tightness": final_wave,
        }

    @staticmethod
    def _score(rvol: float, vcp_result: dict, close_above_pct: float, ema_stack_ok: bool, cfg: BreakoutConfig) -> float:
        rvol_score = min(rvol / 3.0, 1.0)
        vcp_score = 1.0 if vcp_result["is_vcp"] else 0.3
        tightness_score = max(0.0, 1.0 - vcp_result["tightness"] / cfg.vcp_max_final_pullback)
        breakout_score = min(max(close_above_pct / 2.0, 0.0), 1.0)
        ema_bonus = 0.05 if ema_stack_ok else 0.0
        raw = rvol_score * 0.30 + vcp_score * 0.25 + tightness_score * 0.25 + breakout_score * 0.20 + ema_bonus
        return round(min(raw, 1.0), 3)

    def scan_universe(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        timeframe: str = "1d",
        weekly_context_map: Optional[dict[str, WeeklyContext]] = None,
    ) -> list[SetupSignal]:
        results: list[SetupSignal] = []
        weekly_map = weekly_context_map or {}
        for symbol in symbols:
            df = ohlcv_map.get(symbol.upper())
            if df is None or df.empty:
                continue
            sig = self.detect(symbol=symbol, df=df, timeframe=timeframe, weekly_context=weekly_map.get(symbol.upper()))
            if sig is not None:
                results.append(sig)
        return sorted(results, key=lambda item: item.score, reverse=True)
