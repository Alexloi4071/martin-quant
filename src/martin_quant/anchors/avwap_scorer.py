"""avwap_scorer.py

Compatibility scorer built on top of the current AVWAPAnchorManager API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import logging

import pandas as pd

from martin_quant.anchors.avwap_anchor_manager import AVWAPAnchorManager

log = logging.getLogger(__name__)


@dataclass
class AVWAPScore:
    symbol: str
    total_score: float = 0.0
    avwap_reclaim: bool = False
    near_avwap_support: bool = False
    multiple_avwap_support: bool = False
    above_all_avwap: bool = False
    primary_avwap: float = 0.0
    nearest_avwap: float = 0.0
    signals: list[str] = field(default_factory=list)
    anchors_used: list[str] = field(default_factory=list)


class AVWAPScorer:
    def __init__(
        self,
        near_tolerance_pct: float = 2.0,
        auto_detect_anchors: bool = True,
    ) -> None:
        self.near_tolerance_pct = near_tolerance_pct
        self.auto_detect_anchors = auto_detect_anchors

    @staticmethod
    def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out.columns = [str(c).lower() for c in out.columns]
        if "timestamp" in out.columns:
            out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
            out = out.dropna(subset=["timestamp"]).set_index("timestamp")
        elif not isinstance(out.index, pd.DatetimeIndex):
            out.index = pd.to_datetime(out.index, utc=True, errors="coerce")
            out = out[~out.index.isna()]
        return out.sort_index()

    def score(
        self,
        symbol: str,
        df: pd.DataFrame,
        anchor_manager: Optional[AVWAPAnchorManager] = None,
    ) -> AVWAPScore:
        result = AVWAPScore(symbol=symbol)
        if df is None or len(df) < 20:
            return result

        mgr = anchor_manager or AVWAPAnchorManager(touch_threshold_pct=min(self.near_tolerance_pct, 0.5))
        prepared = self._prepare_df(df)
        if prepared.empty:
            return result

        try:
            av = mgr.compute(symbol=symbol, df=prepared)
        except Exception as exc:
            log.debug("AVWAP scoring failed for %s: %s", symbol, exc)
            return result

        if not av.avwap_lines:
            return result

        current_price = float(av.current_price)
        supports = [line for line in av.avwap_lines if line.is_support]
        near_supports = [line for line in supports if abs(line.distance_pct) <= self.near_tolerance_pct]

        result.anchors_used = [line.anchor_type for line in av.avwap_lines]
        result.primary_avwap = av.avwap_lines[0].current_value
        result.nearest_avwap = min(av.avwap_lines, key=lambda line: abs(line.distance_pct)).current_value

        score = 0.0
        signals: list[str] = []

        if av.nearest_support is not None and current_price >= av.nearest_support.current_value:
            result.avwap_reclaim = True
            score += 0.35
            signals.append(f"above_primary_avwap({av.nearest_support.anchor_type}@{av.nearest_support.current_value:.2f})")

        if av.touching_support or near_supports:
            result.near_avwap_support = True
            score += 0.25
            line = av.nearest_support or near_supports[0]
            signals.append(f"near_avwap_support({line.anchor_type}@{line.current_value:.2f})")

        if len(near_supports) >= 2:
            result.multiple_avwap_support = True
            score += 0.20
            signals.append("avwap_cluster(" + "+".join(line.anchor_type for line in near_supports[:3]) + ")")

        if all(current_price >= line.current_value for line in av.avwap_lines):
            result.above_all_avwap = True
            score += 0.20
            signals.append("above_all_avwap")

        result.total_score = min(round(score, 3), 1.0)
        result.signals = signals
        return result
