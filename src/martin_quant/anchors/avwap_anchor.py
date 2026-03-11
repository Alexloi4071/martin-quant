"""avwap_anchor.py

Anchored VWAP (AVWAP) Calculation Engine
=========================================
Martin Luk 策略核心工具 — AVWAP 支撐/阻力計算

功能:
  - 從任意錨點（earnings date, breakout day, IPO, major low）計算 AVWAP
  - 提供多條 AVWAP + 標準差帶 (±1σ, ±2σ)
  - 判斷當前價格與 AVWAP 的相對位置（above/below/near）
  - 整合進 PullbackSetupDetector 作為確認條件

Usage:
    from martin_quant.anchors.avwap_anchor import AVWAPAnchor, AVWAPResult

    anchor = AVWAPAnchor(anchor_date="2025-01-15")
    result = anchor.calculate(df)   # df: OHLCV DataFrame
    print(result.current_avwap)
    print(result.is_price_near_avwap(current_price=125.0))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import logging

import pandas as pd
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class AVWAPBand:
    """AVWAP 標準差帶"""
    upper_1: float   # AVWAP + 1σ
    lower_1: float   # AVWAP - 1σ
    upper_2: float   # AVWAP + 2σ
    lower_2: float   # AVWAP - 2σ


@dataclass
class AVWAPResult:
    """AVWAP 計算結果"""
    symbol: str
    anchor_date: str
    anchor_label: str                      # "earnings" | "breakout" | "ipo" | "major_low" | "custom"
    current_avwap: float
    band: AVWAPBand
    price_vs_avwap: str                    # "above" | "below" | "near"
    distance_pct: float                    # (price - avwap) / avwap * 100
    avwap_series: pd.Series = field(default_factory=pd.Series)
    upper_1_series: pd.Series = field(default_factory=pd.Series)
    lower_1_series: pd.Series = field(default_factory=pd.Series)
    bars_since_anchor: int = 0
    is_valid: bool = True

    def is_price_near_avwap(
        self,
        current_price: float,
        tolerance_pct: float = 2.0,
    ) -> bool:
        """價格是否接近 AVWAP（在 tolerance_pct 範圍內）"""
        if self.current_avwap <= 0:
            return False
        diff_pct = abs(current_price - self.current_avwap) / self.current_avwap * 100
        return diff_pct <= tolerance_pct

    def is_price_above_avwap(self, current_price: float) -> bool:
        return current_price > self.current_avwap

    def is_pulling_back_to_avwap(
        self,
        current_price: float,
        tolerance_pct: float = 2.0,
    ) -> bool:
        """價格在 AVWAP 之上 & 正在回測 AVWAP 附近 → pullback 買點"""
        return (
            self.price_vs_avwap in ("above", "near")
            and self.is_price_near_avwap(current_price, tolerance_pct)
        )

    def support_strength(self) -> str:
        """評估 AVWAP 支撐強度"""
        if self.bars_since_anchor >= 60:
            return "strong"    # 60 日以上的 AVWAP 更可靠
        elif self.bars_since_anchor >= 20:
            return "medium"
        else:
            return "weak"


class AVWAPAnchor:
    """
    Anchored VWAP 計算器。

    Parameters
    ----------
    anchor_date : str
        錨點日期，格式 'YYYY-MM-DD'
    anchor_label : str
        錨點類型標籤（用於 debug/display）
    near_tolerance_pct : float
        判斷「接近」AVWAP 的容忍百分比，預設 2.0%
    """

    def __init__(
        self,
        anchor_date: str,
        anchor_label: str = "custom",
        near_tolerance_pct: float = 2.0,
    ) -> None:
        self.anchor_date = anchor_date
        self.anchor_label = anchor_label
        self.near_tolerance_pct = near_tolerance_pct

    def calculate(
        self,
        df: pd.DataFrame,
        symbol: str = "",
    ) -> Optional[AVWAPResult]:
        """
        計算從錨點到最新日期的 AVWAP。

        Parameters
        ----------
        df : pd.DataFrame
            OHLCV DataFrame，index 為 DatetimeIndex，
            需有欄位: open, high, low, close, volume
            (大小寫不敏感)
        symbol : str
            股票代號（用於 logging）

        Returns
        -------
        AVWAPResult or None if anchor not found in data
        """
        df = self._normalize_columns(df)
        if df is None:
            return None

        # 確保 index 為 DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                df.index = pd.to_datetime(df.index)
            except Exception:
                log.warning("%s: Cannot parse DatetimeIndex", symbol)
                return None

        anchor_dt = pd.Timestamp(self.anchor_date)

        # 找錨點 index（>= anchor_date 的第一天）
        mask = df.index >= anchor_dt
        if not mask.any():
            log.debug("%s: anchor_date %s not in data", symbol, self.anchor_date)
            return AVWAPResult(
                symbol=symbol,
                anchor_date=self.anchor_date,
                anchor_label=self.anchor_label,
                current_avwap=0.0,
                band=AVWAPBand(0, 0, 0, 0),
                price_vs_avwap="unknown",
                distance_pct=0.0,
                is_valid=False,
            )

        anchored_df = df[mask].copy()
        bars_since = len(anchored_df)

        # Typical price
        tp = (anchored_df["high"] + anchored_df["low"] + anchored_df["close"]) / 3.0
        vol = anchored_df["volume"].replace(0, np.nan).fillna(1)

        # Cumulative VWAP
        cum_tp_vol = (tp * vol).cumsum()
        cum_vol = vol.cumsum()
        avwap_series = cum_tp_vol / cum_vol

        # Standard deviation bands
        # Rolling variance of typical price weighted by volume
        cum_tp2_vol = (tp ** 2 * vol).cumsum()
        variance = (cum_tp2_vol / cum_vol) - (avwap_series ** 2)
        variance = variance.clip(lower=0)
        std_series = np.sqrt(variance)

        current_avwap = float(avwap_series.iloc[-1])
        current_std   = float(std_series.iloc[-1])
        current_price = float(anchored_df["close"].iloc[-1])

        upper_1 = current_avwap + current_std
        lower_1 = current_avwap - current_std
        upper_2 = current_avwap + 2 * current_std
        lower_2 = current_avwap - 2 * current_std

        # Price position
        dist_pct = (current_price - current_avwap) / current_avwap * 100
        if abs(dist_pct) <= self.near_tolerance_pct:
            price_vs_avwap = "near"
        elif current_price > current_avwap:
            price_vs_avwap = "above"
        else:
            price_vs_avwap = "below"

        return AVWAPResult(
            symbol=symbol,
            anchor_date=self.anchor_date,
            anchor_label=self.anchor_label,
            current_avwap=round(current_avwap, 4),
            band=AVWAPBand(
                upper_1=round(upper_1, 4),
                lower_1=round(lower_1, 4),
                upper_2=round(upper_2, 4),
                lower_2=round(lower_2, 4),
            ),
            price_vs_avwap=price_vs_avwap,
            distance_pct=round(dist_pct, 2),
            avwap_series=avwap_series,
            upper_1_series=avwap_series + std_series,
            lower_1_series=avwap_series - std_series,
            bars_since_anchor=bars_since,
            is_valid=True,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_columns(df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """統一欄位名稱為小寫"""
        if df is None or df.empty:
            return None
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        required = {"high", "low", "close", "volume"}
        if not required.issubset(set(df.columns)):
            log.warning("AVWAP: missing columns %s", required - set(df.columns))
            return None
        return df


# ---------------------------------------------------------------------------
# Convenience: auto-detect anchor from common events
# ---------------------------------------------------------------------------

def find_earnings_anchor(df: pd.DataFrame, lookback_days: int = 90) -> Optional[str]:
    """
    嘗試找出最近一次 earnings gap（大量 + 跳空）作為 AVWAP 錨點。
    真實系統應從 earnings calendar API 獲取；此為 heuristic 版本。
    """
    if df is None or len(df) < 10:
        return None
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if "volume" not in df.columns or "close" not in df.columns:
        return None

    recent = df.iloc[-lookback_days:] if len(df) >= lookback_days else df
    vol_mean = recent["volume"].mean()
    gap_pct  = (recent["close"] - recent["close"].shift(1)) / recent["close"].shift(1) * 100

    # Find days with gap > 5% AND volume > 2x average
    candidates = recent[
        (gap_pct.abs() > 5.0) & (recent["volume"] > vol_mean * 2)
    ]
    if candidates.empty:
        return None

    # Return most recent
    return str(candidates.index[-1].date())


def find_major_low_anchor(df: pd.DataFrame, lookback_days: int = 60) -> Optional[str]:
    """找近期重要低點作為 AVWAP 錨點"""
    if df is None or len(df) < 10:
        return None
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    recent = df.iloc[-lookback_days:] if len(df) >= lookback_days else df
    min_idx = recent["low"].idxmin()
    return str(min_idx.date()) if hasattr(min_idx, "date") else str(min_idx)
