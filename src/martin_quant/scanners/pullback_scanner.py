"""
Pullback Scanner — Martin Luk 4hr Video (54:34 - 1:14:53)

Core logic:
  1. Stock must be in a confirmed uptrend (price > EMA21 > EMA50)
  2. EMA9 is rising (upward slope)
  3. Price has pulled back and is NOW near EMA9 or EMA21 (within tolerance)
  4. Volume dried up on pullback (< 40% of 20-day avg volume) → "quiet" pullback
  5. ADR% > 4% (enough range to trade)
  6. Optional: Pullback to AVWAP (if anchor exists)

Martin's exact words:
  "I want to see the first or second pullback to the 9 EMA after a breakout.
   The stock should barely touch it — not crash through it."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PullbackSignal:
    symbol: str
    date: str
    setup_type: str          # 'ema9_pullback' | 'ema21_pullback' | 'avwap_pullback'
    entry_price: float
    stop_price: float        # Low of pullback candle
    stop_pct: float          # Distance to stop as %
    r_target: float          # 2R or 3R target
    ema9: float
    ema21: float
    ema50: float
    avwap: Optional[float]
    adr_pct: float
    volume_ratio: float      # Current vol / 20d avg vol
    pullback_depth: float    # How far price pulled back from recent high (%)
    score: float             # 0.0 - 1.0
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "setup_type": self.setup_type,
            "entry": round(self.entry_price, 2),
            "stop": round(self.stop_price, 2),
            "stop_pct": round(self.stop_pct * 100, 2),
            "r_target": round(self.r_target, 2),
            "adr_pct": round(self.adr_pct * 100, 2),
            "volume_ratio": round(self.volume_ratio, 2),
            "pullback_depth": round(self.pullback_depth * 100, 2),
            "score": round(self.score, 3),
            "notes": self.notes,
        }


@dataclass
class PullbackConfig:
    # EMA proximity tolerance — how close price must be to EMA to qualify
    ema9_tolerance: float = 0.012    # 1.2% above/below EMA9
    ema21_tolerance: float = 0.018   # 1.8% above/below EMA21
    avwap_tolerance: float = 0.015   # 1.5% above/below AVWAP

    # Volume filter: pullback must show drying volume
    max_volume_ratio: float = 0.65   # Vol must be < 65% of 20d avg

    # Trend filters
    min_adr_pct: float = 0.04        # Minimum ADR 4%
    ema_slope_bars: int = 5          # Look back 5 bars to judge EMA slope
    min_ema_slope: float = 0.001     # EMA9 must be rising > 0.1% per bar

    # Pullback depth: not too shallow, not too deep
    min_pullback_depth: float = 0.03   # At least 3% pullback from high
    max_pullback_depth: float = 0.15   # No more than 15% pullback

    # Stop placement
    stop_buffer: float = 0.005       # 0.5% below pullback low

    # How many bars to look for recent high
    lookback_high_bars: int = 20


class PullbackScanner:
    """
    Scans a universe of stocks for high-quality pullback setups
    aligned with Martin Luk's methodology.
    """

    def __init__(self, config: Optional[PullbackConfig] = None):
        self.config = config or PullbackConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        avwap: Optional[pd.Series] = None,
    ) -> Optional[PullbackSignal]:
        """
        Analyse a single symbol's daily OHLCV DataFrame.
        Returns a PullbackSignal if a valid setup is found, else None.

        df must have columns: open, high, low, close, volume
        Index: DatetimeIndex (daily)
        """
        if len(df) < 60:
            return None

        df = df.copy()
        df = self._add_indicators(df, avwap)

        # ---- Trend filters (must ALL pass) ----
        if not self._passes_trend_filter(df):
            return None

        # ---- Pullback proximity check ----
        setup_type, proximity_score = self._check_pullback_proximity(df)
        if setup_type is None:
            return None

        # ---- Volume filter ----
        vol_ratio = df["volume"].iloc[-1] / df["vol_20d_avg"].iloc[-1]
        if vol_ratio > self.config.max_volume_ratio:
            # Volume not drying up — not a clean pullback
            return None

        # ---- ADR filter ----
        adr = df["adr"].iloc[-1]
        if adr < self.config.min_adr_pct:
            return None

        # ---- Pullback depth check ----
        recent_high = df["high"].rolling(self.config.lookback_high_bars).max().iloc[-1]
        current_close = df["close"].iloc[-1]
        pullback_depth = (recent_high - current_close) / recent_high
        if not (self.config.min_pullback_depth <= pullback_depth <= self.config.max_pullback_depth):
            return None

        # ---- Stop and entry ----
        entry_price = current_close
        pullback_low = df["low"].rolling(3).min().iloc[-1]  # Lowest of last 3 bars
        stop_price = pullback_low * (1 - self.config.stop_buffer)
        stop_pct = (entry_price - stop_price) / entry_price

        if stop_pct <= 0 or stop_pct > 0.08:   # Stop too wide (> 8%) → skip
            return None

        # ---- Score (0.0 - 1.0) ----
        score = self._calculate_score(
            proximity_score=proximity_score,
            vol_ratio=vol_ratio,
            pullback_depth=pullback_depth,
            adr=adr,
            stop_pct=stop_pct,
            df=df,
        )

        avwap_val = df["avwap"].iloc[-1] if "avwap" in df.columns else None

        return PullbackSignal(
            symbol=symbol,
            date=str(df.index[-1].date()),
            setup_type=setup_type,
            entry_price=round(entry_price, 2),
            stop_price=round(stop_price, 2),
            stop_pct=round(stop_pct, 4),
            r_target=round(entry_price + (entry_price - stop_price) * 2.5, 2),
            ema9=round(df["ema9"].iloc[-1], 2),
            ema21=round(df["ema21"].iloc[-1], 2),
            ema50=round(df["ema50"].iloc[-1], 2),
            avwap=round(avwap_val, 2) if avwap_val else None,
            adr_pct=round(adr, 4),
            volume_ratio=round(vol_ratio, 3),
            pullback_depth=round(pullback_depth, 4),
            score=round(score, 3),
            notes=self._build_notes(setup_type, vol_ratio, pullback_depth, df),
        )

    def scan_universe(
        self,
        universe: dict[str, pd.DataFrame],
        avwap_map: Optional[dict[str, pd.Series]] = None,
    ) -> list[PullbackSignal]:
        """
        Scan multiple symbols. Returns list sorted by score desc.
        universe = {symbol: df, ...}
        avwap_map = {symbol: avwap_series, ...}  (optional)
        """
        signals = []
        avwap_map = avwap_map or {}

        for symbol, df in universe.items():
            try:
                sig = self.scan_symbol(symbol, df, avwap_map.get(symbol))
                if sig:
                    signals.append(sig)
            except Exception as e:
                logger.warning(f"PullbackScanner error on {symbol}: {e}")

        signals.sort(key=lambda s: s.score, reverse=True)
        logger.info(f"PullbackScanner: {len(signals)} signals from {len(universe)} symbols")
        return signals

    # ------------------------------------------------------------------
    # Indicator helpers
    # ------------------------------------------------------------------

    def _add_indicators(self, df: pd.DataFrame, avwap: Optional[pd.Series]) -> pd.DataFrame:
        df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
        df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["vol_20d_avg"] = df["volume"].rolling(20).mean()

        # ADR: average of (high-low)/close over 14 days
        df["adr"] = ((df["high"] - df["low"]) / df["close"]).rolling(14).mean()

        # Relative strength vs SPY placeholder (RS score 0-100)
        # Full RS calculation done in LeaderScanner; here we use simple momentum
        df["rs_3m"] = df["close"].pct_change(63)   # 63 trading days ≈ 3 months

        if avwap is not None:
            df["avwap"] = avwap.reindex(df.index).ffill()

        return df

    # ------------------------------------------------------------------
    # Filter logic
    # ------------------------------------------------------------------

    def _passes_trend_filter(self, df: pd.DataFrame) -> bool:
        last = df.iloc[-1]

        # Price must be ABOVE EMA21 and EMA50
        if last["close"] < last["ema21"]:
            return False
        if last["close"] < last["ema50"]:
            return False

        # EMA9 must be sloping UP
        ema9_now = df["ema9"].iloc[-1]
        ema9_n_bars_ago = df["ema9"].iloc[-(self.config.ema_slope_bars + 1)]
        slope = (ema9_now - ema9_n_bars_ago) / (ema9_n_bars_ago * self.config.ema_slope_bars)
        if slope < self.config.min_ema_slope:
            return False

        # EMA alignment: EMA9 > EMA21 > EMA50
        if not (last["ema9"] > last["ema21"] > last["ema50"]):
            return False

        return True

    def _check_pullback_proximity(
        self, df: pd.DataFrame
    ) -> tuple[Optional[str], float]:
        """
        Returns (setup_type, proximity_score) or (None, 0).
        proximity_score: 1.0 = touching EMA perfectly, lower = further away.
        """
        last = df.iloc[-1]
        close = last["close"]
        ema9 = last["ema9"]
        ema21 = last["ema21"]

        # Check EMA9 pullback first (higher priority)
        dist_ema9 = abs(close - ema9) / ema9
        if dist_ema9 <= self.config.ema9_tolerance and close >= ema9 * 0.995:
            score = 1.0 - (dist_ema9 / self.config.ema9_tolerance)
            return "ema9_pullback", score

        # Check EMA21 pullback
        dist_ema21 = abs(close - ema21) / ema21
        if dist_ema21 <= self.config.ema21_tolerance and close >= ema21 * 0.998:
            score = 0.85 * (1.0 - (dist_ema21 / self.config.ema21_tolerance))
            return "ema21_pullback", score

        # Check AVWAP pullback (if available)
        if "avwap" in df.columns and not pd.isna(last["avwap"]):
            avwap = last["avwap"]
            dist_avwap = abs(close - avwap) / avwap
            if dist_avwap <= self.config.avwap_tolerance and close >= avwap * 0.998:
                score = 0.80 * (1.0 - (dist_avwap / self.config.avwap_tolerance))
                return "avwap_pullback", score

        return None, 0.0

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _calculate_score(
        self,
        proximity_score: float,
        vol_ratio: float,
        pullback_depth: float,
        adr: float,
        stop_pct: float,
        df: pd.DataFrame,
    ) -> float:
        score = 0.0

        # 1. Proximity to EMA (40% weight)
        score += proximity_score * 0.40

        # 2. Volume drying up (25% weight)
        # Perfect = vol at 20% of avg; worst allowed = 65% of avg
        vol_score = max(0, (self.config.max_volume_ratio - vol_ratio) / self.config.max_volume_ratio)
        score += vol_score * 0.25

        # 3. Pullback depth sweet spot: 5-8% is ideal (20% weight)
        ideal_depth = 0.065
        depth_score = 1.0 - abs(pullback_depth - ideal_depth) / 0.10
        score += max(0, depth_score) * 0.20

        # 4. ADR (10% weight) — higher ADR = more tradeable
        adr_score = min(adr / 0.10, 1.0)   # Normalize to 10% ADR cap
        score += adr_score * 0.10

        # 5. Tight stop (5% weight) — 1-2% stop is ideal
        stop_score = 1.0 - abs(stop_pct - 0.015) / 0.05
        score += max(0, stop_score) * 0.05

        return min(score, 1.0)

    def _build_notes(self, setup_type, vol_ratio, pullback_depth, df) -> str:
        parts = []
        if vol_ratio < 0.30:
            parts.append("ultra-dry volume")
        elif vol_ratio < 0.50:
            parts.append("dry volume")
        if pullback_depth < 0.05:
            parts.append("shallow pullback")
        elif pullback_depth > 0.10:
            parts.append("deep pullback")
        rs = df["rs_3m"].iloc[-1]
        if rs > 0.30:
            parts.append(f"RS+{rs:.0%}")
        return "; ".join(parts) if parts else setup_type
