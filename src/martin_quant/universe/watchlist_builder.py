"""watchlist_builder.py  (v2 — Martin Luk corrected filters)

Key fixes in this version:
  1. ADR >= 5.0% HARD FILTER  (Martin: only fast-moving stocks)
  2. Dollar Volume filter       (price × avg_volume, not raw volume)
  3. Stock category tagging    (Leading / Mediocre / Lagging / Pillar)
  4. 150 EMA trend filter      (price > ema_150 for long candidates)

Martin's universe criteria (financialwisdomtv interview 2026-02):
  - ADR% > 5%        : only volatile / fast-moving stocks
  - Dollar Vol > $10M/day : sufficient liquidity
  - RS rank > 70     : outperforming 70% of the market
  - Price > $10      : avoid penny stocks
  - Close > EMA 150  : long-term uptrend filter for longs

Category definitions:
  Leading  : RS rank >= 80  (top 20%)
  Mediocre : RS rank 50-79
  Lagging  : RS rank < 50
  Pillar   : market_cap >= $100B (mega-cap anchor positions)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class WatchlistConfig:
    # Price filters
    min_price: float = 10.0
    max_price: float = 2000.0

    # ADR% (Average Daily Range) — Martin HARD minimum
    min_adr_pct: float = 5.0           # MUST be >= 5% per Martin Luk
    adr_lookback: int = 20

    # Dollar Volume (price × volume) — NOT raw share volume
    min_dollar_volume_20d: float = 10_000_000.0   # $10M/day minimum
    dollar_vol_lookback: int = 20

    # Relative Strength
    min_rs_rank_pct: float = 70.0      # RS rank percentile (0-100)
    rs_lookback_days: int = 252        # 1-year RS calculation window

    # Category thresholds
    leading_rs_min: float = 80.0       # RS >= 80 = Leading
    mediocre_rs_min: float = 50.0      # RS 50-79 = Mediocre
    pillar_mcap_min: float = 100_000_000_000.0  # $100B+ = Pillar

    # EMA trend
    require_above_ema150: bool = True   # close > EMA150 for long candidates

    # Market cap
    min_market_cap: float = 500_000_000.0   # $500M minimum


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WatchlistEntry:
    symbol: str
    price: float
    adr_pct: float
    avg_dollar_volume_20d: float
    rs_rank_pct: float
    rs_1yr_pct: float
    above_ema150: bool
    market_cap: Optional[float]
    sector: str
    theme: str
    category: str       # "leading" | "mediocre" | "lagging" | "pillar"
    volume_rank: float  # 0-100 percentile

    def to_dict(self) -> dict:
        return {
            "symbol":              self.symbol,
            "price":               round(self.price, 2),
            "adr_pct":             round(self.adr_pct, 2),
            "avg_dollar_volume_20d": round(self.avg_dollar_volume_20d, 0),
            "rs_rank_pct":         round(self.rs_rank_pct, 1),
            "rs_1yr_pct":          round(self.rs_1yr_pct, 2),
            "above_ema150":        self.above_ema150,
            "market_cap":          self.market_cap,
            "sector":              self.sector,
            "theme":               self.theme,
            "category":            self.category,
            "volume_rank":         round(self.volume_rank, 1),
        }


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def compute_adr_pct(df: pd.DataFrame, lookback: int = 20) -> float:
    """
    Average Daily Range% = mean((high - low) / low * 100) over last N bars.
    This is the PRIMARY fast-stock filter for Martin Luk strategy.
    """
    recent = df.tail(lookback)
    adr = ((recent["high"] - recent["low"]) / recent["low"] * 100).mean()
    return float(adr)


def compute_dollar_volume(df: pd.DataFrame, lookback: int = 20) -> float:
    """
    Average daily dollar volume = mean(close * volume) over last N bars.
    Martin uses DOLLAR volume (not share volume) for liquidity screening.
    Minimum threshold: $10M/day.
    """
    recent = df.tail(lookback)
    dollar_vol = (recent["close"] * recent["volume"]).mean()
    return float(dollar_vol)


def compute_rs_vs_benchmark(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    lookback_days: int = 252,
) -> float:
    """
    Relative Strength = stock 1-year return / SPY 1-year return.
    Returns the RS ratio (> 1.0 = outperforming SPY).
    """
    stock_ret = (
        df["close"].iloc[-1] / df["close"].iloc[-min(lookback_days, len(df))] - 1
    )
    bench_ret = (
        benchmark_df["close"].iloc[-1]
        / benchmark_df["close"].iloc[-min(lookback_days, len(benchmark_df))] - 1
    )
    if bench_ret == 0:
        return 0.0
    return float(stock_ret / abs(bench_ret))


def compute_ema150(df: pd.DataFrame) -> Optional[float]:
    """Compute the latest 150-day EMA value."""
    if len(df) < 150:
        return None
    ema = df["close"].ewm(span=150, adjust=False, min_periods=150).mean()
    return float(ema.iloc[-1])


def classify_category(
    rs_rank_pct: float,
    market_cap: Optional[float],
    cfg: WatchlistConfig,
) -> str:
    """
    Martin's four-category classification:
      Pillar   = mega-cap ($100B+), low ADR but stable anchors
      Leading  = RS >= 80, fast-moving leaders
      Mediocre = RS 50-79, middle of the pack
      Lagging  = RS < 50, avoid or short candidates
    """
    if market_cap and market_cap >= cfg.pillar_mcap_min:
        return "pillar"
    if rs_rank_pct >= cfg.leading_rs_min:
        return "leading"
    if rs_rank_pct >= cfg.mediocre_rs_min:
        return "mediocre"
    return "lagging"


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

class WatchlistBuilder:
    """
    Builds a filtered, ranked watchlist from a universe of OHLCV data.

    Usage:
        builder = WatchlistBuilder(config)
        entries = builder.build(
            symbols=symbols,
            ohlcv_map=ohlcv_map,
            spy_df=spy_df,
            metadata=meta_dict,   # {symbol: {sector, theme, market_cap}}
        )
        df = pd.DataFrame([e.to_dict() for e in entries])
    """

    def __init__(self, config: Optional[WatchlistConfig] = None) -> None:
        self.config = config or WatchlistConfig()

    def _passes_filters(self, df: pd.DataFrame) -> tuple[bool, float, float, Optional[float]]:
        """
        Returns (passes, adr_pct, dollar_vol, ema150)
        Applies all hard filters.
        """
        cfg = self.config

        if len(df) < 30:
            return False, 0.0, 0.0, None

        last_close = float(df["close"].iloc[-1])

        # Price filter
        if not (cfg.min_price <= last_close <= cfg.max_price):
            return False, 0.0, 0.0, None

        # ADR filter (HARD — Martin requires > 5%)
        adr = compute_adr_pct(df, cfg.adr_lookback)
        if adr < cfg.min_adr_pct:
            return False, adr, 0.0, None

        # Dollar Volume filter
        dvol = compute_dollar_volume(df, cfg.dollar_vol_lookback)
        if dvol < cfg.min_dollar_volume_20d:
            return False, adr, dvol, None

        # EMA 150 filter
        ema150 = compute_ema150(df)
        if cfg.require_above_ema150 and ema150 is not None:
            if last_close < ema150:
                return False, adr, dvol, ema150

        return True, adr, dvol, ema150

    def build(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        spy_df: Optional[pd.DataFrame] = None,
        metadata: Optional[dict[str, dict]] = None,
    ) -> list[WatchlistEntry]:
        """
        Build and rank the watchlist.

        Parameters
        ----------
        symbols : list[str]
        ohlcv_map : dict  {symbol: daily_ohlcv_df}
        spy_df : pd.DataFrame, optional
            SPY daily OHLCV for RS calculation.
        metadata : dict, optional
            {symbol: {"sector": str, "theme": str, "market_cap": float}}

        Returns
        -------
        list[WatchlistEntry] sorted by RS rank desc.
        """
        cfg  = self.config
        meta = metadata or {}

        # Step 1: compute RS for all symbols
        rs_map: dict[str, float] = {}
        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None or len(df) < 20:
                continue
            if spy_df is not None:
                rs_map[sym] = compute_rs_vs_benchmark(df, spy_df, cfg.rs_lookback_days)
            else:
                # Fallback: use 1-year return as proxy
                lookback = min(252, len(df))
                rs_map[sym] = float(df["close"].iloc[-1] / df["close"].iloc[-lookback] - 1)

        # Step 2: compute RS rank percentile across universe
        if rs_map:
            rs_values = np.array(list(rs_map.values()))
            rs_ranks = {sym: float(np.mean(rs_values <= v) * 100)
                        for sym, v in rs_map.items()}
        else:
            rs_ranks = {sym: 0.0 for sym in symbols}

        # Step 3: compute volume rank percentile
        dvol_map: dict[str, float] = {}
        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is not None and len(df) >= 20:
                dvol_map[sym] = compute_dollar_volume(df, cfg.dollar_vol_lookback)
        if dvol_map:
            dv_values = np.array(list(dvol_map.values()))
            vol_ranks = {sym: float(np.mean(dv_values <= v) * 100)
                         for sym, v in dvol_map.items()}
        else:
            vol_ranks = {sym: 0.0 for sym in symbols}

        # Step 4: apply filters + build entries
        entries: list[WatchlistEntry] = []
        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None:
                continue

            rs_rank = rs_ranks.get(sym, 0.0)
            if rs_rank < cfg.min_rs_rank_pct:
                continue

            passes, adr, dvol, ema150 = self._passes_filters(df)
            if not passes:
                continue

            sym_meta    = meta.get(sym.upper(), {})
            last_close  = float(df["close"].iloc[-1])
            market_cap  = sym_meta.get("market_cap")
            above_ema150 = (ema150 is not None) and (last_close > ema150)

            rs_1yr = rs_map.get(sym, 0.0) * 100  # convert to pct

            entries.append(WatchlistEntry(
                symbol=sym.upper(),
                price=last_close,
                adr_pct=adr,
                avg_dollar_volume_20d=dvol,
                rs_rank_pct=rs_rank,
                rs_1yr_pct=rs_1yr,
                above_ema150=above_ema150,
                market_cap=market_cap,
                sector=sym_meta.get("sector", "Unknown"),
                theme=sym_meta.get("theme", ""),
                category=classify_category(rs_rank, market_cap, cfg),
                volume_rank=vol_ranks.get(sym, 0.0),
            ))

        # Sort by RS rank desc (Leading stocks first)
        return sorted(entries, key=lambda e: e.rs_rank_pct, reverse=True)
