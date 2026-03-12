"""pullback_setup.py  (Batch 15 — VUD + EMA21 + First Pullback fix)

Key fixes vs original:
  1. EMA 20 → EMA 21  (Martin Luk canonical)
  2. VUD filter: volume must be drying up during pullback (< 80% of 20d avg)
  3. First Pullback detection: confirm this is the 1st retest of swing high
  4. Score now includes vol_ratio as quality signal
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from martin_quant.core.datatypes import SetupSignal
from martin_quant.core.enums import SetupType
from martin_quant.features.atr import compute_atr
from martin_quant.features.ema import compute_ema
from martin_quant.features.volume_quality import calc_vol_ratio, is_volume_dry


@dataclass(slots=True)
class PullbackConfig:
    lookback_high_days: int = 20
    min_pullback_depth_pct: float = 5.0
    max_pullback_depth_pct: float = 30.0
    max_support_distance_pct: float = 3.0      # relaxed slightly for EMA21 spacing
    min_history_days: int = 60
    require_close_above_ema50: bool = True
    require_ema_stack: bool = True
    first_pullback_lookback: int = 30
    # VUD parameters
    vud_window: int = 5                        # look back 5 bars for vol dry-up
    vud_threshold: float = 0.80                # vol must be < 80% of 20d avg
    require_vud: bool = True                   # Martin core filter — on by default
    # First Pullback parameters
    require_first_pullback: bool = True        # "First is Best"
    fp_prior_lookback: int = 60               # bars before the rally to check


class PullbackSetupDetector:
    """
    Detects healthy pullback setups on a daily OHLCV DataFrame.

    A valid pullback requires (Martin Luk criteria):
    - Stock made a swing high in recent history (first_pullback_lookback bars)
    - Price has pulled back between min/max pullback depth from that high
    - Price is near EMA21 or EMA50 support within max_support_distance_pct
    - EMA stack is bullish: EMA9 > EMA21 > EMA50 (optional)
    - Price is above EMA50 (optional)
    - Volume is DRYING UP during the pullback (VUD filter) — Martin core rule
    - This is the FIRST pullback from the swing high (First is Best)
    """

    def __init__(self, config: PullbackConfig | None = None) -> None:
        self.cfg = config or PullbackConfig()

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

        if len(df) < cfg.min_history_days:
            return None

        df = df.copy().sort_values("timestamp").reset_index(drop=True)
        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"]

        # ---- EMA (Martin canonical: 9 / 21 / 50) ----------------------
        ema9  = compute_ema(close, 9)
        ema21 = compute_ema(close, 21)   # FIX: was EMA 20
        ema50 = compute_ema(close, 50)

        current_close = float(close.iloc[-1])
        current_ema9  = float(ema9.iloc[-1])
        current_ema21 = float(ema21.iloc[-1])
        current_ema50 = float(ema50.iloc[-1])

        # ---- Swing high in recent window ------------------------------
        recent = df.tail(cfg.first_pullback_lookback)
        swing_high_idx = int(recent["high"].idxmax())
        swing_high     = float(recent["high"].max())

        pullback_depth_pct = (swing_high - current_close) / swing_high * 100.0

        if pullback_depth_pct < cfg.min_pullback_depth_pct:
            return None
        if pullback_depth_pct > cfg.max_pullback_depth_pct:
            return None

        # ---- EMA filters ----------------------------------------------
        if cfg.require_close_above_ema50 and current_close < current_ema50:
            return None

        if cfg.require_ema_stack:
            if not (current_ema9 > current_ema21 > current_ema50):
                return None

        # ---- Support proximity (EMA21 or EMA50) -----------------------
        support_levels = [current_ema21, current_ema50]
        best_support   = min(support_levels, key=lambda s: abs(current_close - s))
        support_dist_pct = abs(current_close - best_support) / best_support * 100.0
        if support_dist_pct > cfg.max_support_distance_pct:
            return None

        # ---- VUD: Volume Dry-Up filter (Martin core rule) -------------
        vol_ratio = calc_vol_ratio(volume, avg_window=20)
        vud_ok    = is_volume_dry(
            volume,
            avg_window=20,
            lookback=cfg.vud_window,
            threshold=cfg.vud_threshold,
        )
        if cfg.require_vud and not vud_ok:
            return None   #放量回調 → 不是 Martin 買點

        recent_vol_ratio = float(vol_ratio.iloc[-cfg.vud_window:].mean())

        # ---- First Pullback detection ("First is Best") ---------------
        is_first_pb = self._check_first_pullback(
            df, swing_high_idx, swing_high, cfg
        )
        if cfg.require_first_pullback and not is_first_pb:
            return None

        # ---- Risk / reward --------------------------------------------
        atr14   = compute_atr(df, period=14)
        atr_val = float(atr14.iloc[-1]) if not atr14.isna().all() else 0.0

        stop   = best_support - atr_val * 0.5
        risk   = current_close - stop
        target = current_close + risk * 3.0

        score = self._score(
            pullback_depth_pct, support_dist_pct, recent_vol_ratio, cfg
        )

        context: dict[str, Any] = {
            "swing_high":          swing_high,
            "pullback_depth_pct":  round(pullback_depth_pct, 2),
            "support_level":       round(best_support, 4),
            "support_dist_pct":    round(support_dist_pct, 2),
            "ema9":                round(current_ema9, 4),
            "ema21":               round(current_ema21, 4),   # FIX: was ema20
            "ema50":               round(current_ema50, 4),
            "atr14":               round(atr_val, 4),
            "vol_ratio_5d_avg":    round(recent_vol_ratio, 3),
            "vud_confirmed":       vud_ok,
            "is_first_pullback":   is_first_pb,
        }

        notes = [
            f"Pullback {pullback_depth_pct:.1f}% from swing high near EMA{'21' if best_support == current_ema21 else '50'}",
            f"VUD: vol_ratio={recent_vol_ratio:.2f} ({'✓ drying' if vud_ok else '✗ expanding'})",
            f"First pullback: {'✓ Yes' if is_first_pb else '? No'}",
        ]

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
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_first_pullback(
        df: pd.DataFrame,
        swing_high_idx: int,
        swing_high: float,
        cfg: PullbackConfig,
    ) -> bool:
        """
        Confirm this is the FIRST pullback from the swing high.

        Logic:
        - Look at the bars BEFORE the swing high (up to fp_prior_lookback bars)
        - If price was ever >= swing_high * 0.98 before this rally, it's NOT first
        - This ensures we're catching the initial breakout retest, not a late pullback
        """
        prior_start = max(0, swing_high_idx - cfg.fp_prior_lookback)
        prior_bars  = df.iloc[prior_start:swing_high_idx]
        if prior_bars.empty:
            return True
        prior_high = float(prior_bars["high"].max())
        return prior_high < swing_high * 0.97  # prior period never reached this level

    @staticmethod
    def _score(
        pullback_pct: float,
        support_dist_pct: float,
        vol_ratio: float,
        cfg: PullbackConfig,
    ) -> float:
        # Ideal pullback: ~8-12% from high
        depth_score    = max(0.0, 1.0 - abs(pullback_pct - 10.0) / 10.0)
        # Closer to EMA support = better
        proximity_score = max(0.0, 1.0 - (support_dist_pct / cfg.max_support_distance_pct))
        # Lower volume = better (VUD quality)
        vud_score = max(0.0, 1.0 - vol_ratio)  # vol_ratio < 0.5 → score=0.5+
        return round(
            depth_score * 0.35 + proximity_score * 0.40 + vud_score * 0.25,
            3,
        )

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
