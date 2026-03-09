"""leader_scanner.py

Leader Scanner — Martin Luk Scanner #3

用途: 週線 RS 排名，建立市場領务股名單 (結合 Pillar + Leading)

輸出:
  - Weekly RS rank vs SPY (進行週線較寷，比日線更穩定)
  - Market health indicator: Leading stocks advancing% vs declining%
  - Theme momentum: 幾個主題哪個最強
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class LeaderScanConfig:
    rs_lookback_weeks: int = 26      # 6-month weekly RS
    min_weekly_rs_pct: float = 75.0  # top 25% only = Leaders
    min_dollar_volume: float = 20_000_000.0   # $20M/day dollar vol
    require_ema_bull_stack: bool = True        # EMA9 > EMA21 > EMA50 on weekly
    min_weeks_history: int = 30


@dataclass
class LeaderEntry:
    symbol: str
    sector: str
    theme: str
    weekly_rs_pct: float       # percentile rank (0-100)
    weekly_rs_raw: float       # raw 26-week return differential vs SPY
    above_ema21_weekly: bool
    dollar_volume: float
    score: float
    market_health_contribution: str   # "advancing" | "declining" | "neutral"

    def to_dict(self) -> dict:
        return {
            "symbol":           self.symbol,
            "sector":           self.sector,
            "theme":            self.theme,
            "weekly_rs_pct":    round(self.weekly_rs_pct, 1),
            "weekly_rs_raw":    round(self.weekly_rs_raw, 2),
            "above_ema21_weekly": self.above_ema21_weekly,
            "dollar_volume":    round(self.dollar_volume, 0),
            "score":            round(self.score, 3),
            "mkt_health":       self.market_health_contribution,
        }


@dataclass
class MarketHealthSnapshot:
    total_leaders: int
    advancing: int      # leaders that rose this week
    declining: int      # leaders that fell this week
    advance_decline_ratio: float
    health_state: str   # "healthy" | "mixed" | "deteriorating"
    top_themes: list[tuple[str, int]]  # [(theme_name, count), ...]

    def to_dict(self) -> dict:
        return {
            "total_leaders":    self.total_leaders,
            "advancing":        self.advancing,
            "declining":        self.declining,
            "ad_ratio":         round(self.advance_decline_ratio, 2),
            "health_state":     self.health_state,
            "top_themes":       self.top_themes[:5],
        }


class LeaderScanner:
    """
    Identifies market-leading stocks using weekly RS ranking.
    Also computes a market health snapshot from the leaders list.

    Usage:
        scanner = LeaderScanner()
        leaders, health = scanner.scan(
            symbols=symbols,
            ohlcv_map=ohlcv_map,
            spy_df=spy_df,
            metadata=meta,
        )
    """

    def __init__(self, config: Optional[LeaderScanConfig] = None) -> None:
        self.config = config or LeaderScanConfig()

    def _resample_weekly(self, df: pd.DataFrame) -> pd.DataFrame:
        ts = pd.to_datetime(df.get("timestamp", df.index))
        d  = df.set_index(ts).sort_index()
        w  = d.resample("W-FRI").agg(
            {"open": "first", "high": "max",
             "low": "min", "close": "last", "volume": "sum"}
        ).dropna(subset=["close"])
        return w

    def _weekly_rs(self, weekly: pd.DataFrame, spy_weekly: pd.DataFrame, lookback: int) -> float:
        sc = weekly["close"]
        bc = spy_weekly["close"]
        shared = sc.index.intersection(bc.index)
        if len(shared) < lookback:
            return 0.0
        sc_ret = sc.loc[shared].pct_change(lookback).iloc[-1]
        bc_ret = bc.loc[shared].pct_change(lookback).iloc[-1]
        if pd.isna(sc_ret) or pd.isna(bc_ret):
            return 0.0
        return float((sc_ret - bc_ret) * 100)

    def _above_weekly_ema21(self, weekly: pd.DataFrame) -> bool:
        if len(weekly) < 21:
            return False
        ema21 = weekly["close"].ewm(span=21, adjust=False, min_periods=21).mean().iloc[-1]
        return bool(weekly["close"].iloc[-1] > ema21)

    def _weekly_direction(self, weekly: pd.DataFrame) -> str:
        if len(weekly) < 2:
            return "neutral"
        chg = weekly["close"].iloc[-1] - weekly["close"].iloc[-2]
        if chg > 0:
            return "advancing"
        elif chg < 0:
            return "declining"
        return "neutral"

    def scan(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        spy_df: Optional[pd.DataFrame] = None,
        metadata: Optional[dict[str, dict]] = None,
    ) -> tuple[list[LeaderEntry], MarketHealthSnapshot]:
        cfg  = self.config
        meta = metadata or {}

        spy_weekly = self._resample_weekly(spy_df) if spy_df is not None else None

        raw_rs: dict[str, float] = {}
        info:   dict[str, dict]  = {}

        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None or len(df) < cfg.min_weeks_history * 5:
                continue

            weekly = self._resample_weekly(df)
            if len(weekly) < cfg.min_weeks_history:
                continue

            # Dollar volume filter
            dvol = float((df["close"] * df["volume"]).tail(20).mean())
            if dvol < cfg.min_dollar_volume:
                continue

            rs_raw = self._weekly_rs(weekly, spy_weekly, cfg.rs_lookback_weeks) \
                if spy_weekly is not None else 0.0

            above_w21 = self._above_weekly_ema21(weekly)
            if cfg.require_ema_bull_stack and not above_w21:
                continue

            raw_rs[sym] = rs_raw
            info[sym] = {
                "dvol":      dvol,
                "above_w21": above_w21,
                "direction": self._weekly_direction(weekly),
                "sector":    meta.get(sym.upper(), {}).get("sector", "Unknown"),
                "theme":     meta.get(sym.upper(), {}).get("theme", ""),
            }

        # RS percentile ranking
        if not raw_rs:
            empty_health = MarketHealthSnapshot(
                total_leaders=0, advancing=0, declining=0,
                advance_decline_ratio=0.0, health_state="mixed", top_themes=[]
            )
            return [], empty_health

        rs_arr   = np.array(list(raw_rs.values()))
        rs_ranks = {sym: float(np.mean(rs_arr <= v) * 100)
                    for sym, v in raw_rs.items()}

        # Build entries
        entries: list[LeaderEntry] = []
        for sym, pct in rs_ranks.items():
            if pct < cfg.min_weekly_rs_pct:
                continue
            d = info[sym]
            score = pct / 100.0 * 0.6 + (0.2 if d["above_w21"] else 0.0) + 0.2
            entries.append(LeaderEntry(
                symbol=sym,
                sector=d["sector"],
                theme=d["theme"],
                weekly_rs_pct=round(pct, 1),
                weekly_rs_raw=round(raw_rs[sym], 2),
                above_ema21_weekly=d["above_w21"],
                dollar_volume=d["dvol"],
                score=round(score, 3),
                market_health_contribution=d["direction"],
            ))

        entries.sort(key=lambda e: e.score, reverse=True)

        # Market health snapshot
        advancing = sum(1 for e in entries if e.market_health_contribution == "advancing")
        declining = sum(1 for e in entries if e.market_health_contribution == "declining")
        total     = len(entries)
        adr_ratio = advancing / declining if declining > 0 else float(advancing)

        if adr_ratio > 2.0:
            health = "healthy"
        elif adr_ratio > 0.8:
            health = "mixed"
        else:
            health = "deteriorating"

        theme_counts: dict[str, int] = defaultdict(int)
        for e in entries:
            if e.theme:
                theme_counts[e.theme] += 1
        top_themes = sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        health_snap = MarketHealthSnapshot(
            total_leaders=total,
            advancing=advancing,
            declining=declining,
            advance_decline_ratio=round(adr_ratio, 2),
            health_state=health,
            top_themes=top_themes,
        )
        return entries, health_snap
