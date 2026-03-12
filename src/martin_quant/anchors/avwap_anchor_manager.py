"""avwap_anchor_manager.py

Anchored VWAP (AVWAP) support/resistance manager.

Martin Luk uses AVWAP as a key dynamic support level:
  - Anchor on earnings date  → EPS AVWAP
  - Anchor on breakout date  → BO AVWAP
  - Anchor on 52-week low    → Base AVWAP
  - Anchor on recent swing-low → Pullback AVWAP

Usage:
    mgr = AVWAPAnchorManager()
    result = mgr.compute(symbol, ohlcv_df, anchor_dates)
    print(result.nearest_support)  # closest AVWAP below current price
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AVWAPLine:
    anchor_type: str          # "eps" | "breakout" | "base" | "swing_low" | "custom"
    anchor_date: str          # YYYY-MM-DD
    current_value: float      # AVWAP price as of latest bar
    distance_pct: float       # (current_price - avwap) / avwap * 100  (+above / -below)
    is_support: bool          # price > avwap (acting as support)
    is_resistance: bool       # price < avwap (acting as resistance)
    bars_since_anchor: int    # number of trading bars since anchor
    slope_pct: float          # 20-bar slope of AVWAP (% per bar)


@dataclass
class AVWAPResult:
    symbol: str
    current_price: float
    avwap_lines: list[AVWAPLine] = field(default_factory=list)
    nearest_support: Optional[AVWAPLine] = None    # closest AVWAP below price
    nearest_resistance: Optional[AVWAPLine] = None # closest AVWAP above price
    touching_support: bool = False   # within 0.5% of any support AVWAP
    pullback_signal: bool = False    # price pulled back to AVWAP from above
    breakout_signal: bool = False    # price broke above AVWAP after being below
    score_boost: float = 0.0         # extra score for daily_scan integration

    def summary(self) -> str:
        lines = [f"{self.symbol} @ {self.current_price:.2f}"]
        for av in self.avwap_lines:
            tag = "SUP" if av.is_support else "RES"
            lines.append(
                f"  [{tag}] {av.anchor_type:12s} AVWAP={av.current_value:.2f} "
                f"dist={av.distance_pct:+.1f}% slope={av.slope_pct:+.3f}%/bar"
            )
        if self.touching_support:
            lines.append("  ✅ TOUCHING SUPPORT AVWAP — pullback entry zone")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core calculator
# ---------------------------------------------------------------------------

def _compute_avwap_series(df: pd.DataFrame, anchor_idx: int) -> pd.Series:
    """Compute AVWAP from anchor_idx to end of df."""
    sub = df.iloc[anchor_idx:].copy()
    typical_price = (sub["high"] + sub["low"] + sub["close"]) / 3
    pv = typical_price * sub["volume"]
    cumvol = sub["volume"].cumsum()
    cumpv  = pv.cumsum()
    avwap  = cumpv / cumvol.replace(0, np.nan)
    return avwap


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class AVWAPAnchorManager:
    """
    Manages multiple AVWAP anchors for a single symbol.

    Auto-detects anchor dates if not provided:
      - 52-week low  → base AVWAP
      - Recent swing low (20-bar) → pullback AVWAP

    Parameters
    ----------
    touch_threshold_pct : float
        Distance % to classify as "touching" support (default 0.5)
    pullback_lookback : int
        Bars to look back for swing-low anchor auto-detection
    """

    def __init__(
        self,
        touch_threshold_pct: float = 0.5,
        pullback_lookback: int = 20,
    ) -> None:
        self.touch_pct   = touch_threshold_pct
        self.pb_lookback = pullback_lookback

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(
        self,
        symbol: str,
        df: pd.DataFrame,
        anchor_dates: Optional[dict[str, str]] = None,
    ) -> AVWAPResult:
        """
        Compute all AVWAP lines for the symbol.

        Parameters
        ----------
        df : DataFrame with columns [open, high, low, close, volume] indexed by date.
        anchor_dates : dict  e.g.
            {"eps": "2024-08-28", "breakout": "2024-07-15", "custom": "2024-11-01"}
        """
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        if df.empty or len(df) < 5:
            return AVWAPResult(symbol=symbol, current_price=0.0)

        current_price = float(df["close"].iloc[-1])
        anchor_map: dict[str, str] = anchor_dates or {}

        # Auto-detect 52-week low anchor
        if "base" not in anchor_map and len(df) >= 252:
            lk = df.iloc[-252:]
            min_idx = lk["low"].idxmin()
            anchor_map["base"] = str(min_idx)[:10]

        # Auto-detect swing-low anchor
        if "swing_low" not in anchor_map and len(df) >= self.pb_lookback:
            recent = df.iloc[-self.pb_lookback:]
            min_idx = recent["low"].idxmin()
            anchor_map["swing_low"] = str(min_idx)[:10]

        # Build AVWAP lines
        avwap_lines: list[AVWAPLine] = []
        df_dates = [str(d)[:10] for d in df.index]

        for anchor_type, anchor_date in anchor_map.items():
            # Find anchor index
            anchor_idx = None
            for i, d in enumerate(df_dates):
                if d >= anchor_date:
                    anchor_idx = i
                    break
            if anchor_idx is None:
                log.debug("%s: anchor_date %s not in df", symbol, anchor_date)
                continue
            if len(df) - anchor_idx < 3:
                continue  # too few bars since anchor

            avwap_series = _compute_avwap_series(df, anchor_idx)
            if avwap_series.empty or avwap_series.isna().all():
                continue

            av_val = float(avwap_series.iloc[-1])
            if np.isnan(av_val) or av_val <= 0:
                continue

            dist_pct = (current_price - av_val) / av_val * 100
            bars_since = len(df) - anchor_idx

            # Slope over last 20 bars of the AVWAP series
            if len(avwap_series) >= 20:
                slope_pct = float(
                    (avwap_series.iloc[-1] - avwap_series.iloc[-20])
                    / avwap_series.iloc[-20] * 100 / 20
                )
            else:
                slope_pct = 0.0

            avwap_lines.append(AVWAPLine(
                anchor_type=anchor_type,
                anchor_date=anchor_date,
                current_value=round(av_val, 4),
                distance_pct=round(dist_pct, 3),
                is_support=dist_pct >= 0,
                is_resistance=dist_pct < 0,
                bars_since_anchor=bars_since,
                slope_pct=round(slope_pct, 4),
            ))

        # Sort by proximity (absolute distance)
        avwap_lines.sort(key=lambda a: abs(a.distance_pct))

        # Nearest support / resistance
        supports    = [a for a in avwap_lines if a.is_support]
        resistances = [a for a in avwap_lines if a.is_resistance]
        nearest_sup = supports[0]   if supports    else None
        nearest_res = resistances[0] if resistances else None

        # Signals
        touching = (
            nearest_sup is not None
            and abs(nearest_sup.distance_pct) <= self.touch_pct
        )

        # Pullback signal: price came from above, now touching AVWAP
        pullback_signal = touching and nearest_sup is not None

        # Breakout signal: price was below nearest resistance AVWAP, now above
        breakout_signal = (
            nearest_res is not None
            and abs(nearest_res.distance_pct) <= 1.0
            and current_price > nearest_res.current_value
        )

        # Score boost for daily_scan
        score_boost = 0.0
        if pullback_signal:
            score_boost += 0.08
        if breakout_signal:
            score_boost += 0.06
        if nearest_sup and 0 < nearest_sup.distance_pct <= 2.0 and nearest_sup.slope_pct > 0:
            score_boost += 0.04  # price just above rising AVWAP

        return AVWAPResult(
            symbol=symbol,
            current_price=round(current_price, 4),
            avwap_lines=avwap_lines,
            nearest_support=nearest_sup,
            nearest_resistance=nearest_res,
            touching_support=touching,
            pullback_signal=pullback_signal,
            breakout_signal=breakout_signal,
            score_boost=round(score_boost, 3),
        )

    def batch_compute(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        anchor_dates_map: Optional[dict[str, dict[str, str]]] = None,
    ) -> dict[str, AVWAPResult]:
        """Compute AVWAP for multiple symbols. Returns {symbol: AVWAPResult}."""
        results: dict[str, AVWAPResult] = {}
        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None:
                continue
            anchors = (anchor_dates_map or {}).get(sym, {})
            try:
                results[sym] = self.compute(sym, df, anchors)
            except Exception as e:
                log.warning("AVWAP failed for %s: %s", sym, e)
        return results
