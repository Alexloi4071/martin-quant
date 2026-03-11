"""avwap_anchor_manager.py

Multi-Anchor AVWAP Manager
==========================
Martin Luk 策略：管理每支股票的多條 AVWAP 錨點，
提供整合後的支撐/阻力判斷給 DailyScanner 使用。

典型錨點組合:
  1. Earnings AVWAP   — 上季財報公告日
  2. Breakout AVWAP   — 最近突破日
  3. Major Low AVWAP  — 近60日最低點
  4. YTD AVWAP        — 年初第一個交易日

Usage:
    from martin_quant.anchors.avwap_anchor_manager import AVWAPAnchorManager

    mgr = AVWAPAnchorManager()
    summary = mgr.get_summary(symbol="NVDA", df=nvda_df, earnings_date="2025-02-26")
    print(summary.primary_avwap)
    print(summary.is_support_confirmed)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import logging
import datetime

import pandas as pd

from martin_quant.anchors.avwap_anchor import (
    AVWAPAnchor, AVWAPResult,
    find_earnings_anchor, find_major_low_anchor,
)

log = logging.getLogger(__name__)


@dataclass
class AVWAPSummary:
    """多錨點 AVWAP 整合摘要"""
    symbol: str
    primary_avwap: float                   # 最重要的 AVWAP（通常是 earnings）
    primary_label: str
    anchors: list[AVWAPResult] = field(default_factory=list)

    # 關鍵判斷
    is_price_above_primary: bool = False
    is_pulling_back_to_primary: bool = False
    is_support_confirmed: bool = False      # 多條 AVWAP 匯聚 → 強支撐
    confluence_zone: Optional[tuple[float, float]] = None  # (low, high) 支撐區
    avwap_score: float = 0.0               # 0.0 ~ 1.0，進入 total_score 加分

    def to_notes(self) -> str:
        parts = []
        for r in self.anchors:
            if r.is_valid:
                parts.append(
                    f"AVWAP[{r.anchor_label}]={r.current_avwap:.2f}"
                    f"({r.price_vs_avwap},{r.distance_pct:+.1f}%)"
                )
        return " | ".join(parts) if parts else "no_avwap"


class AVWAPAnchorManager:
    """
    為 DailyScanner 提供 AVWAP 多錨點支撐/阻力分析。

    Parameters
    ----------
    near_tolerance_pct : float
        判斷「接近 AVWAP」的容忍範圍，預設 2.0%
    confluence_gap_pct : float
        多條 AVWAP 在此範圍內視為「匯聚」，預設 1.5%
    """

    def __init__(
        self,
        near_tolerance_pct: float = 2.0,
        confluence_gap_pct: float = 1.5,
    ) -> None:
        self.near_tolerance_pct = near_tolerance_pct
        self.confluence_gap_pct = confluence_gap_pct

    def get_summary(
        self,
        symbol: str,
        df: pd.DataFrame,
        earnings_date: Optional[str] = None,
        breakout_date: Optional[str] = None,
        custom_anchors: Optional[dict[str, str]] = None,
    ) -> AVWAPSummary:
        """
        計算所有錨點的 AVWAP 並返回整合摘要。

        Parameters
        ----------
        symbol : str
        df : pd.DataFrame  OHLCV，DatetimeIndex
        earnings_date : str  上季財報日期 'YYYY-MM-DD'（可選）
        breakout_date : str  最近突破日期（可選）
        custom_anchors : dict  {label: date_str}（可選）
        """
        results: list[AVWAPResult] = []

        # 1. Earnings AVWAP
        if earnings_date:
            r = AVWAPAnchor(earnings_date, "earnings", self.near_tolerance_pct).calculate(df, symbol)
            if r and r.is_valid:
                results.append(r)
        else:
            # 嘗試自動偵測 earnings gap
            auto_eps = find_earnings_anchor(df)
            if auto_eps:
                r = AVWAPAnchor(auto_eps, "earnings_auto", self.near_tolerance_pct).calculate(df, symbol)
                if r and r.is_valid:
                    results.append(r)

        # 2. Breakout AVWAP
        if breakout_date:
            r = AVWAPAnchor(breakout_date, "breakout", self.near_tolerance_pct).calculate(df, symbol)
            if r and r.is_valid:
                results.append(r)

        # 3. Major Low AVWAP (auto)
        major_low_date = find_major_low_anchor(df)
        if major_low_date:
            r = AVWAPAnchor(major_low_date, "major_low", self.near_tolerance_pct).calculate(df, symbol)
            if r and r.is_valid:
                results.append(r)

        # 4. YTD AVWAP
        ytd_date = f"{datetime.date.today().year}-01-01"
        r = AVWAPAnchor(ytd_date, "ytd", self.near_tolerance_pct).calculate(df, symbol)
        if r and r.is_valid:
            results.append(r)

        # 5. Custom anchors
        if custom_anchors:
            for label, date_str in custom_anchors.items():
                r = AVWAPAnchor(date_str, label, self.near_tolerance_pct).calculate(df, symbol)
                if r and r.is_valid:
                    results.append(r)

        if not results:
            return AVWAPSummary(
                symbol=symbol,
                primary_avwap=0.0,
                primary_label="none",
                is_support_confirmed=False,
                avwap_score=0.0,
            )

        current_price = self._get_current_price(df)

        # Primary = 最近錨點（bars_since_anchor 最少）
        primary = min(results, key=lambda r: r.bars_since_anchor)

        # Confluence check
        valid_avwaps = [r.current_avwap for r in results if r.current_avwap > 0]
        confluence_zone = self._find_confluence(valid_avwaps)

        # Support confirmed = price above primary AND near primary OR confluence
        above_primary = current_price > primary.current_avwap
        near_primary  = primary.is_price_near_avwap(current_price, self.near_tolerance_pct)
        is_support = above_primary and (near_primary or confluence_zone is not None)

        # AVWAP score (0~1)
        score = self._compute_score(
            results, current_price, above_primary, near_primary, confluence_zone
        )

        return AVWAPSummary(
            symbol=symbol,
            primary_avwap=primary.current_avwap,
            primary_label=primary.anchor_label,
            anchors=results,
            is_price_above_primary=above_primary,
            is_pulling_back_to_primary=primary.is_pulling_back_to_avwap(current_price, self.near_tolerance_pct),
            is_support_confirmed=is_support,
            confluence_zone=confluence_zone,
            avwap_score=round(score, 3),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_confluence(
        self,
        avwap_values: list[float],
    ) -> Optional[tuple[float, float]]:
        """找出多條 AVWAP 匯聚區域"""
        if len(avwap_values) < 2:
            return None
        avwap_values = sorted(avwap_values)
        for i in range(len(avwap_values) - 1):
            gap_pct = (avwap_values[i + 1] - avwap_values[i]) / avwap_values[i] * 100
            if gap_pct <= self.confluence_gap_pct:
                return (avwap_values[i], avwap_values[i + 1])
        return None

    @staticmethod
    def _get_current_price(df: pd.DataFrame) -> float:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if "close" in df_c.columns and not df_c.empty:
            return float(df_c["close"].iloc[-1])
        return 0.0

    def _compute_score(
        self,
        results: list[AVWAPResult],
        current_price: float,
        above_primary: bool,
        near_primary: bool,
        confluence_zone: Optional[tuple],
    ) -> float:
        """AVWAP 信號評分 0~1"""
        score = 0.0
        if above_primary:
            score += 0.3
        if near_primary:
            score += 0.3
        if confluence_zone is not None:
            score += 0.2
        # Bonus: multiple AVWAPs all below price (full support stack)
        below_count = sum(1 for r in results if r.current_avwap < current_price)
        if below_count >= 3:
            score += 0.2
        elif below_count == 2:
            score += 0.1
        return min(score, 1.0)
