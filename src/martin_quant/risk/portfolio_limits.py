from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PortfolioLimitsConfig:
    max_open_trades: int = 10
    max_sector_concentration_pct: float = 40.0
    max_gross_exposure_pct: float = 280.0


@dataclass
class OpenPosition:
    symbol: str
    sector: str
    position_value: float
    entry_price: float
    shares: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sector": self.sector,
            "position_value": round(self.position_value, 2),
            "entry_price": round(self.entry_price, 4),
            "shares": self.shares,
        }


class PortfolioLimitsChecker:
    """
    Checks if adding a new trade would breach any portfolio-level limits.

    Limits:
    - max_open_trades: total number of concurrent positions
    - max_sector_concentration_pct: single sector as % of total equity
    - max_gross_exposure_pct: total position value as % of equity
    """

    def __init__(self, config: PortfolioLimitsConfig | None = None) -> None:
        self.config = config or PortfolioLimitsConfig()
        self.positions: list[OpenPosition] = []

    def add_position(self, position: OpenPosition) -> None:
        self.positions.append(position)

    def remove_position(self, symbol: str) -> None:
        self.positions = [p for p in self.positions if p.symbol.upper() != symbol.upper()]

    def can_add_trade(
        self,
        symbol: str,
        sector: str,
        new_position_value: float,
        equity: float,
    ) -> tuple[bool, list[str]]:
        cfg = self.config
        reasons: list[str] = []

        existing_symbols = {p.symbol.upper() for p in self.positions}
        if symbol.upper() in existing_symbols:
            reasons.append(f"{symbol} already in portfolio")

        if len(self.positions) >= cfg.max_open_trades:
            reasons.append(
                f"Max open trades reached ({cfg.max_open_trades})"
            )

        sector_exposure = sum(
            p.position_value for p in self.positions
            if p.sector.lower() == sector.lower()
        )
        sector_pct = (sector_exposure + new_position_value) / equity * 100.0
        if sector_pct > cfg.max_sector_concentration_pct:
            reasons.append(
                f"Sector '{sector}' would be {sector_pct:.1f}% "
                f"(limit {cfg.max_sector_concentration_pct}%)"
            )

        gross_exposure = sum(p.position_value for p in self.positions)
        gross_pct = (gross_exposure + new_position_value) / equity * 100.0
        if gross_pct > cfg.max_gross_exposure_pct:
            reasons.append(
                f"Gross exposure would be {gross_pct:.1f}% "
                f"(limit {cfg.max_gross_exposure_pct}%)"
            )

        return (len(reasons) == 0, reasons)

    def portfolio_summary(self, equity: float) -> dict[str, Any]:
        total_value = sum(p.position_value for p in self.positions)
        sector_breakdown: dict[str, float] = {}
        for p in self.positions:
            sector_breakdown[p.sector] = sector_breakdown.get(p.sector, 0.0) + p.position_value

        return {
            "open_trades": len(self.positions),
            "gross_exposure": round(total_value, 2),
            "gross_exposure_pct": round(total_value / equity * 100.0, 2) if equity else 0.0,
            "sector_breakdown": {
                k: round(v / equity * 100.0, 2) for k, v in sector_breakdown.items()
            },
            "positions": [p.to_dict() for p in self.positions],
        }
