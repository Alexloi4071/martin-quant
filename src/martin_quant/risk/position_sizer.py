from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PositionSizerConfig:
    per_trade_risk_pct: float = 0.5
    max_position_pct: float = 30.0
    max_position_small_cap_pct: float = 20.0
    small_cap_threshold_mcap: float = 2_000_000_000.0


@dataclass(slots=True)
class PositionSizeResult:
    symbol: str
    shares: int
    position_value: float
    position_pct_of_equity: float
    risk_per_trade: float
    risk_pct_of_equity: float
    entry_price: float
    stop_price: float
    risk_per_share: float
    r_multiple_target: float
    target_price: float
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "shares": self.shares,
            "position_value": round(self.position_value, 2),
            "position_pct_of_equity": round(self.position_pct_of_equity, 2),
            "risk_per_trade": round(self.risk_per_trade, 2),
            "risk_pct_of_equity": round(self.risk_pct_of_equity, 2),
            "entry_price": round(self.entry_price, 4),
            "stop_price": round(self.stop_price, 4),
            "risk_per_share": round(self.risk_per_share, 4),
            "r_multiple_target": self.r_multiple_target,
            "target_price": round(self.target_price, 4),
            "notes": self.notes,
        }


class PositionSizer:
    """
    Fixed fractional position sizer based on per-trade risk %.

    Formula:
        risk_dollars   = equity * per_trade_risk_pct / 100
        risk_per_share = entry_price - stop_price
        shares         = floor(risk_dollars / risk_per_share)
        position_value = shares * entry_price

    Caps:
        position_value <= equity * max_position_pct / 100
        (small cap: uses max_position_small_cap_pct instead)
    """

    def __init__(self, config: PositionSizerConfig | None = None) -> None:
        self.config = config or PositionSizerConfig()

    def size(
        self,
        symbol: str,
        equity: float,
        entry_price: float,
        stop_price: float,
        target_price: float | None = None,
        r_multiple_target: float = 3.0,
        market_cap: float | None = None,
    ) -> PositionSizeResult | None:
        cfg = self.config
        notes: list[str] = []

        if entry_price <= 0 or stop_price <= 0 or equity <= 0:
            return None

        risk_per_share = entry_price - stop_price
        if risk_per_share <= 0:
            return None

        risk_dollars = equity * cfg.per_trade_risk_pct / 100.0
        raw_shares = int(risk_dollars / risk_per_share)

        is_small_cap = (
            market_cap is not None and
            market_cap < cfg.small_cap_threshold_mcap
        )
        max_pos_pct = cfg.max_position_small_cap_pct if is_small_cap else cfg.max_position_pct
        if is_small_cap:
            notes.append(f"Small cap cap applied: max {max_pos_pct}%")

        max_position_value = equity * max_pos_pct / 100.0
        max_shares_by_cap  = int(max_position_value / entry_price)
        shares = min(raw_shares, max_shares_by_cap)

        if shares <= 0:
            return None

        position_value = shares * entry_price
        actual_risk    = shares * risk_per_share

        if target_price is None:
            target_price = entry_price + risk_per_share * r_multiple_target

        return PositionSizeResult(
            symbol=symbol,
            shares=shares,
            position_value=position_value,
            position_pct_of_equity=position_value / equity * 100.0,
            risk_per_trade=actual_risk,
            risk_pct_of_equity=actual_risk / equity * 100.0,
            entry_price=entry_price,
            stop_price=stop_price,
            risk_per_share=risk_per_share,
            r_multiple_target=r_multiple_target,
            target_price=target_price,
            notes=notes,
        )
