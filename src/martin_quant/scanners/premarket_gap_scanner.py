"""premarket_gap_scanner.py

Pre-Market Gap Scanner — Martin Luk Scanner #1

用途: 每天開市前找出 gap-up 候選珠，準備 EPS 隆出交易

條件:
  1. Gap-up % >= min_gap_pct  (e.g. 5%)
  2. Pre-market volume > prev_avg_vol * rvol_threshold  (e.g. 2x)
  3. Gap 是在 EMA9/21 之上 (not gap-down into support)
  4. ADR >= 4%  (at least somewhat fast-moving)
  5. Optional: EPS catalyst flag

Data source note:
  Pre-market OHLCV needs a provider that gives pre-market data.
  This module accepts pre-market bars as a separate DataFrame.
  If unavailable, pass gap_pct and premarket_vol directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class GapScanConfig:
    min_gap_pct: float = 4.0          # minimum gap-up % from prev close
    max_gap_pct: float = 50.0         # avoid crazy pre-earnings gaps
    min_rvol_premarket: float = 2.0   # pre-mkt volume vs 20d avg
    min_adr_pct: float = 4.0          # stock must be fast-moving
    require_above_ema21: bool = True   # gap must be above EMA21
    ema_span: int = 21


@dataclass
class GapCandidate:
    symbol: str
    prev_close: float
    premarket_price: float
    gap_pct: float
    premarket_rvol: float
    has_eps_catalyst: bool
    above_ema21: bool
    score: float
    notes: str

    def to_dict(self) -> dict:
        return {
            "symbol":            self.symbol,
            "prev_close":        round(self.prev_close, 2),
            "premarket_price":   round(self.premarket_price, 2),
            "gap_pct":           round(self.gap_pct, 2),
            "premarket_rvol":    round(self.premarket_rvol, 2),
            "has_eps_catalyst":  self.has_eps_catalyst,
            "above_ema21":       self.above_ema21,
            "score":             round(self.score, 3),
            "notes":             self.notes,
        }


class PremarketGapScanner:
    """
    Identifies pre-market gap-up candidates for EPS episodic pivots.

    Usage:
        scanner = PremarketGapScanner()
        candidates = scanner.scan(
            symbols=["NVDA", "SMCI"],
            daily_ohlcv_map={...},
            premarket_prices={"NVDA": 145.0},   # from data provider
            premarket_volumes={"NVDA": 5_000_000},
            eps_catalyst_set={"NVDA"},
        )
    """

    def __init__(self, config: Optional[GapScanConfig] = None) -> None:
        self.config = config or GapScanConfig()

    def _score(self, gap_pct: float, rvol: float, eps: bool, above_ema: bool) -> float:
        score = 0.0
        # Gap magnitude (capped at 20%)
        score += min(gap_pct / 20.0, 1.0) * 0.3
        # Pre-market RVOL
        score += min(rvol / 10.0, 1.0) * 0.3
        # EPS catalyst
        score += 0.25 if eps else 0.0
        # Above EMA21
        score += 0.15 if above_ema else 0.0
        return round(score, 3)

    def _above_ema(self, df: pd.DataFrame, price: float) -> bool:
        cfg = self.config
        if len(df) < cfg.ema_span:
            return True  # insufficient data, assume pass
        ema = df["close"].ewm(span=cfg.ema_span, adjust=False,
                               min_periods=cfg.ema_span).mean().iloc[-1]
        return price > ema

    def scan(
        self,
        symbols: list[str],
        daily_ohlcv_map: dict[str, pd.DataFrame],
        premarket_prices: dict[str, float],
        premarket_volumes: Optional[dict[str, float]] = None,
        eps_catalyst_set: Optional[set[str]] = None,
    ) -> list[GapCandidate]:
        """
        Parameters
        ----------
        symbols : list[str]
        daily_ohlcv_map : dict  {symbol: daily_df}
        premarket_prices : dict {symbol: premarket_last_price}
        premarket_volumes : dict {symbol: premarket_volume_so_far}, optional
        eps_catalyst_set : set of symbols with earnings today, optional

        Returns
        -------
        list[GapCandidate] sorted by score desc.
        """
        cfg  = self.config
        pvol = premarket_volumes or {}
        eps  = eps_catalyst_set or set()
        results: list[GapCandidate] = []

        for sym in symbols:
            pm_price = premarket_prices.get(sym)
            if pm_price is None:
                continue

            df = daily_ohlcv_map.get(sym)
            if df is None or len(df) < 21:
                continue

            prev_close = float(df["close"].iloc[-1])
            gap_pct    = (pm_price - prev_close) / prev_close * 100

            if not (cfg.min_gap_pct <= gap_pct <= cfg.max_gap_pct):
                continue

            # ADR filter
            adr = ((df["high"] - df["low"]) / df["low"] * 100).tail(20).mean()
            if adr < cfg.min_adr_pct:
                continue

            # RVOL
            avg_vol = float(df["volume"].tail(20).mean())
            pm_vol  = pvol.get(sym, avg_vol * 0.5)  # fallback estimate
            rvol    = pm_vol / avg_vol if avg_vol > 0 else 0.0
            if rvol < cfg.min_rvol_premarket:
                continue

            # EMA21 check
            above_ema = self._above_ema(df, pm_price)
            if cfg.require_above_ema21 and not above_ema:
                continue

            has_eps = sym in eps
            score   = self._score(gap_pct, rvol, has_eps, above_ema)

            results.append(GapCandidate(
                symbol=sym,
                prev_close=prev_close,
                premarket_price=float(pm_price),
                gap_pct=round(gap_pct, 2),
                premarket_rvol=round(rvol, 2),
                has_eps_catalyst=has_eps,
                above_ema21=above_ema,
                score=score,
                notes=(
                    f"Gap {gap_pct:.1f}% | RVOL {rvol:.1f}x"
                    + (" | EPS catalyst" if has_eps else "")
                ),
            ))

        return sorted(results, key=lambda c: c.score, reverse=True)
