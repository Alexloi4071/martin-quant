"""
Dynamic Position Sizer — Martin Luk 4hr Video (40:38 - 48:22)

Martin's exact rules:
  - Base risk per trade: 0.25% - 0.5% of equity
  - Position size = risk% / stop%
  - Example: risk=0.5%, stop=1.5% → position=33% of equity
  - Can hold multiple positions → total exposure up to 200-280%
  - Small caps / micro caps: hard cap at 15-20% (gap risk)
  - When market is WEAK: reduce all sizes by exposure_factor from LeaderScanner
  - Never risk more than 1% on a single trade (hard rule)

This module integrates with:
  - LeaderScanner → exposure_factor
  - RegimeDetector → regime_multiplier
  - PullbackSignal / BreakoutSignal → stop_pct
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SizingResult:
    symbol: str
    equity: float
    shares: int
    dollar_size: float
    position_pct: float       # % of equity
    risk_dollars: float       # Actual $ at risk
    risk_pct: float           # % of equity at risk
    stop_pct: float           # Stop distance %
    r_multiple_target: float
    notes: str = ""

    def __str__(self) -> str:
        return (
            f"{self.symbol}: {self.shares} shares | "
            f"${self.dollar_size:,.0f} ({self.position_pct:.1%} equity) | "
            f"Risk: ${self.risk_dollars:,.0f} ({self.risk_pct:.2%}) | "
            f"Stop: {self.stop_pct:.1%} | Target: {self.r_multiple_target}R"
        )


@dataclass
class SizerConfig:
    base_risk_pct: float = 0.005       # 0.5% base risk per trade
    max_risk_pct: float = 0.010        # Hard cap: never risk > 1% on one trade
    min_risk_pct: float = 0.002        # Minimum meaningful risk = 0.2%

    # Position size caps
    max_position_pct: float = 0.35     # Single position max 35%
    max_small_cap_pct: float = 0.15    # Small cap ($0-$2B mktcap) max 15%
    max_micro_cap_pct: float = 0.10    # Micro cap (<$500M) max 10%
    small_cap_threshold: float = 2e9   # $2B
    micro_cap_threshold: float = 5e8   # $500M

    # Total portfolio exposure
    max_total_exposure: float = 2.50   # 250% max total exposure (with margin)
    max_positions: int = 8             # Max simultaneous positions

    # Regime multipliers (applied on top of LeaderScanner exposure)
    regime_multipliers: dict = None

    def __post_init__(self):
        if self.regime_multipliers is None:
            self.regime_multipliers = {
                "BULL": 1.0,
                "WEAK_BULL": 0.80,
                "CHOPPY": 0.60,
                "BEAR": 0.40,
                "UNKNOWN": 0.70,
            }


class PositionSizer:
    """
    Calculates dynamic position sizes for Martin Luk's swing trading system.
    Integrates market health (leader breadth) and regime into every calculation.
    """

    def __init__(self, config: Optional[SizerConfig] = None):
        self.config = config or SizerConfig()

    # ------------------------------------------------------------------
    # Core sizing
    # ------------------------------------------------------------------

    def size(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        equity: float,
        regime: str = "BULL",
        exposure_factor: float = 1.0,   # From LeaderScanner.MarketHealthReading
        market_cap: Optional[float] = None,
        current_exposure: float = 0.0,   # Sum of all open position sizes / equity
        r_multiple_target: float = 2.5,
    ) -> Optional[SizingResult]:
        """
        Calculate position size for a single trade.

        Returns None if:
          - Stop is invalid
          - Risk would exceed hard caps
          - Portfolio is already at max exposure
        """
        if entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
            logger.warning(f"{symbol}: invalid stop ({stop_price:.2f} >= entry {entry_price:.2f})")
            return None

        stop_pct = (entry_price - stop_price) / entry_price
        if stop_pct > 0.12:   # Stop wider than 12% → skip
            logger.info(f"{symbol}: stop too wide ({stop_pct:.1%}), skipping")
            return None

        # ---- Adjust risk based on regime + market health ----
        regime_mult = self.config.regime_multipliers.get(regime, 0.70)
        adjusted_risk_pct = self.config.base_risk_pct * regime_mult * exposure_factor
        adjusted_risk_pct = max(self.config.min_risk_pct,
                                 min(adjusted_risk_pct, self.config.max_risk_pct))

        # ---- Position size from risk ----
        # position_pct = risk_pct / stop_pct
        raw_position_pct = adjusted_risk_pct / stop_pct

        # ---- Apply caps ----
        max_pct = self._get_max_position_pct(market_cap)
        position_pct = min(raw_position_pct, max_pct)

        # ---- Check remaining portfolio capacity ----
        remaining_capacity = self.config.max_total_exposure - current_exposure
        if remaining_capacity <= 0.05:
            logger.info(f"{symbol}: portfolio at max exposure ({current_exposure:.0%}), skipping")
            return None
        position_pct = min(position_pct, remaining_capacity)

        # ---- Convert to dollars and shares ----
        dollar_size = equity * position_pct
        shares = int(dollar_size / entry_price)

        if shares < 1:
            return None

        actual_dollar = shares * entry_price
        actual_pct = actual_dollar / equity
        risk_dollars = shares * (entry_price - stop_price)
        risk_pct = risk_dollars / equity

        notes_parts = []
        if regime_mult < 1.0:
            notes_parts.append(f"regime={regime}({regime_mult:.0%})")
        if exposure_factor < 1.0:
            notes_parts.append(f"health_factor={exposure_factor:.0%}")
        if market_cap and market_cap < self.config.small_cap_threshold:
            cap_label = "micro" if market_cap < self.config.micro_cap_threshold else "small"
            notes_parts.append(f"{cap_label}_cap_cap={max_pct:.0%}")

        logger.info(
            f"Sized {symbol}: {shares} sh @ ${entry_price:.2f} = "
            f"${actual_dollar:,.0f} ({actual_pct:.1%}) | risk={risk_pct:.2%}"
        )

        return SizingResult(
            symbol=symbol,
            equity=equity,
            shares=shares,
            dollar_size=round(actual_dollar, 2),
            position_pct=round(actual_pct, 4),
            risk_dollars=round(risk_dollars, 2),
            risk_pct=round(risk_pct, 4),
            stop_pct=round(stop_pct, 4),
            r_multiple_target=r_multiple_target,
            notes="; ".join(notes_parts),
        )

    def size_portfolio(
        self,
        candidates: list[dict],   # [{symbol, entry, stop, market_cap}, ...]
        equity: float,
        regime: str = "BULL",
        exposure_factor: float = 1.0,
    ) -> list[SizingResult]:
        """
        Size a full list of candidates, tracking cumulative exposure.
        Candidates should already be sorted by score/priority.
        """
        results = []
        current_exposure = 0.0

        for c in candidates:
            if len(results) >= self.config.max_positions:
                break

            result = self.size(
                symbol=c["symbol"],
                entry_price=c["entry"],
                stop_price=c["stop"],
                equity=equity,
                regime=regime,
                exposure_factor=exposure_factor,
                market_cap=c.get("market_cap"),
                current_exposure=current_exposure,
            )
            if result:
                results.append(result)
                current_exposure += result.position_pct

        logger.info(
            f"Portfolio sized: {len(results)} positions | "
            f"total exposure: {current_exposure:.0%}"
        )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_max_position_pct(self, market_cap: Optional[float]) -> float:
        if market_cap is None:
            return self.config.max_position_pct
        if market_cap < self.config.micro_cap_threshold:
            return self.config.max_micro_cap_pct
        if market_cap < self.config.small_cap_threshold:
            return self.config.max_small_cap_pct
        return self.config.max_position_pct

    def simulate_portfolio_stats(
        self,
        results: list[SizingResult],
    ) -> dict:
        """Quick summary stats for a sized portfolio."""
        if not results:
            return {}
        total_exposure = sum(r.position_pct for r in results)
        total_risk = sum(r.risk_pct for r in results)
        avg_stop = sum(r.stop_pct for r in results) / len(results)
        return {
            "positions": len(results),
            "total_exposure_pct": round(total_exposure * 100, 1),
            "total_risk_pct": round(total_risk * 100, 2),
            "avg_stop_pct": round(avg_stop * 100, 1),
            "max_single_risk": round(max(r.risk_pct for r in results) * 100, 2),
        }
