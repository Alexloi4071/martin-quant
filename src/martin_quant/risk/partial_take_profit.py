from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass(**({"slots": True} if sys.version_info >= (3, 10) else {}))
class PartialTakeProfitConfig:
    # Each item: (r_multiple, pct_of_position_to_sell)
    # e.g. [(3.0, 40.0), (5.0, 30.0)] means:
    #   at +3R sell 40% of position, at +5R sell 30% of remaining
    partial_levels: list[tuple[float, float]] = field(
        default_factory=lambda: [(3.0, 40.0), (5.0, 30.0)]
    )
    trail_mode: str = "ema9"  # ema9 | fixed_pct | none


@dataclass
class PartialFillEvent:
    r_multiple_hit: float
    shares_sold: int
    sell_price: float
    pnl: float
    remaining_shares: int
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "r_multiple_hit": self.r_multiple_hit,
            "shares_sold": self.shares_sold,
            "sell_price": round(self.sell_price, 4),
            "pnl": round(self.pnl, 2),
            "remaining_shares": self.remaining_shares,
            "notes": self.notes,
        }


class PartialTakeProfitManager:
    """
    Manages partial exits at pre-defined R multiples.

    Usage:
        manager = PartialTakeProfitManager(config)
        manager.open_trade(symbol, entry_price, stop_price, shares)

        # On each bar:
        events = manager.check_bar(current_high)
    """

    def __init__(self, config: PartialTakeProfitConfig | None = None) -> None:
        self.config = config or PartialTakeProfitConfig()
        self._reset()

    def _reset(self) -> None:
        self.symbol: str = ""
        self.entry_price: float = 0.0
        self.stop_price: float = 0.0
        self.risk_per_share: float = 0.0
        self.shares: int = 0
        self.levels_remaining: list[tuple[float, float]] = []
        self.fills: list[PartialFillEvent] = []
        self.is_open: bool = False

    def open_trade(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        shares: int,
    ) -> None:
        self._reset()
        self.symbol = symbol
        self.entry_price = entry_price
        self.stop_price = stop_price
        self.risk_per_share = entry_price - stop_price
        self.shares = shares
        self.levels_remaining = list(self.config.partial_levels)
        self.fills = []
        self.is_open = True

    def check_bar(self, current_high: float) -> list[PartialFillEvent]:
        if not self.is_open or self.shares <= 0 or not self.levels_remaining:
            return []

        new_fills: list[PartialFillEvent] = []

        while self.levels_remaining:
            r_target, pct = self.levels_remaining[0]
            target_price = self.entry_price + self.risk_per_share * r_target

            if current_high < target_price:
                break

            shares_to_sell = max(1, int(self.shares * pct / 100.0))
            shares_to_sell = min(shares_to_sell, self.shares)
            pnl = shares_to_sell * (target_price - self.entry_price)

            evt = PartialFillEvent(
                r_multiple_hit=r_target,
                shares_sold=shares_to_sell,
                sell_price=round(target_price, 4),
                pnl=round(pnl, 2),
                remaining_shares=self.shares - shares_to_sell,
                notes=f"Partial exit {pct}% at {r_target}R",
            )
            self.fills.append(evt)
            new_fills.append(evt)
            self.shares -= shares_to_sell
            self.levels_remaining.pop(0)

        if self.shares <= 0:
            self.is_open = False

        return new_fills

    def close_trade(
        self, close_price: float
    ) -> PartialFillEvent | None:
        if not self.is_open or self.shares <= 0:
            return None
        pnl = self.shares * (close_price - self.entry_price)
        evt = PartialFillEvent(
            r_multiple_hit=(close_price - self.entry_price) / self.risk_per_share
            if self.risk_per_share != 0 else 0.0,
            shares_sold=self.shares,
            sell_price=round(close_price, 4),
            pnl=round(pnl, 2),
            remaining_shares=0,
            notes="Final close",
        )
        self.fills.append(evt)
        self.shares = 0
        self.is_open = False
        return evt

    def summary(self) -> dict[str, Any]:
        total_pnl = sum(f.pnl for f in self.fills)
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "total_pnl": round(total_pnl, 2),
            "num_partial_exits": len(self.fills),
            "fills": [f.to_dict() for f in self.fills],
        }
