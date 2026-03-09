"""portfolio_limits.py  (v2 — dynamic gross exposure per market regime)

Martin Luk 影片 40:38 重點:
  - 牛市最大用到 280% Gross Exposure (借孔打)
  - 中性市場 除以 2: 140%
  - 熊市 全現金 / 只做空: 0-50%

v2 新增:
  - set_market_regime() 方法 — 動態調整 max_gross_exposure
  - position_size_factor 簡化 API
  - Leading / Lagging 花色不同香應
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class PortfolioLimitsConfig:
    # Position count
    max_open_trades: int = 10

    # Concentration
    max_sector_concentration_pct: float = 40.0   # max % of equity in one sector
    max_single_position_pct: float = 30.0         # max % of equity in one stock
    max_single_position_small_cap_pct: float = 15.0  # stricter for small caps
    small_cap_threshold: float = 2_000_000_000.0  # $2B market cap threshold

    # Gross exposure by regime (Martin's 280% bull rule)
    bull_max_gross_exposure_pct: float = 280.0    # full margin deployment
    neutral_max_gross_exposure_pct: float = 150.0 # half-size
    bear_max_gross_exposure_pct: float = 50.0     # cash + small shorts only

    # Category limits
    max_leading_positions: int = 8    # Leading stocks can be majority
    max_mediocre_positions: int = 3   # Limit mediocre entries
    max_lagging_positions: int = 1    # Only 1 lagging (e.g. short)


# ---------------------------------------------------------------------------
# Open position
# ---------------------------------------------------------------------------

@dataclass
class OpenPosition:
    symbol: str
    sector: str
    position_value: float      # in dollars
    entry_price: float
    shares: int
    direction: str = "long"    # "long" | "short"
    category: str = "leading"  # "leading" | "mediocre" | "lagging" | "pillar"
    market_cap: Optional[float] = None


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

class PortfolioLimitsChecker:
    """
    Enforces portfolio-level risk limits, with dynamic gross exposure
    that adjusts to the current market regime.

    Usage:
        checker = PortfolioLimitsChecker(cfg)
        checker.set_market_regime("bull")    # <-- call daily
        ok, reasons = checker.can_add_trade(...)
        if ok:
            checker.add_position(pos)
    """

    def __init__(self, config: Optional[PortfolioLimitsConfig] = None) -> None:
        self.config    = config or PortfolioLimitsConfig()
        self.positions: list[OpenPosition] = []
        self._regime   = "neutral"          # current market regime

    # ------------------------------------------------------------------
    # Regime management
    # ------------------------------------------------------------------

    def set_market_regime(self, regime: str) -> None:
        """
        Set the market regime to adjust gross exposure limits.

        Parameters
        ----------
        regime : str  "bull" | "neutral" | "bear"
        """
        regime = regime.lower()
        if regime not in ("bull", "neutral", "bear"):
            raise ValueError(f"regime must be 'bull', 'neutral', or 'bear', got '{regime}'")
        self._regime = regime

    @property
    def max_gross_exposure_pct(self) -> float:
        """Dynamic max gross exposure based on market regime."""
        cfg = self.config
        return {
            "bull":    cfg.bull_max_gross_exposure_pct,
            "neutral": cfg.neutral_max_gross_exposure_pct,
            "bear":    cfg.bear_max_gross_exposure_pct,
        }[self._regime]

    # ------------------------------------------------------------------
    # Portfolio metrics
    # ------------------------------------------------------------------

    @property
    def open_trade_count(self) -> int:
        return len(self.positions)

    def gross_exposure(self, equity: float) -> float:
        """Total position value as % of equity."""
        total = sum(p.position_value for p in self.positions)
        return (total / equity * 100) if equity > 0 else 0.0

    def sector_exposure_pct(self, sector: str, equity: float) -> float:
        sector_val = sum(
            p.position_value for p in self.positions if p.sector == sector
        )
        return (sector_val / equity * 100) if equity > 0 else 0.0

    def category_count(self, category: str) -> int:
        return sum(1 for p in self.positions if p.category == category)

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def can_add_trade(
        self,
        symbol: str,
        sector: str,
        new_position_value: float,
        equity: float,
        direction: str = "long",
        category: str = "leading",
        market_cap: Optional[float] = None,
    ) -> tuple[bool, list[str]]:
        """
        Returns (can_trade: bool, reasons: list[str]).
        reasons is empty when can_trade is True.
        """
        cfg     = self.config
        reasons: list[str] = []

        # 1. Max open positions
        if self.open_trade_count >= cfg.max_open_trades:
            reasons.append(
                f"Max open trades reached ({cfg.max_open_trades})."
            )

        # 2. Gross exposure
        proj_gross = self.gross_exposure(equity) + (new_position_value / equity * 100)
        if proj_gross > self.max_gross_exposure_pct:
            reasons.append(
                f"Gross exposure {proj_gross:.1f}% would exceed "
                f"{self.max_gross_exposure_pct:.0f}% limit "
                f"(regime={self._regime})."
            )

        # 3. Single position size
        pos_pct = new_position_value / equity * 100
        is_small_cap = market_cap is not None and market_cap < cfg.small_cap_threshold
        if is_small_cap and pos_pct > cfg.max_single_position_small_cap_pct:
            reasons.append(
                f"Small-cap position {pos_pct:.1f}% exceeds "
                f"{cfg.max_single_position_small_cap_pct:.0f}% limit."
            )
        elif pos_pct > cfg.max_single_position_pct:
            reasons.append(
                f"Position size {pos_pct:.1f}% exceeds "
                f"{cfg.max_single_position_pct:.0f}% limit."
            )

        # 4. Sector concentration
        proj_sector = self.sector_exposure_pct(sector, equity) + pos_pct
        if proj_sector > cfg.max_sector_concentration_pct:
            reasons.append(
                f"Sector '{sector}' exposure {proj_sector:.1f}% would exceed "
                f"{cfg.max_sector_concentration_pct:.0f}% limit."
            )

        # 5. Category limits (bear regime: block new longs)
        if self._regime == "bear" and direction == "long":
            reasons.append("Bear regime: no new long positions.")

        if category == "mediocre" and self.category_count("mediocre") >= cfg.max_mediocre_positions:
            reasons.append(
                f"Max mediocre positions ({cfg.max_mediocre_positions}) reached."
            )
        if category == "lagging" and self.category_count("lagging") >= cfg.max_lagging_positions:
            reasons.append(
                f"Max lagging positions ({cfg.max_lagging_positions}) reached."
            )

        return len(reasons) == 0, reasons

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def add_position(self, pos: OpenPosition) -> None:
        self.positions.append(pos)

    def remove_position(self, symbol: str) -> None:
        self.positions = [p for p in self.positions if p.symbol != symbol]

    def clear(self) -> None:
        self.positions.clear()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self, equity: float) -> dict:
        return {
            "regime":            self._regime,
            "open_trades":       self.open_trade_count,
            "gross_exposure_pct": round(self.gross_exposure(equity), 1),
            "max_gross_pct":     self.max_gross_exposure_pct,
            "sectors":           list({p.sector for p in self.positions}),
            "categories":        {cat: self.category_count(cat)
                                   for cat in ("leading", "mediocre", "lagging", "pillar")},
        }
