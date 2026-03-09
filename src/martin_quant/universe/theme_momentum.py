"""theme_momentum.py

板塊/主題動能排名 — Martin Luk 影片 3:07:04

Martin 的板塊輪動邏輯:
  - 識別強勢主題 (Quantum, AI, Crypto, Semis, etc.)
  - 只買主題動能排名前幾位的個股
  - 如果主題動能消退 = 出場信號

主題 momentum 計算:
  1. Average RS 1yr   of all stocks in theme
  2. % above EMA21    of all stocks in theme (breadth)
  3. Average 5d gain  (recent momentum)
  4. Volume surge     (institutional interest)
  5. Leader count     (how many qualify as Leading category)

輸出:
  ThemeMomentumRanking — 按 composite score 排名
  可直接傳入 WatchlistBuilder 用於主題過濾
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class ThemeMomentumConfig:
    min_stocks_per_theme: int = 2         # need at least 2 stocks in theme
    rs_lookback_days: int = 252           # 1-year RS lookback
    ema_span: int = 21                    # EMA21 for breadth calculation
    recent_gain_days: int = 5             # 5-day momentum
    rvol_lookback: int = 20               # RVOL lookback
    leading_rs_pct_threshold: float = 75.0  # RS > 75% = Leader


# Default known themes
DEFAULT_THEMES: dict[str, list[str]] = {
    "AI":          ["NVDA", "AMD", "SMCI", "ARM", "AVGO", "PLTR", "SOUN", "BBAI"],
    "Quantum":     ["IONQ", "RGTI", "QBTS", "QUBT", "IBM"],
    "Crypto":      ["COIN", "MSTR", "RIOT", "MARA", "HUT", "CLSK"],
    "Semis":       ["NVDA", "AMD", "AVGO", "QCOM", "MRVL", "AMAT", "LRCX", "KLAC"],
    "Defense":     ["LMT", "RTX", "NOC", "GD", "HII", "TDG"],
    "Biotech":     ["MRNA", "BNTX", "CRSP", "EDIT", "BEAM", "NTLA"],
    "FinTech":     ["SQ", "AFRM", "SOFI", "UPST", "LC", "DAVE"],
    "EV":          ["TSLA", "RIVN", "LCID", "NIO", "LI", "XPEV"],
    "Cloud":       ["SNOW", "DDOG", "CRWD", "NET", "ZS", "OKTA", "MDB"],
    "Energy":      ["XOM", "CVX", "OXY", "SLB", "HAL", "MPC"],
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ThemeStats:
    theme: str
    stock_count: int
    avg_rs_pct: float           # avg RS percentile across stocks in theme
    pct_above_ema21: float      # breadth: % of stocks above EMA21
    avg_5d_gain_pct: float      # average 5-day return
    avg_rvol: float             # average relative volume
    leader_count: int           # stocks with RS > leading threshold
    composite_score: float      # overall theme momentum (0-1)
    momentum_state: str         # "hot" | "cooling" | "cold"
    top_stocks: list[str]       # top 3 stocks by RS in this theme

    def to_dict(self) -> dict:
        return {
            "theme":           self.theme,
            "stock_count":     self.stock_count,
            "avg_rs_pct":      round(self.avg_rs_pct, 1),
            "pct_above_ema21": round(self.pct_above_ema21, 1),
            "avg_5d_gain":     round(self.avg_5d_gain_pct, 2),
            "avg_rvol":        round(self.avg_rvol, 2),
            "leader_count":    self.leader_count,
            "score":           round(self.composite_score, 3),
            "state":           self.momentum_state,
            "top_stocks":      self.top_stocks,
        }


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------

class ThemeMomentumCalculator:
    """
    Ranks themes by composite momentum score.

    Usage:
        calc = ThemeMomentumCalculator()
        rankings = calc.rank(
            ohlcv_map=ohlcv_map,
            spy_df=spy_df,
            theme_map=DEFAULT_THEMES,    # or custom
        )
        # rankings[0] = hottest theme
        top_themes = [t.theme for t in rankings[:3]]
    """

    def __init__(self, config: Optional[ThemeMomentumConfig] = None) -> None:
        self.config = config or ThemeMomentumConfig()

    def _rs_1yr(self, df: pd.DataFrame, spy_df: Optional[pd.DataFrame]) -> float:
        cfg = self.config
        lookback = min(cfg.rs_lookback_days, len(df) - 1)
        stock_ret = (df["close"].iloc[-1] / df["close"].iloc[-lookback] - 1) * 100
        if spy_df is not None and len(spy_df) >= lookback:
            spy_ret = (spy_df["close"].iloc[-1] / spy_df["close"].iloc[-lookback] - 1) * 100
            return float(stock_ret - spy_ret)
        return float(stock_ret)

    def _above_ema21(self, df: pd.DataFrame) -> bool:
        cfg = self.config
        if len(df) < cfg.ema_span:
            return False
        ema21 = df["close"].ewm(
            span=cfg.ema_span, adjust=False, min_periods=cfg.ema_span
        ).mean().iloc[-1]
        return bool(df["close"].iloc[-1] > ema21)

    def _gain_5d(self, df: pd.DataFrame) -> float:
        cfg = self.config
        lookback = min(cfg.recent_gain_days, len(df) - 1)
        return float(
            (df["close"].iloc[-1] / df["close"].iloc[-lookback] - 1) * 100
        )

    def _rvol(self, df: pd.DataFrame) -> float:
        cfg = self.config
        if len(df) < cfg.rvol_lookback + 1:
            return 1.0
        avg_vol = float(df["volume"].iloc[-cfg.rvol_lookback - 1: -1].mean())
        last_vol = float(df["volume"].iloc[-1])
        return last_vol / avg_vol if avg_vol > 0 else 1.0

    def rank(
        self,
        ohlcv_map: dict[str, pd.DataFrame],
        spy_df: Optional[pd.DataFrame] = None,
        theme_map: Optional[dict[str, list[str]]] = None,
    ) -> list[ThemeStats]:
        cfg     = self.config
        themes  = theme_map or DEFAULT_THEMES

        # Compute per-stock metrics
        all_rs: dict[str, float] = {}
        for sym, df in ohlcv_map.items():
            if df is not None and len(df) >= 20:
                all_rs[sym.upper()] = self._rs_1yr(df, spy_df)

        # RS percentile across all available stocks
        if all_rs:
            rs_vals = np.array(list(all_rs.values()))
            rs_pct_map = {
                sym: float(np.mean(rs_vals <= v) * 100)
                for sym, v in all_rs.items()
            }
        else:
            rs_pct_map = {}

        results: list[ThemeStats] = []
        for theme_name, theme_syms in themes.items():
            available = [
                sym.upper() for sym in theme_syms
                if sym.upper() in ohlcv_map and ohlcv_map[sym.upper()] is not None
            ]
            if len(available) < cfg.min_stocks_per_theme:
                continue

            rs_pcts  = [rs_pct_map.get(s, 50.0) for s in available]
            ema_above = [
                self._above_ema21(ohlcv_map[s])
                for s in available if len(ohlcv_map[s]) >= cfg.ema_span
            ]
            gains_5d = [
                self._gain_5d(ohlcv_map[s]) for s in available
            ]
            rvols = [
                self._rvol(ohlcv_map[s]) for s in available
            ]

            avg_rs       = float(np.mean(rs_pcts))
            breadth      = float(np.mean(ema_above) * 100) if ema_above else 0.0
            avg_gain     = float(np.mean(gains_5d))
            avg_rvol     = float(np.mean(rvols))
            leader_count = sum(1 for r in rs_pcts if r >= cfg.leading_rs_pct_threshold)

            # Composite score
            score = (
                (avg_rs / 100.0) * 0.35
                + (breadth / 100.0) * 0.25
                + min(max(avg_gain, 0) / 20.0, 1.0) * 0.20
                + min(avg_rvol / 3.0, 1.0) * 0.10
                + (leader_count / max(len(available), 1)) * 0.10
            )

            # State classification
            if score >= 0.65:
                state = "hot"
            elif score >= 0.40:
                state = "cooling"
            else:
                state = "cold"

            # Top 3 stocks in this theme
            scored_stocks = sorted(
                available, key=lambda s: rs_pct_map.get(s, 0), reverse=True
            )[:3]

            results.append(ThemeStats(
                theme=theme_name,
                stock_count=len(available),
                avg_rs_pct=round(avg_rs, 1),
                pct_above_ema21=round(breadth, 1),
                avg_5d_gain_pct=round(avg_gain, 2),
                avg_rvol=round(avg_rvol, 2),
                leader_count=leader_count,
                composite_score=round(score, 3),
                momentum_state=state,
                top_stocks=scored_stocks,
            ))

        return sorted(results, key=lambda t: t.composite_score, reverse=True)
