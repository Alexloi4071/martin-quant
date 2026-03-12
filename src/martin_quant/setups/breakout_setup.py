"""breakout_setup.py  (Batch 15 — VCP + EMA21 fix)

Key fixes vs original:
  1. EMA 20 → EMA 21  (Martin Luk canonical)
  2. Real VCP (Volatility Contraction Pattern) detection — 3-wave contraction
     inspired by Minervini, used extensively by Martin Luk
  3. VCP tightness score added to signal scoring
  4. context now reports each contraction wave depth
"""
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
    # VCP settings
    vcp_lookback: int = 60           # bars to look for contraction waves
    vcp_min_waves: int = 2           # minimum contraction waves required
    vcp_max_final_pullback: float = 0.10  # final wave must be < 10%
    require_vcp: bool = True         # require VCP pattern for breakout signal


class BreakoutSetupDetector:
    """
    Detects VCP breakout setups on a daily OHLCV DataFrame.

    A valid VCP breakout requires:
    - Volatility Contraction Pattern: each pullback wave smaller than last
    - Final contraction < 10% (tight base)
    - Volume on breakout bar >= min_rvol_on_breakout x 20d avg
    - Price breaks above the base resistance
    - EMA stack alignment (EMA9 > EMA21 > EMA50)
    """

    def __init__(self, config: BreakoutConfig | None = None) -> None:
        self.cfg = config or BreakoutConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        symbol: str,
        df: pd.DataFrame,
        timeframe: str = "1d",
    ) -> SetupSignal | None:
        cfg = self.cfg

        min_required = cfg.lookback_high_days + cfg.max_base_days + 20
        if len(df) < min_required:
            return None

        df = df.copy().sort_values("timestamp").reset_index(drop=True)
        close  = df["close"]
        high   = df["high"]
        volume = df["volume"]

        # ---- Resistance (N-day high excluding today) ------------------
        resistance = float(high.iloc[-(cfg.lookback_high_days + 1):-1].max())
        current_close  = float(close.iloc[-1])
        current_volume = float(volume.iloc[-1])

        close_above_pct = (current_close - resistance) / resistance * 100.0
        if close_above_pct < cfg.min_close_above_breakout_pct:
            return None

        # ---- RVOL check -----------------------------------------------
        avg_volume_20d = float(volume.iloc[-21:-1].mean())
        rvol = current_volume / avg_volume_20d if avg_volume_20d > 0 else 0.0
        if rvol < cfg.min_rvol_on_breakout:
            return None

        # ---- VCP detection -------------------------------------------
        vcp_result = self._detect_vcp(df, cfg)
        if cfg.require_vcp and not vcp_result["is_vcp"]:
            return None

        # ---- EMA (Martin canonical: 9 / 21 / 50) ---------------------
        ema9  = compute_ema(close, 9)
        ema21 = compute_ema(close, 21)   # FIX: was EMA 20
        ema50 = compute_ema(close, 50)
        current_ema9  = float(ema9.iloc[-1])
        current_ema21 = float(ema21.iloc[-1])
        current_ema50 = float(ema50.iloc[-1])

        # EMA stack (soft check — log but don't discard)
        ema_stack_ok = current_ema9 > current_ema21 > current_ema50

        # ---- ATR / risk -----------------------------------------------
        atr14   = compute_atr(df, period=14)
        atr_val = float(atr14.iloc[-1]) if not atr14.isna().all() else 0.0

        stop   = resistance - atr_val
        risk   = current_close - stop
        target = current_close + risk * 3.0

        # ---- Score ----------------------------------------------------
        score = self._score(rvol, vcp_result, close_above_pct, ema_stack_ok, cfg)

        context: dict[str, Any] = {
            "resistance":               round(resistance, 4),
            "close_above_resistance_pct": round(close_above_pct, 2),
            "rvol":                     round(rvol, 2),
            "atr14":                    round(atr_val, 4),
            "ema9":                     round(current_ema9, 4),
            "ema21":                    round(current_ema21, 4),   # FIX: was ema20
            "ema50":                    round(current_ema50, 4),
            "ema_stack_ok":             ema_stack_ok,
            "avg_volume_20d":           round(avg_volume_20d, 0),
            "vcp_detected":             vcp_result["is_vcp"],
            "vcp_waves":                vcp_result["contractions"],
            "vcp_tightness_pct":        round(vcp_result["tightness"] * 100, 2),
        }

        notes = [
            f"Breakout {close_above_pct:.1f}% above resistance, RVOL {rvol:.1f}x",
            f"VCP: {'✓ ' + str(len(vcp_result['contractions'])) + ' waves' if vcp_result['is_vcp'] else '✗ not detected'}",
            f"EMA stack: {'✓' if ema_stack_ok else '✗'} (9>{current_ema21:.2f}>50)",
        ]

        return SetupSignal(
            symbol=symbol,
            timestamp=df["timestamp"].iloc[-1],
            setup_type=SetupType.BREAKOUT,
            timeframe=timeframe,
            direction="long",
            score=score,
            trigger_level=round(resistance, 4),
            invalidation_level=round(stop, 4),
            support_level=round(current_ema21, 4),  # FIX: was ema20
            resistance_level=round(resistance, 4),
            context=context,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # VCP Detection (Minervini / Martin Luk)
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_vcp(df: pd.DataFrame, cfg: BreakoutConfig) -> dict:
        """
        Detect Volatility Contraction Pattern (VCP).

        Rules:
        - Find alternating swing highs and lows in the lookback window
        - Each pullback wave must be SMALLER than the previous
        - Volume must also trend lower across waves (soft check)
        - Final wave depth must be < vcp_max_final_pullback

        Returns dict with: is_vcp, contractions (list of pct), tightness (final wave)
        """
        lookback = min(cfg.vcp_lookback, len(df) - 5)
        sub      = df.iloc[-lookback:].copy().reset_index(drop=True)
        highs    = sub["high"]
        lows     = sub["low"]
        n        = len(sub)

        # Find local swing highs and lows (5-bar pivot)
        pivot_window = 4
        swing_points: list[tuple[str, int, float]] = []

        for i in range(pivot_window, n - pivot_window):
            h_window = highs.iloc[i - pivot_window: i + pivot_window + 1]
            l_window = lows.iloc[i - pivot_window: i + pivot_window + 1]
            if float(highs.iloc[i]) == float(h_window.max()):
                swing_points.append(("H", i, float(highs.iloc[i])))
            elif float(lows.iloc[i]) == float(l_window.min()):
                swing_points.append(("L", i, float(lows.iloc[i])))

        # De-duplicate: keep only alternating H/L
        clean: list[tuple[str, int, float]] = []
        for pt in swing_points:
            if not clean or clean[-1][0] != pt[0]:
                clean.append(pt)
            else:
                # Keep the more extreme one
                if pt[0] == "H" and pt[2] > clean[-1][2]:
                    clean[-1] = pt
                elif pt[0] == "L" and pt[2] < clean[-1][2]:
                    clean[-1] = pt

        # Calculate pullback depths between each H→L pair
        contractions: list[float] = []
        for j in range(len(clean) - 1):
            if clean[j][0] == "H" and clean[j + 1][0] == "L":
                pct = (clean[j][2] - clean[j + 1][2]) / clean[j][2]
                contractions.append(round(pct, 4))

        if len(contractions) < cfg.vcp_min_waves:
            return {"is_vcp": False, "contractions": contractions, "tightness": 0.0}

        # Check each wave is SMALLER than the previous (contraction)
        is_contracting = all(
            contractions[i] < contractions[i - 1]
            for i in range(1, len(contractions))
        )

        final_wave = contractions[-1]
        is_tight   = final_wave < cfg.vcp_max_final_pullback

        is_vcp = is_contracting and is_tight

        return {
            "is_vcp":        is_vcp,
            "contractions":  [round(c * 100, 2) for c in contractions],  # % format
            "tightness":     final_wave,
        }

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score(
        rvol: float,
        vcp_result: dict,
        close_above_pct: float,
        ema_stack_ok: bool,
        cfg: BreakoutConfig,
    ) -> float:
        rvol_score     = min(rvol / 3.0, 1.0)
        vcp_score      = 1.0 if vcp_result["is_vcp"] else 0.3
        # Tighter final wave = higher score (lower tightness pct = better)
        tightness_score = max(0.0, 1.0 - vcp_result["tightness"] / cfg.vcp_max_final_pullback)
        breakout_score  = min(max(close_above_pct / 2.0, 0.0), 1.0)
        ema_bonus       = 0.05 if ema_stack_ok else 0.0
        raw = (
            rvol_score     * 0.30
            + vcp_score    * 0.25
            + tightness_score * 0.25
            + breakout_score  * 0.20
            + ema_bonus
        )
        return round(min(raw, 1.0), 3)

    # ------------------------------------------------------------------
    # Universe scan
    # ------------------------------------------------------------------

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
