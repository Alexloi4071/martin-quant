"""avwap_scorer.py

AVWAP 串接評分模組
==================
將 AVWAPAnchorManager 的結果轉換為 setup 評分可用的數字，
讓 PullbackSetupDetector / BreakoutSetupDetector 能直接使用。

Design:
  AVWAPScorer.score(symbol, df, anchor_manager) -> AVWAPScore
  AVWAPScore.total_score  (0.0 ~ 1.0)
  AVWAPScore.signals      list[str]   (human-readable)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import logging

import pandas as pd

from martin_quant.anchors.avwap_anchor import AVWAPAnchor, AVWAPResult, find_earnings_anchor, find_major_low_anchor
from martin_quant.anchors.avwap_anchor_manager import AVWAPAnchorManager, AnchorProfile

log = logging.getLogger(__name__)


@dataclass
class AVWAPScore:
    """AVWAP 評分結果，可直接加入 SetupScore"""
    symbol: str
    total_score: float = 0.0          # 0.0 ~ 1.0
    avwap_reclaim: bool = False       # 最重要：收盤站上 AVWAP
    near_avwap_support: bool = False  # 回調到 AVWAP ±2% 支撐帶
    multiple_avwap_support: bool = False  # ≥2 條 AVWAP 在同一區域
    above_all_avwap: bool = False     # 站上所有 AVWAP（最強）
    primary_avwap: float = 0.0        # 主要 AVWAP 價格
    nearest_avwap: float = 0.0        # 最近的 AVWAP
    signals: list = field(default_factory=list)
    anchors_used: list = field(default_factory=list)


class AVWAPScorer:
    """
    計算當前 bar 的 AVWAP 評分。

    Parameters
    ----------
    near_tolerance_pct : float
        判斷「接近 AVWAP」的容忍百分比，預設 2.0
    auto_detect_anchors : bool
        若 AnchorManager 沒有錨點，自動從 earnings gap / major low 偵測
    """

    def __init__(
        self,
        near_tolerance_pct: float = 2.0,
        auto_detect_anchors: bool = True,
    ) -> None:
        self.near_tolerance_pct = near_tolerance_pct
        self.auto_detect_anchors = auto_detect_anchors

    def score(
        self,
        symbol: str,
        df: pd.DataFrame,
        anchor_manager: Optional[AVWAPAnchorManager] = None,
    ) -> AVWAPScore:
        """
        主要入口：計算 AVWAP 評分。

        Parameters
        ----------
        symbol : str
        df : pd.DataFrame
            日線 OHLCV（最少 60 根）
        anchor_manager : AVWAPAnchorManager, optional
            若已有 manager 直接使用，否則自動建立

        Returns
        -------
        AVWAPScore
        """
        result = AVWAPScore(symbol=symbol)
        if df is None or len(df) < 20:
            return result

        current_price = float(df["close"].iloc[-1] if "close" in df.columns
                              else df.iloc[-1, -1])

        # ── 收集所有 AVWAP 錨點結果 ──────────────────────────────────────────
        avwap_results: list[AVWAPResult] = []

        if anchor_manager is not None:
            avwap_results = anchor_manager.get_all_results(symbol, df)
        
        # 自動偵測補充錨點
        if self.auto_detect_anchors and len(avwap_results) < 2:
            avwap_results = self._auto_detect(symbol, df, existing=avwap_results)

        if not avwap_results:
            return result

        valid = [r for r in avwap_results if r.is_valid and r.current_avwap > 0]
        if not valid:
            return result

        result.anchors_used = [r.anchor_label for r in valid]
        avwap_prices = [r.current_avwap for r in valid]
        result.primary_avwap = valid[0].current_avwap  # first = most important
        result.nearest_avwap = min(avwap_prices, key=lambda x: abs(x - current_price))

        score = 0.0
        signals = []

        # ── 1. 站上主要 AVWAP（+0.35）───────────────────────────────────────
        primary = valid[0]
        if current_price > primary.current_avwap:
            result.avwap_reclaim = True
            score += 0.35
            signals.append(f"above_primary_avwap({primary.anchor_label}@{primary.current_avwap:.2f})")

        # ── 2. 站上所有 AVWAP（+0.20 bonus）─────────────────────────────────
        if all(current_price > r.current_avwap for r in valid):
            result.above_all_avwap = True
            score += 0.20
            signals.append("above_all_avwap")

        # ── 3. 回調至最近 AVWAP 支撐帶（+0.25）──────────────────────────────
        nearest = min(valid, key=lambda r: abs(r.current_avwap - current_price))
        near_pct = abs(current_price - nearest.current_avwap) / nearest.current_avwap * 100
        if near_pct <= self.near_tolerance_pct and current_price >= nearest.current_avwap:
            result.near_avwap_support = True
            score += 0.25
            signals.append(
                f"near_avwap_support({nearest.anchor_label}@{nearest.current_avwap:.2f},"
                f"{near_pct:.1f}%away)"
            )

        # ── 4. 多條 AVWAP 聚合在同一支撐帶（+0.20）──────────────────────────
        # 如果有 ≥2 條 AVWAP 在當前價格 ±3% 內
        cluster = [
            r for r in valid
            if abs(r.current_avwap - current_price) / current_price * 100 <= 3.0
        ]
        if len(cluster) >= 2:
            result.multiple_avwap_support = True
            score += 0.20
            labels = "+".join(r.anchor_label for r in cluster)
            signals.append(f"avwap_cluster({labels})")

        result.total_score = min(round(score, 3), 1.0)
        result.signals = signals
        return result

    # ── Private Helpers ────────────────────────────────────────────────────

    def _auto_detect(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing: list[AVWAPResult],
    ) -> list[AVWAPResult]:
        """Auto-detect earnings + major_low anchors if not already present."""
        existing_labels = {r.anchor_label for r in existing}
        results = list(existing)

        # Earnings anchor
        if "earnings" not in existing_labels:
            earnings_date = find_earnings_anchor(df)
            if earnings_date:
                anchor = AVWAPAnchor(earnings_date, anchor_label="earnings",
                                     near_tolerance_pct=self.near_tolerance_pct)
                r = anchor.calculate(df, symbol=symbol)
                if r and r.is_valid:
                    results.insert(0, r)  # earnings is primary

        # Major low anchor
        if "major_low" not in existing_labels:
            low_date = find_major_low_anchor(df)
            if low_date:
                anchor = AVWAPAnchor(low_date, anchor_label="major_low",
                                     near_tolerance_pct=self.near_tolerance_pct)
                r = anchor.calculate(df, symbol=symbol)
                if r and r.is_valid:
                    results.append(r)

        return results
