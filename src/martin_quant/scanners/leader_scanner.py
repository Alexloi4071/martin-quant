"""
Leader Scanner — Martin Luk 4hr Video (Market Health via Leader Breadth)

Martin's concept:
  "The number of leaders on my watchlist tells me the health of the market.
   When the list is expanding → I deploy more capital.
   When the list is shrinking → I cut exposure and wait."

This module:
  1. Ranks all symbols by Relative Strength (RS) vs SPY
  2. Applies fundamental filters (EPS growth, Sales growth) if data is available
  3. Produces a scored Leader List
  4. Tracks list size over time → market health signal
  5. Identifies which SECTORS are leading
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
import json
from pathlib import Path
from datetime import date

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LeaderStock:
    symbol: str
    rs_rank: float       # 0-100, higher = stronger
    rs_3m: float         # 3-month price return vs SPY
    rs_6m: float         # 6-month price return vs SPY
    rs_1y: float         # 1-year price return vs SPY
    composite_rs: float  # Weighted composite (IBD-style)
    above_ema50: bool
    above_ema200: bool
    new_high_within_15pct: bool   # Price within 15% of 52w high
    adr_pct: float
    sector: str
    score: float         # 0-100

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "rs_rank": round(self.rs_rank, 1),
            "rs_3m": round(self.rs_3m * 100, 1),
            "rs_6m": round(self.rs_6m * 100, 1),
            "composite_rs": round(self.composite_rs * 100, 1),
            "above_ema50": self.above_ema50,
            "new_high_within_15pct": self.new_high_within_15pct,
            "adr_pct": round(self.adr_pct * 100, 1),
            "sector": self.sector,
            "score": round(self.score, 1),
        }


@dataclass
class MarketHealthReading:
    date: str
    leader_count: int
    expanding: bool          # True if count > prev_count
    health_label: str        # 'STRONG' | 'MODERATE' | 'WEAK' | 'VERY_WEAK'
    exposure_factor: float   # 0.3 - 1.0, scales position sizes
    top_sectors: list[str]
    notes: str = ""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class LeaderConfig:
    # Minimum thresholds to qualify as a "leader"
    min_rs_rank: float = 80.0         # Top 20% RS strength
    min_adr_pct: float = 0.04         # ADR > 4%
    require_above_ema50: bool = True
    require_within_15pct_of_high: bool = True

    # RS composite weights (must sum to 1.0)
    rs_weight_3m: float = 0.40
    rs_weight_6m: float = 0.35
    rs_weight_1y: float = 0.25

    # Market health thresholds (by leader count)
    strong_threshold: int = 50
    moderate_threshold: int = 30
    weak_threshold: int = 15

    # History file for tracking leader count changes
    history_file: str = "data/leader_history.json"


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class LeaderScanner:
    """
    Builds and maintains the Leader List — the market's health barometer.
    """

    def __init__(
        self,
        config: Optional[LeaderConfig] = None,
        spy_df: Optional[pd.DataFrame] = None,
    ):
        self.config = config or LeaderConfig()
        self.spy_df = spy_df        # SPY daily OHLCV for RS calculation
        self._history: list[dict] = self._load_history()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_leader_list(
        self,
        universe: dict[str, pd.DataFrame],
        sector_map: Optional[dict[str, str]] = None,
    ) -> tuple[list[LeaderStock], MarketHealthReading]:
        """
        Main entry point.
        Returns (leader_list_sorted_by_score, market_health_reading).
        universe = {symbol: daily_df}
        sector_map = {symbol: sector_name}  (optional)
        """
        sector_map = sector_map or {}
        leaders: list[LeaderStock] = []

        # Calculate SPY returns for RS
        spy_returns = self._calc_spy_returns() if self.spy_df is not None else None

        all_rs = {}
        for symbol, df in universe.items():
            if len(df) < 252:
                continue
            try:
                rs_composite, rs_3m, rs_6m, rs_1y = self._calc_rs(
                    df, spy_returns
                )
                all_rs[symbol] = (rs_composite, rs_3m, rs_6m, rs_1y, df)
            except Exception as e:
                logger.debug(f"LeaderScanner RS error {symbol}: {e}")

        if not all_rs:
            logger.warning("LeaderScanner: no valid RS data")
            return [], self._make_health_reading(0, [])

        # Rank all symbols by composite RS
        sorted_by_rs = sorted(all_rs.items(), key=lambda x: x[1][0], reverse=True)
        total = len(sorted_by_rs)

        for rank_idx, (symbol, (rs_composite, rs_3m, rs_6m, rs_1y, df)) in enumerate(
            sorted_by_rs
        ):
            rs_rank = (1 - rank_idx / total) * 100   # 100 = top, 0 = bottom

            if rs_rank < self.config.min_rs_rank:
                break   # Already sorted; once below threshold, stop

            last = df.iloc[-1]
            ema50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
            ema200 = df["close"].ewm(span=200, adjust=False).mean().iloc[-1]
            high_52w = df["high"].rolling(252).max().iloc[-1]
            adr = ((df["high"] - df["low"]) / df["close"]).rolling(14).mean().iloc[-1]

            above_ema50 = last["close"] > ema50
            above_ema200 = last["close"] > ema200
            near_high = last["close"] >= high_52w * 0.85

            # Apply filters
            if self.config.require_above_ema50 and not above_ema50:
                continue
            if self.config.require_within_15pct_of_high and not near_high:
                continue
            if adr < self.config.min_adr_pct:
                continue

            score = self._calc_leader_score(
                rs_rank, rs_composite, above_ema50, above_ema200, near_high, adr
            )

            leaders.append(
                LeaderStock(
                    symbol=symbol,
                    rs_rank=round(rs_rank, 1),
                    rs_3m=rs_3m,
                    rs_6m=rs_6m,
                    rs_1y=rs_1y,
                    composite_rs=rs_composite,
                    above_ema50=above_ema50,
                    above_ema200=above_ema200,
                    new_high_within_15pct=near_high,
                    adr_pct=adr,
                    sector=sector_map.get(symbol, "Unknown"),
                    score=score,
                )
            )

        leaders.sort(key=lambda l: l.score, reverse=True)
        top_sectors = self._get_top_sectors(leaders)
        health = self._make_health_reading(len(leaders), top_sectors)
        self._save_history(health)

        logger.info(
            f"LeaderScanner: {len(leaders)} leaders | health={health.health_label} "
            f"| exposure={health.exposure_factor:.0%} | sectors={top_sectors[:3]}"
        )
        return leaders, health

    def get_market_health(self) -> Optional[MarketHealthReading]:
        """Return most recent saved health reading."""
        if not self._history:
            return None
        h = self._history[-1]
        return MarketHealthReading(**h)

    # ------------------------------------------------------------------
    # RS calculation (IBD composite style)
    # ------------------------------------------------------------------

    def _calc_spy_returns(self) -> dict[str, float]:
        if self.spy_df is None or len(self.spy_df) < 252:
            return {"3m": 0, "6m": 0, "1y": 0}
        close = self.spy_df["close"]
        return {
            "3m": close.iloc[-1] / close.iloc[-63] - 1 if len(close) >= 63 else 0,
            "6m": close.iloc[-1] / close.iloc[-126] - 1 if len(close) >= 126 else 0,
            "1y": close.iloc[-1] / close.iloc[-252] - 1 if len(close) >= 252 else 0,
        }

    def _calc_rs(
        self, df: pd.DataFrame, spy_returns: Optional[dict]
    ) -> tuple[float, float, float, float]:
        close = df["close"]
        rs_3m = close.iloc[-1] / close.iloc[-63] - 1 if len(close) >= 63 else 0
        rs_6m = close.iloc[-1] / close.iloc[-126] - 1 if len(close) >= 126 else 0
        rs_1y = close.iloc[-1] / close.iloc[-252] - 1 if len(close) >= 252 else 0

        # Relative to SPY (excess return)
        if spy_returns:
            rs_3m -= spy_returns.get("3m", 0)
            rs_6m -= spy_returns.get("6m", 0)
            rs_1y -= spy_returns.get("1y", 0)

        cfg = self.config
        composite = (
            rs_3m * cfg.rs_weight_3m
            + rs_6m * cfg.rs_weight_6m
            + rs_1y * cfg.rs_weight_1y
        )
        return composite, rs_3m, rs_6m, rs_1y

    # ------------------------------------------------------------------
    # Scoring & health
    # ------------------------------------------------------------------

    def _calc_leader_score(
        self, rs_rank, rs_composite, above_ema50, above_ema200, near_high, adr
    ) -> float:
        score = rs_rank * 0.50                        # 50% from RS rank
        score += min(rs_composite * 100, 30) * 0.30   # 30% from RS magnitude
        if above_ema50:
            score += 10
        if above_ema200:
            score += 5
        if near_high:
            score += 10
        adr_bonus = min(adr / 0.10, 1.0) * 5         # Up to 5 bonus points
        score += adr_bonus
        return min(score, 100)

    def _make_health_reading(
        self, leader_count: int, top_sectors: list[str]
    ) -> MarketHealthReading:
        cfg = self.config
        prev_count = self._history[-1]["leader_count"] if self._history else 0
        expanding = leader_count > prev_count

        if leader_count >= cfg.strong_threshold:
            label, exposure = "STRONG", 1.0
        elif leader_count >= cfg.moderate_threshold:
            label, exposure = "MODERATE", 0.75
        elif leader_count >= cfg.weak_threshold:
            label, exposure = "WEAK", 0.50
        else:
            label, exposure = "VERY_WEAK", 0.30

        # Expanding market gets bonus exposure
        if expanding and exposure < 1.0:
            exposure = min(exposure + 0.10, 1.0)

        notes = "expanding" if expanding else "contracting"
        if leader_count == 0:
            notes = "no leaders — stay in cash"

        return MarketHealthReading(
            date=str(date.today()),
            leader_count=leader_count,
            expanding=expanding,
            health_label=label,
            exposure_factor=exposure,
            top_sectors=top_sectors,
            notes=notes,
        )

    def _get_top_sectors(self, leaders: list[LeaderStock]) -> list[str]:
        sector_counts: dict[str, int] = {}
        for l in leaders:
            sector_counts[l.sector] = sector_counts.get(l.sector, 0) + 1
        sorted_sectors = sorted(sector_counts, key=sector_counts.get, reverse=True)
        return sorted_sectors[:5]

    # ------------------------------------------------------------------
    # History persistence
    # ------------------------------------------------------------------

    def _load_history(self) -> list[dict]:
        p = Path(self.config.history_file)
        if p.exists():
            try:
                with open(p) as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_history(self, health: MarketHealthReading) -> None:
        p = Path(self.config.history_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "date": health.date,
            "leader_count": health.leader_count,
            "expanding": health.expanding,
            "health_label": health.health_label,
            "exposure_factor": health.exposure_factor,
            "top_sectors": health.top_sectors,
            "notes": health.notes,
        }
        self._history.append(entry)
        # Keep last 365 days
        self._history = self._history[-365:]
        try:
            with open(p, "w") as f:
                json.dump(self._history, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save leader history: {e}")
