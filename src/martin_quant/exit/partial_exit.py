"""
Partial Exit Manager — Martin Luk 4hr Video (65:00 - 71:00)

Martin's exact staged exit rules:
  - At +2R: Consider taking 10% off if market is choppy
  - At +3R: Take 10-15% off, raise stop to breakeven+
  - At +5R: Take another 15-20% off, trail stop to 2R
  - At +8R+: Trail aggressively — let winner run
  - NEVER cut a winner early in a BULL market
  - "Sell into strength, not weakness"

Additional rules:
  - If stock closes BELOW EMA9 → exit 50% immediately
  - Two consecutive closes below EMA9 → full exit
  - Gap down > 3% pre-market → exit immediately at open
  - Earnings coming up and stock not up > 2R → exit before earnings
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

import pandas as pd

logger = logging.getLogger(__name__)


class ExitAction(Enum):
    HOLD = "hold"
    PARTIAL_10 = "partial_10"    # Sell 10%
    PARTIAL_15 = "partial_15"    # Sell 15%
    PARTIAL_20 = "partial_20"    # Sell 20%
    PARTIAL_50 = "partial_50"    # Sell 50% (EMA9 violation)
    FULL_EXIT = "full_exit"      # Sell 100%
    RAISE_STOP = "raise_stop"    # Adjust stop only


@dataclass
class ExitDecision:
    symbol: str
    action: ExitAction
    current_r: float
    new_stop: Optional[float]
    sell_pct: float           # 0.0 - 1.0
    reason: str
    urgency: str              # 'normal' | 'urgent' | 'immediate'

    def __str__(self) -> str:
        return (
            f"{self.symbol}: {self.action.value.upper()} "
            f"({self.sell_pct:.0%}) @ {self.current_r:.1f}R | {self.reason}"
        )


@dataclass
class PartialExitConfig:
    # R-multiple thresholds for staged selling
    r2_sell_pct: float = 0.10    # Sell 10% at 2R (only if choppy market)
    r3_sell_pct: float = 0.12    # Sell 12% at 3R (always)
    r5_sell_pct: float = 0.18    # Sell 18% at 5R
    r8_sell_pct: float = 0.25    # Sell 25% at 8R

    # Stop adjustments
    raise_stop_at_r3: bool = True          # Move stop to breakeven at 3R
    trail_stop_at_r5: bool = True          # Trail to 2R level at 5R
    trail_stop_at_r8: bool = True          # Tighter trail at 8R

    # EMA exit rules
    ema9_close_below_exit_pct: float = 0.50   # Sell 50% on 1 close below EMA9
    ema9_two_closes_full_exit: bool = True     # Full exit on 2 closes below EMA9

    # Pre-earnings rule
    exit_before_earnings_if_below_r: float = 2.0   # Exit before earnings if < 2R up

    # Gap down rule
    gap_down_exit_threshold: float = 0.03    # > 3% gap down → full exit

    # Regime override
    hold_winners_longer_in_bull: bool = True   # In BULL: skip 2R sell, hold to 3R+


class PartialExitManager:
    """
    Decides whether and how much to sell at each price level.
    Tracks exit history per position.
    """

    def __init__(self, config: Optional[PartialExitConfig] = None):
        self.config = config or PartialExitConfig()
        self._exit_history: dict[str, list[dict]] = {}   # symbol → list of exits

    # ------------------------------------------------------------------
    # Main decision logic
    # ------------------------------------------------------------------

    def evaluate(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        current_price: float,
        daily_df: Optional[pd.DataFrame] = None,
        earnings_in_days: Optional[int] = None,
        premarket_price: Optional[float] = None,
        regime: str = "BULL",
    ) -> ExitDecision:
        """
        Evaluate what to do with an open position right now.
        Returns an ExitDecision with action and reasoning.
        """
        risk_per_r = entry_price - stop_price
        if risk_per_r <= 0:
            return ExitDecision(
                symbol=symbol, action=ExitAction.HOLD,
                current_r=0, new_stop=None, sell_pct=0,
                reason="invalid stop", urgency="normal"
            )

        current_r = (current_price - entry_price) / risk_per_r

        # ---- PRIORITY 1: Gap down (immediate) ----
        if premarket_price is not None:
            gap_pct = (premarket_price - current_price) / current_price
            if gap_pct < -self.config.gap_down_exit_threshold:
                return ExitDecision(
                    symbol=symbol, action=ExitAction.FULL_EXIT,
                    current_r=current_r, new_stop=None, sell_pct=1.0,
                    reason=f"gap_down {gap_pct:.1%}", urgency="immediate"
                )

        # ---- PRIORITY 2: Below stop (full exit) ----
        if current_price <= stop_price:
            return ExitDecision(
                symbol=symbol, action=ExitAction.FULL_EXIT,
                current_r=current_r, new_stop=None, sell_pct=1.0,
                reason="hit_stop", urgency="immediate"
            )

        # ---- PRIORITY 3: EMA9 violation ----
        if daily_df is not None and len(daily_df) >= 10:
            ema9_exit = self._check_ema9_exit(symbol, daily_df, current_r)
            if ema9_exit:
                return ema9_exit

        # ---- PRIORITY 4: Pre-earnings rule ----
        if (
            earnings_in_days is not None
            and earnings_in_days <= 3
            and current_r < self.config.exit_before_earnings_if_below_r
        ):
            return ExitDecision(
                symbol=symbol, action=ExitAction.FULL_EXIT,
                current_r=current_r, new_stop=None, sell_pct=1.0,
                reason=f"earnings_in_{earnings_in_days}d_only_{current_r:.1f}R",
                urgency="urgent"
            )

        # ---- PRIORITY 5: R-multiple staged exits ----
        return self._r_based_exit(symbol, entry_price, stop_price, current_price, current_r, regime)

    # ------------------------------------------------------------------
    # R-based staged selling
    # ------------------------------------------------------------------

    def _r_based_exit(
        self, symbol, entry_price, stop_price, current_price, current_r, regime
    ) -> ExitDecision:
        cfg = self.config
        history = self._exit_history.get(symbol, [])
        already_sold_at = {h["trigger_r"] for h in history}
        risk = entry_price - stop_price

        # ---- At 8R+: aggressive trail ----
        if current_r >= 8.0 and 8.0 not in already_sold_at:
            new_stop = entry_price + risk * 5.0   # Trail to 5R
            self._record_exit(symbol, 8.0, cfg.r8_sell_pct)
            return ExitDecision(
                symbol=symbol, action=ExitAction.PARTIAL_20,
                current_r=current_r,
                new_stop=new_stop,
                sell_pct=cfg.r8_sell_pct,
                reason="8R_trail_stop", urgency="normal"
            )

        # ---- At 5R: take more off, trail stop ----
        if current_r >= 5.0 and 5.0 not in already_sold_at:
            new_stop = entry_price + risk * 2.0   # Trail to 2R
            self._record_exit(symbol, 5.0, cfg.r5_sell_pct)
            return ExitDecision(
                symbol=symbol, action=ExitAction.PARTIAL_20,
                current_r=current_r,
                new_stop=new_stop,
                sell_pct=cfg.r5_sell_pct,
                reason="5R_trail_to_2R", urgency="normal"
            )

        # ---- At 3R: take some off, move stop to breakeven ----
        if current_r >= 3.0 and 3.0 not in already_sold_at:
            new_stop = entry_price + risk * 0.5 if cfg.raise_stop_at_r3 else stop_price
            self._record_exit(symbol, 3.0, cfg.r3_sell_pct)
            return ExitDecision(
                symbol=symbol, action=ExitAction.PARTIAL_15,
                current_r=current_r,
                new_stop=new_stop,
                sell_pct=cfg.r3_sell_pct,
                reason="3R_move_stop_be+", urgency="normal"
            )

        # ---- At 2R: ONLY in choppy/bear, or skip in bull ----
        if current_r >= 2.0 and 2.0 not in already_sold_at:
            if regime in ("CHOPPY", "BEAR") or not cfg.hold_winners_longer_in_bull:
                self._record_exit(symbol, 2.0, cfg.r2_sell_pct)
                return ExitDecision(
                    symbol=symbol, action=ExitAction.PARTIAL_10,
                    current_r=current_r, new_stop=stop_price,
                    sell_pct=cfg.r2_sell_pct,
                    reason="2R_choppy_partial", urgency="normal"
                )

        # ---- Default: HOLD ----
        return ExitDecision(
            symbol=symbol, action=ExitAction.HOLD,
            current_r=current_r, new_stop=None, sell_pct=0.0,
            reason="hold_let_winner_run", urgency="normal"
        )

    # ------------------------------------------------------------------
    # EMA9 violation check
    # ------------------------------------------------------------------

    def _check_ema9_exit(self, symbol, df, current_r) -> Optional[ExitDecision]:
        df = df.copy()
        df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()

        last2 = df.tail(2)
        below_ema9 = last2["close"] < last2["ema9"]

        if self.config.ema9_two_closes_full_exit and below_ema9.all():
            return ExitDecision(
                symbol=symbol, action=ExitAction.FULL_EXIT,
                current_r=current_r, new_stop=None, sell_pct=1.0,
                reason="ema9_two_bar_violation", urgency="urgent"
            )

        if below_ema9.iloc[-1]:
            return ExitDecision(
                symbol=symbol, action=ExitAction.PARTIAL_50,
                current_r=current_r, new_stop=None,
                sell_pct=self.config.ema9_close_below_exit_pct,
                reason="ema9_close_below", urgency="urgent"
            )

        return None

    # ------------------------------------------------------------------
    # History tracking
    # ------------------------------------------------------------------

    def _record_exit(self, symbol: str, trigger_r: float, sell_pct: float) -> None:
        if symbol not in self._exit_history:
            self._exit_history[symbol] = []
        self._exit_history[symbol].append(
            {"trigger_r": trigger_r, "sell_pct": sell_pct}
        )

    def get_total_sold_pct(self, symbol: str) -> float:
        """How much of original position has been sold."""
        history = self._exit_history.get(symbol, [])
        sold = 0.0
        for h in history:
            sold = sold + h["sell_pct"] * (1 - sold)   # Sequential reduction
        return round(sold, 4)

    def reset_symbol(self, symbol: str) -> None:
        """Call when position is fully closed."""
        self._exit_history.pop(symbol, None)
