"""eps_setup.py

EPS Episodic Pivot Setup — Martin Luk 第四種 Setup

影片 1:14:53 核心邏輯:
  EPS Episodic Pivot = 有業績催化劑的 gap-up + 首次回踩 EMA9 入場

  Martin 原話: "This is the most powerful setup because the stock is
  re-rated by institutional investors overnight. The gap IS the setup."

三步判斷:
  1. Catalyst 確認 : 有 EPS 超預期 / 業績好 / 重大消息
  2. Gap 品質     : gap > 5%, RVOL > 3x, 收在日內高位
  3. 入場時機     : gap 後第一次 5分鐘 EMA9 回踩買入
                    (不追 gap 當下，等回踩)

止損邏輯:
  - 主要止損: gap-up 日的日內低點 (絕對不能填掉 gap)
  - 緊止損  : 回踩 bar 的低點 (5分鐘)
  - Gap 填掉 = setup 失效，即止損
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class EpsSetupConfig:
    # Gap quality
    min_gap_pct: float = 5.0            # minimum gap-up from prev close
    max_gap_pct: float = 60.0           # avoid outlier gap
    min_rvol_gap_day: float = 3.0       # RVOL on gap day (institutional buying)
    min_close_strength: float = 0.65    # close in top 35% of day range

    # Pullback entry
    max_pullback_pct: float = 5.0       # max pullback from gap high before entry
    require_ema9_hold: bool = True      # must hold above EMA9 on pullback
    ema_span: int = 9

    # Gap fill rule
    gap_fill_invalidates: bool = True   # gap fill = stop out immediately
    gap_fill_buffer_pct: float = 0.5    # how close to gap can price come

    # Confirmation
    min_days_since_gap: int = 0         # 0 = same day entry allowed
    max_days_since_gap: int = 10        # stale after 10 days


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass
class EpsSignal:
    symbol: str
    gap_date: str
    gap_pct: float
    rvol_gap_day: float
    close_strength_gap_day: float
    has_eps_catalyst: bool
    current_price: float
    entry_price: float
    stop_price: float               # gap day low
    tight_stop_price: float         # pullback bar low
    target_1r: float
    target_3r: float
    ema9_level: float
    days_since_gap: int
    gap_intact: bool                # True if price > gap day open
    setup_stage: str                # "fresh" | "pullback" | "consolidating"
    score: float
    notes: str

    def to_dict(self) -> dict:
        return {
            "symbol":              self.symbol,
            "gap_date":           self.gap_date,
            "gap_pct":            round(self.gap_pct, 2),
            "rvol_gap_day":       round(self.rvol_gap_day, 2),
            "close_strength":     round(self.close_strength_gap_day, 2),
            "has_eps_catalyst":   self.has_eps_catalyst,
            "current_price":      round(self.current_price, 2),
            "entry_price":        round(self.entry_price, 2),
            "stop_price":         round(self.stop_price, 2),
            "tight_stop":         round(self.tight_stop_price, 2),
            "target_1r":          round(self.target_1r, 2),
            "target_3r":          round(self.target_3r, 2),
            "ema9":               round(self.ema9_level, 2),
            "days_since_gap":     self.days_since_gap,
            "gap_intact":         self.gap_intact,
            "setup_stage":        self.setup_stage,
            "score":              round(self.score, 3),
            "notes":              self.notes,
        }


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class EpsSetupDetector:
    """
    Scans daily OHLCV for EPS Episodic Pivot setups.

    Usage:
        detector = EpsSetupDetector()
        signals = detector.scan(
            symbols=symbols,
            ohlcv_map=ohlcv_map,
            eps_catalyst_set={"NVDA", "SMCI"},
        )
    """

    def __init__(self, config: Optional[EpsSetupConfig] = None) -> None:
        self.config = config or EpsSetupConfig()

    def _find_gap_bar_idx(self, df: pd.DataFrame) -> Optional[int]:
        """Scan last 10 bars for a qualifying gap-up day."""
        cfg = self.config
        for i in range(len(df) - 1, max(len(df) - 11, 0), -1):
            if i == 0:
                break
            close_prev = float(df["close"].iloc[i - 1])
            open_curr  = float(df["open"].iloc[i])
            gap_pct    = (open_curr - close_prev) / close_prev * 100
            if cfg.min_gap_pct <= gap_pct <= cfg.max_gap_pct:
                return i
        return None

    def _compute_ema9(self, df: pd.DataFrame) -> pd.Series:
        cfg = self.config
        return df["close"].ewm(
            span=cfg.ema_span, adjust=False, min_periods=cfg.ema_span
        ).mean()

    def _score(
        self,
        gap_pct: float,
        rvol: float,
        cs: float,
        eps: bool,
        days: int,
    ) -> float:
        score = 0.0
        score += min(gap_pct / 30.0, 1.0) * 0.25   # gap size
        score += min(rvol / 10.0, 1.0)  * 0.25   # institutional volume
        score += cs * 0.20                          # close strength
        score += 0.20 if eps else 0.0               # EPS catalyst
        score += max(0.10 - days * 0.01, 0.0)       # recency bonus
        return round(score, 3)

    def scan(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        eps_catalyst_set: Optional[set[str]] = None,
    ) -> list[EpsSignal]:
        cfg = self.config
        eps = eps_catalyst_set or set()
        results: list[EpsSignal] = []

        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None or len(df) < 15:
                continue

            gap_idx = self._find_gap_bar_idx(df)
            if gap_idx is None:
                continue

            days_since = len(df) - 1 - gap_idx
            if days_since > cfg.max_days_since_gap:
                continue

            gap_bar   = df.iloc[gap_idx]
            prev_bar  = df.iloc[gap_idx - 1]
            last_bar  = df.iloc[-1]

            close_prev = float(prev_bar["close"])
            gap_pct    = (float(gap_bar["open"]) - close_prev) / close_prev * 100

            # RVOL on gap day
            avg_vol  = float(df["volume"].iloc[max(0, gap_idx - 21): gap_idx].mean())
            rvol     = float(gap_bar["volume"]) / avg_vol if avg_vol > 0 else 0.0
            if rvol < cfg.min_rvol_gap_day:
                continue

            # Close strength on gap day
            day_rng  = float(gap_bar["high"]) - float(gap_bar["low"])
            cs       = (float(gap_bar["close"]) - float(gap_bar["low"])) / day_rng \
                if day_rng > 0 else 0.5
            if cs < cfg.min_close_strength:
                continue

            # Gap integrity: current price above gap day low
            gap_day_low   = float(gap_bar["low"])
            current_price = float(last_bar["close"])
            gap_intact    = current_price > gap_day_low

            if cfg.gap_fill_invalidates and not gap_intact:
                continue

            # EMA9
            ema9 = self._compute_ema9(df)
            ema9_current = float(ema9.iloc[-1])

            # Entry: current price if near EMA9, else EMA9 itself
            pullback_from_high = (float(df["high"].iloc[gap_idx:].max()) - current_price)
            pullback_pct       = pullback_from_high / current_price * 100

            if current_price >= ema9_current and pullback_pct <= cfg.max_pullback_pct:
                entry       = current_price
                setup_stage = "pullback" if pullback_pct > 1.0 else "fresh"
            elif current_price < ema9_current:
                continue   # below EMA9 = setup extended
            else:
                entry       = current_price
                setup_stage = "consolidating"

            stop          = gap_day_low
            tight_stop    = float(df["low"].iloc[-1])
            risk_per_share = entry - stop
            if risk_per_share <= 0:
                continue

            has_eps = sym.upper() in eps
            score   = self._score(gap_pct, rvol, cs, has_eps, days_since)

            results.append(EpsSignal(
                symbol=sym.upper(),
                gap_date=str(df.index[gap_idx].date()) if hasattr(df.index[gap_idx], 'date') else str(gap_idx),
                gap_pct=round(gap_pct, 2),
                rvol_gap_day=round(rvol, 2),
                close_strength_gap_day=round(cs, 3),
                has_eps_catalyst=has_eps,
                current_price=round(current_price, 2),
                entry_price=round(entry, 2),
                stop_price=round(stop, 2),
                tight_stop_price=round(tight_stop, 2),
                target_1r=round(entry + risk_per_share, 2),
                target_3r=round(entry + risk_per_share * 3, 2),
                ema9_level=round(ema9_current, 2),
                days_since_gap=days_since,
                gap_intact=gap_intact,
                setup_stage=setup_stage,
                score=score,
                notes=(
                    f"Gap {gap_pct:.1f}% | RVOL {rvol:.1f}x | "
                    f"Stage: {setup_stage} | "
                    + ("EPS catalyst | " if has_eps else "")
                    + f"Gap intact: {gap_intact}"
                ),
            ))

        return sorted(results, key=lambda s: s.score, reverse=True)
