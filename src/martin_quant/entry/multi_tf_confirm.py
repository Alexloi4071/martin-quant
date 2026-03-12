"""
Multi-Timeframe Entry Confirmation — Martin Luk 4hr Video (36:00 - 42:00)

Martin's 3-layer system:
  Layer 1 — DAILY  : Is this a valid setup? (trend + pattern)
  Layer 2 — HOURLY : Is there a trigger forming? (1H EMA alignment, consolidation)
  Layer 3 — 5-MIN  : Exact entry candle (ORB break, momentum candle, AVWAP reclaim)

Martin's rule:
  "Don't enter on the daily bar open. Wait for the hourly chart
   to show you momentum before committing. The 5-minute ORB
   is your sniper entry."

This module validates all three layers and returns a confirmed entry signal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from enum import Enum

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class TFLayer(Enum):
    DAILY = "daily"
    HOURLY = "hourly"
    FIVE_MIN = "5min"


@dataclass
class MultiTFResult:
    symbol: str
    daily_ok: bool
    hourly_ok: bool
    fivemin_ok: bool
    confirmed: bool           # All 3 layers pass
    entry_price: float
    stop_price: float
    trigger_type: str         # 'orb_break' | 'momentum_candle' | 'avwap_reclaim' | 'hourly_ema'
    confidence: float         # 0.0 - 1.0
    notes: str = ""

    def __str__(self) -> str:
        layers = [
            f"D={'✅' if self.daily_ok else '❌'}",
            f"1H={'✅' if self.hourly_ok else '❌'}",
            f"5m={'✅' if self.fivemin_ok else '❌'}",
        ]
        status = '✅ CONFIRMED' if self.confirmed else '⏳ NOT READY'
        return f"{self.symbol} {status} | {' '.join(layers)} | trigger={self.trigger_type} | conf={self.confidence:.0%}"


@dataclass
class MultiTFConfig:
    # Daily layer
    daily_require_above_ema9: bool = True
    daily_require_above_ema21: bool = True

    # Hourly layer
    hourly_ema_fast: int = 9
    hourly_ema_slow: int = 21
    hourly_require_ema_aligned: bool = True    # ema9 > ema21 on 1H
    hourly_consolidation_bars: int = 6          # Look for 6 bars of tight range
    hourly_consolidation_atr_ratio: float = 0.5 # Range < 50% of ATR = consolidating

    # 5-min layer (ORB)
    orb_minutes: int = 15                       # 15-min opening range
    orb_buffer_pct: float = 0.002               # 0.2% above ORB high
    momentum_candle_body_ratio: float = 0.65    # Body > 65% of candle range
    momentum_candle_vol_ratio: float = 1.5      # Vol > 1.5x of avg

    # Confidence weights
    weight_daily: float = 0.40
    weight_hourly: float = 0.35
    weight_fivemin: float = 0.25


class MultiTFConfirm:
    """
    Validates a trade setup across Daily, Hourly, and 5-minute timeframes.
    """

    def __init__(self, config: Optional[MultiTFConfig] = None):
        self.config = config or MultiTFConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def confirm(
        self,
        symbol: str,
        daily_df: pd.DataFrame,
        hourly_df: Optional[pd.DataFrame] = None,
        fivemin_df: Optional[pd.DataFrame] = None,
    ) -> MultiTFResult:
        """
        Full 3-layer confirmation.
        If hourly_df or fivemin_df is None, those layers auto-pass
        (useful when intraday data is not available at scan time).
        """
        # ---- Layer 1: Daily ----
        daily_ok, daily_score, daily_notes = self._check_daily(daily_df)

        # ---- Layer 2: Hourly ----
        if hourly_df is not None and len(hourly_df) >= 20:
            hourly_ok, hourly_score, hourly_notes = self._check_hourly(hourly_df)
        else:
            hourly_ok, hourly_score, hourly_notes = True, 0.7, "no 1H data (assumed ok)"

        # ---- Layer 3: 5-min ----
        if fivemin_df is not None and len(fivemin_df) >= 10:
            fivemin_ok, fivemin_score, trigger_type, entry, stop, fivemin_notes = (
                self._check_fivemin(fivemin_df, daily_df)
            )
        else:
            fivemin_ok = True
            fivemin_score = 0.7
            trigger_type = "pending"
            entry = daily_df["close"].iloc[-1] if len(daily_df) > 0 else 0
            stop = 0
            fivemin_notes = "no 5m data (pending intraday trigger)"

        confirmed = daily_ok and hourly_ok and fivemin_ok

        confidence = (
            daily_score * self.config.weight_daily
            + hourly_score * self.config.weight_hourly
            + fivemin_score * self.config.weight_fivemin
        )

        all_notes = " | ".join(filter(None, [daily_notes, hourly_notes, fivemin_notes]))

        result = MultiTFResult(
            symbol=symbol,
            daily_ok=daily_ok,
            hourly_ok=hourly_ok,
            fivemin_ok=fivemin_ok,
            confirmed=confirmed,
            entry_price=round(entry, 2),
            stop_price=round(stop, 2) if stop else 0,
            trigger_type=trigger_type,
            confidence=round(confidence, 3),
            notes=all_notes,
        )

        logger.debug(str(result))
        return result

    # ------------------------------------------------------------------
    # Layer 1: Daily
    # ------------------------------------------------------------------

    def _check_daily(self, df: pd.DataFrame) -> tuple[bool, float, str]:
        if len(df) < 21:
            return False, 0, "insufficient daily data"

        df = df.copy()
        df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
        df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
        last = df.iloc[-1]

        checks = []
        score = 0.0

        # Price above EMA9
        if last["close"] > last["ema9"]:
            score += 0.40
            checks.append("above_ema9")
        elif self.config.daily_require_above_ema9:
            return False, 0, "price below daily EMA9"

        # Price above EMA21
        if last["close"] > last["ema21"]:
            score += 0.40
            checks.append("above_ema21")
        elif self.config.daily_require_above_ema21:
            return False, 0, "price below daily EMA21"

        # EMA9 > EMA21 (alignment)
        if last["ema9"] > last["ema21"]:
            score += 0.20
            checks.append("ema_aligned")

        ok = score >= 0.60
        return ok, score, "+".join(checks) if checks else "daily_weak"

    # ------------------------------------------------------------------
    # Layer 2: Hourly
    # ------------------------------------------------------------------

    def _check_hourly(self, df: pd.DataFrame) -> tuple[bool, float, str]:
        df = df.copy()
        df["ema9"] = df["close"].ewm(span=self.config.hourly_ema_fast, adjust=False).mean()
        df["ema21"] = df["close"].ewm(span=self.config.hourly_ema_slow, adjust=False).mean()
        last = df.iloc[-1]

        score = 0.0
        checks = []

        # 1H EMA9 > EMA21
        if last["ema9"] > last["ema21"]:
            score += 0.40
            checks.append("1H_ema_aligned")
        elif self.config.hourly_require_ema_aligned:
            return False, 0, "1H EMA not aligned"

        # Price above 1H EMA9
        if last["close"] > last["ema9"]:
            score += 0.30
            checks.append("above_1H_ema9")

        # Consolidation check (tight range = coiling energy)
        if self._is_hourly_consolidating(df):
            score += 0.30
            checks.append("consolidating")

        ok = score >= 0.50
        return ok, score, "+".join(checks) if checks else "1H_weak"

    def _is_hourly_consolidating(self, df: pd.DataFrame) -> bool:
        """Last N hours have a tight range relative to ATR."""
        n = self.config.hourly_consolidation_bars
        if len(df) < n + 14:
            return False
        recent = df.tail(n)
        recent_range = (recent["high"].max() - recent["low"].min()) / recent["close"].mean()
        # ATR proxy
        atr = ((df["high"] - df["low"]) / df["close"]).rolling(14).mean().iloc[-1]
        return recent_range < atr * self.config.hourly_consolidation_atr_ratio

    # ------------------------------------------------------------------
    # Layer 3: 5-min (ORB + momentum)
    # ------------------------------------------------------------------

    def _check_fivemin(
        self,
        df: pd.DataFrame,
        daily_df: pd.DataFrame,
    ) -> tuple[bool, float, str, float, float, str]:
        """
        Returns: (ok, score, trigger_type, entry_price, stop_price, notes)
        """
        df = df.copy()
        df["vol_avg"] = df["volume"].rolling(20).mean()

        # ---- ORB (Opening Range Breakout) ----
        orb_bars = self.config.orb_minutes // 5
        if len(df) < orb_bars + 1:
            return False, 0, "pending", 0, 0, "insufficient 5m bars"

        orb_data = df.head(orb_bars)
        orb_high = orb_data["high"].max()
        orb_low = orb_data["low"].min()
        orb_buffer = orb_high * self.config.orb_buffer_pct

        latest = df.iloc[-1]
        entry_price = 0
        stop_price = 0
        trigger_type = "none"
        score = 0.0
        notes_parts = []

        # Check ORB break
        if latest["close"] > orb_high + orb_buffer:
            trigger_type = "orb_break"
            entry_price = orb_high + orb_buffer
            stop_price = orb_low * 0.999   # Just below ORB low
            score += 0.60
            notes_parts.append(f"ORB_break>{orb_high:.2f}")

            # Volume confirmation on break bar
            if latest["volume"] > latest["vol_avg"] * 1.5:
                score += 0.20
                notes_parts.append("vol_confirm")

        # Check momentum candle (big body, big volume) — alternative entry
        if trigger_type == "none":
            candle_body = abs(latest["close"] - latest["open"])
            candle_range = latest["high"] - latest["low"]
            if candle_range > 0:
                body_ratio = candle_body / candle_range
                vol_ratio = latest["volume"] / (latest["vol_avg"] or 1)

                if (
                    body_ratio >= self.config.momentum_candle_body_ratio
                    and vol_ratio >= self.config.momentum_candle_vol_ratio
                    and latest["close"] > latest["open"]   # Bullish
                ):
                    trigger_type = "momentum_candle"
                    entry_price = latest["high"] + 0.01
                    stop_price = latest["low"] * 0.999
                    score = 0.65
                    notes_parts.append(f"momentum_body={body_ratio:.0%}")

        # If no trigger found yet
        if trigger_type == "none" or entry_price == 0:
            # Fall back to daily close as entry (wait for tomorrow)
            entry_price = daily_df["close"].iloc[-1] if len(daily_df) > 0 else 0
            stop_price = 0
            return False, 0.3, "pending", entry_price, stop_price, "no 5m trigger yet"

        score = min(score, 1.0)
        ok = score >= 0.50
        return ok, score, trigger_type, entry_price, stop_price, " ".join(notes_parts)
