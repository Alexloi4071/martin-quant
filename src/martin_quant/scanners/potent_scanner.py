"""potent_scanner.py

Potent Scanner — Martin Luk Scanner #2

用途: 找出前一日最強股票 (領务股)，追蹤板塊輪動 + 主題動能

條件:
  1. 前一日漲跌幅 >= min_gain_pct  (e.g. 5%)
  2. 前一日成交量是 20日均的 rvol_threshold 倍以上
  3. 收盤在日內高位 70% 以上 (close_strength >= 0.7)
  4. 价格在 EMA9/21 上方
  5. 同板塊至少 2 支相同表現 (板塊確認)

輸出 PotentCandidate 列表，按 sector_momentum 排序
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class PotentScanConfig:
    min_gain_pct: float = 5.0          # prev day gain
    min_rvol: float = 1.5              # relative volume
    min_close_strength: float = 0.65   # close in upper 65% of day range
    require_above_ema9: bool = True
    ema_span_fast: int = 9
    ema_span_mid: int = 21
    sector_min_count: int = 2          # sector confirmed if N+ stocks qualify


@dataclass
class PotentCandidate:
    symbol: str
    sector: str
    theme: str
    gain_pct: float
    rvol: float
    close_strength: float
    above_ema9: bool
    above_ema21: bool
    sector_momentum_count: int   # how many in same sector qualified
    score: float
    notes: str

    def to_dict(self) -> dict:
        return {
            "symbol":         self.symbol,
            "sector":         self.sector,
            "theme":          self.theme,
            "gain_pct":       round(self.gain_pct, 2),
            "rvol":           round(self.rvol, 2),
            "close_strength": round(self.close_strength, 3),
            "above_ema9":     self.above_ema9,
            "above_ema21":    self.above_ema21,
            "sector_count":   self.sector_momentum_count,
            "score":          round(self.score, 3),
            "notes":          self.notes,
        }


class PotentScanner:
    """
    Identifies yesterday's strongest stocks for sector rotation tracking.

    Usage:
        scanner = PotentScanner()
        candidates = scanner.scan(
            symbols=symbols,
            ohlcv_map=ohlcv_map,
            metadata=meta,  # {symbol: {sector, theme}}
        )
    """

    def __init__(self, config: Optional[PotentScanConfig] = None) -> None:
        self.config = config or PotentScanConfig()

    def scan(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        metadata: Optional[dict[str, dict]] = None,
    ) -> list[PotentCandidate]:
        cfg  = self.config
        meta = metadata or {}

        raw: list[dict] = []

        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None or len(df) < 22:
                continue

            last  = df.iloc[-1]
            prev  = df.iloc[-2]

            # Prev day gain
            gain_pct = (last["close"] - prev["close"]) / prev["close"] * 100
            if gain_pct < cfg.min_gain_pct:
                continue

            # Relative volume
            avg_vol = float(df["volume"].iloc[-21:-1].mean())
            rvol    = float(last["volume"]) / avg_vol if avg_vol > 0 else 0.0
            if rvol < cfg.min_rvol:
                continue

            # Close strength
            day_rng = last["high"] - last["low"]
            cs = (last["close"] - last["low"]) / day_rng if day_rng > 0 else 0.5
            if cs < cfg.min_close_strength:
                continue

            # EMA checks
            ema9  = df["close"].ewm(span=cfg.ema_span_fast, adjust=False,
                                     min_periods=cfg.ema_span_fast).mean().iloc[-1]
            ema21 = df["close"].ewm(span=cfg.ema_span_mid, adjust=False,
                                     min_periods=cfg.ema_span_mid).mean().iloc[-1]
            above_ema9  = last["close"] > ema9
            above_ema21 = last["close"] > ema21

            if cfg.require_above_ema9 and not above_ema9:
                continue

            sym_meta = meta.get(sym.upper(), {})
            raw.append({
                "symbol":      sym,
                "sector":      sym_meta.get("sector", "Unknown"),
                "theme":       sym_meta.get("theme", ""),
                "gain_pct":    gain_pct,
                "rvol":        rvol,
                "close_strength": cs,
                "above_ema9":  above_ema9,
                "above_ema21": above_ema21,
            })

        # Count sector momentum
        sector_counts: dict[str, int] = defaultdict(int)
        for r in raw:
            sector_counts[r["sector"]] += 1

        results: list[PotentCandidate] = []
        for r in raw:
            sector_cnt = sector_counts[r["sector"]]
            score = (
                min(r["gain_pct"] / 20.0, 1.0) * 0.25
                + min(r["rvol"] / 5.0, 1.0) * 0.25
                + r["close_strength"] * 0.20
                + (0.20 if sector_cnt >= cfg.sector_min_count else 0.0)
                + (0.10 if r["above_ema9"] else 0.0)
            )
            results.append(PotentCandidate(
                symbol=r["symbol"],
                sector=r["sector"],
                theme=r["theme"],
                gain_pct=r["gain_pct"],
                rvol=r["rvol"],
                close_strength=r["close_strength"],
                above_ema9=r["above_ema9"],
                above_ema21=r["above_ema21"],
                sector_momentum_count=sector_cnt,
                score=round(score, 3),
                notes=(
                    f"+{r['gain_pct']:.1f}% | RVOL {r['rvol']:.1f}x | "
                    f"{sector_cnt} in sector"
                ),
            ))

        return sorted(results, key=lambda c: c.score, reverse=True)
