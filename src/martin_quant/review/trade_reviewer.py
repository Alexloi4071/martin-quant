"""trade_reviewer.py

Per-Trade Reviewer
==================
Martin Luk 策略 — 單筆交易後規分析：
  - 進場驗證 (Setup 是否符合規則)
  - 出場失誤分析 (Too early / Too late?)
  - R 倍數評估
  - 建議改進方向

Usage:
    from martin_quant.review.trade_reviewer import TradeReviewer
    reviewer = TradeReviewer()
    review = reviewer.review_trade(
        symbol="NVDA",
        entry_date="2026-03-01",
        exit_date="2026-03-10",
        entry_price=125.0,
        stop_price=121.0,
        exit_price=136.5,
        setup_type="eps",
        df=nvda_df,
    )
    print(review.verdict)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import logging

import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class TradeReview:
    """Per-trade 後規分析結果"""
    symbol: str
    setup_type: str
    entry_price: float
    exit_price: float
    r_achieved: float
    r_max_available: float       # 最高點 / 入場 的最大可能 R
    r_left_on_table: float       # r_max - r_achieved
    exit_timing: str             # "optimal" | "early" | "late"
    entry_quality: str           # "A" | "B" | "C"
    verdict: str                 # 文字摘要
    lessons: list[str] = field(default_factory=list)
    score: float = 0.0           # 0-10


class TradeReviewer:
    """
    單筆交易後規分析工具。
    """

    def review_trade(
        self,
        symbol: str,
        entry_date: str,
        exit_date: str,
        entry_price: float,
        stop_price: float,
        exit_price: float,
        setup_type: str,
        df: Optional[pd.DataFrame] = None,
        direction: str = "long",
    ) -> TradeReview:
        """
        對建筆交易進行後規分析。
        """
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            return TradeReview(
                symbol=symbol, setup_type=setup_type,
                entry_price=entry_price, exit_price=exit_price,
                r_achieved=0.0, r_max_available=0.0, r_left_on_table=0.0,
                exit_timing="unknown", entry_quality="C",
                verdict="Cannot review: zero risk per share",
            )

        # R achieved
        if direction == "long":
            r_achieved = (exit_price - entry_price) / risk_per_share
        else:
            r_achieved = (entry_price - exit_price) / risk_per_share

        # Max R available (from df)
        r_max = self._calc_max_r(df, entry_date, exit_date, entry_price, stop_price, direction)

        r_left = max(0.0, r_max - r_achieved)

        # Exit timing
        if r_achieved >= r_max * 0.85:
            exit_timing = "optimal"
        elif r_achieved >= r_max * 0.5:
            exit_timing = "early"
        else:
            exit_timing = "late" if r_achieved < 0 else "early"

        # Entry quality
        entry_quality = self._grade_entry(df, entry_date, entry_price, stop_price, setup_type)

        # Lessons
        lessons = self._generate_lessons(
            r_achieved, r_max, r_left, exit_timing, entry_quality, setup_type
        )

        # Score
        score = self._compute_score(r_achieved, r_max, entry_quality)

        verdict = (
            f"{symbol} {setup_type}: R={r_achieved:+.2f} "
            f"(max={r_max:.2f}, left={r_left:.2f}) "
            f"entry={entry_quality} exit={exit_timing}"
        )

        return TradeReview(
            symbol=symbol, setup_type=setup_type,
            entry_price=entry_price, exit_price=exit_price,
            r_achieved=round(r_achieved, 2),
            r_max_available=round(r_max, 2),
            r_left_on_table=round(r_left, 2),
            exit_timing=exit_timing,
            entry_quality=entry_quality,
            verdict=verdict,
            lessons=lessons,
            score=round(score, 1),
        )

    def review_batch(
        self,
        trades: list[dict],
        ohlcv_map: Optional[dict[str, pd.DataFrame]] = None,
    ) -> list[TradeReview]:
        """Batch review multiple trades"""
        reviews = []
        for t in trades:
            sym = t.get("symbol", "")
            df  = (ohlcv_map or {}).get(sym)
            rev = self.review_trade(
                symbol=sym,
                entry_date=t.get("entry_date", ""),
                exit_date=t.get("exit_date", ""),
                entry_price=float(t.get("entry_price", 0)),
                stop_price=float(t.get("stop_price", 0)),
                exit_price=float(t.get("exit_price", 0)),
                setup_type=t.get("setup_type", ""),
                df=df,
                direction=t.get("direction", "long"),
            )
            reviews.append(rev)
        return reviews

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_max_r(
        df: Optional[pd.DataFrame],
        entry_date: str,
        exit_date: str,
        entry_price: float,
        stop_price: float,
        direction: str,
    ) -> float:
        """Calculate max R achievable during holding period"""
        if df is None:
            return 0.0
        risk = abs(entry_price - stop_price)
        if risk <= 0:
            return 0.0
        try:
            df_c = df.copy()
            df_c.columns = [c.lower() for c in df_c.columns]
            if not isinstance(df_c.index, pd.DatetimeIndex):
                df_c.index = pd.to_datetime(df_c.index)

            mask = (df_c.index >= pd.Timestamp(entry_date)) & (
                df_c.index <= pd.Timestamp(exit_date)
            )
            held = df_c[mask]
            if held.empty:
                return 0.0

            if direction == "long":
                best = float(held["high"].max())
                return max(0.0, (best - entry_price) / risk)
            else:
                best = float(held["low"].min())
                return max(0.0, (entry_price - best) / risk)
        except Exception:
            return 0.0

    @staticmethod
    def _grade_entry(
        df: Optional[pd.DataFrame],
        entry_date: str,
        entry_price: float,
        stop_price: float,
        setup_type: str,
    ) -> str:
        """Grade entry quality A/B/C based on context"""
        if df is None:
            return "B"
        try:
            df_c = df.copy()
            df_c.columns = [c.lower() for c in df_c.columns]
            if not isinstance(df_c.index, pd.DatetimeIndex):
                df_c.index = pd.to_datetime(df_c.index)

            before = df_c[df_c.index < pd.Timestamp(entry_date)].tail(20)
            if before.empty:
                return "B"

            ema9  = before["close"].ewm(span=9, adjust=False).mean()
            ema21 = before["close"].ewm(span=21, adjust=False).mean()

            # A: entered right at EMA9/21 in uptrend
            last_ema9  = float(ema9.iloc[-1])
            last_ema21 = float(ema21.iloc[-1])
            near_ema   = abs(entry_price - last_ema9) / last_ema9 < 0.02
            in_uptrend = last_ema9 > last_ema21

            if near_ema and in_uptrend:
                return "A"
            elif in_uptrend:
                return "B"
            else:
                return "C"
        except Exception:
            return "B"

    @staticmethod
    def _generate_lessons(
        r_achieved: float,
        r_max: float,
        r_left: float,
        exit_timing: str,
        entry_quality: str,
        setup_type: str,
    ) -> list[str]:
        lessons = []
        if r_achieved < 0:
            lessons.append("⚠️ Loss trade — review if stop was placed correctly")
        if r_left > 2.0:
            lessons.append(f"💡 Left {r_left:.1f}R on table — consider trailing stop instead of full exit")
        if exit_timing == "early" and r_max > 3.0:
            lessons.append("📊 Exited too early — market was still trending, use EMA9 trail")
        if entry_quality == "C":
            lessons.append("⚠️ C-grade entry — wait for cleaner setup near EMA9/21")
        if entry_quality == "A" and r_achieved > 2.0:
            lessons.append("✅ A-grade entry with good R — this is the ideal template")
        if setup_type == "eps" and r_achieved > 3.0:
            lessons.append("🔥 EPS setup delivered — keep prioritizing earnings catalysts")
        return lessons

    @staticmethod
    def _compute_score(r_achieved: float, r_max: float, entry_quality: str) -> float:
        """Score 0-10"""
        base = min(max(r_achieved, -2), 5) * 1.5 + 2.5   # 0~10 range
        quality_bonus = {"A": 1.0, "B": 0.0, "C": -1.0}.get(entry_quality, 0)
        return min(10.0, max(0.0, round(base + quality_bonus, 1)))
