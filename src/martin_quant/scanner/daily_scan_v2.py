"""daily_scan_v2.py

DailyScanner V2 — 串接 AVWAP + Sector Regime + ORB 15m
=======================================================
在 V1 基礎上新增：
  1. AVWAPScorer 自動計算每支股票的 AVWAP 支撐評分
  2. SectorRegimeFilter 過濾不適合當前 regime 的 sector
  3. ORBTrigger 15m 入場觸發（需傳入 df_15m）
  4. 總評分 = setup_score × regime_weight + avwap_score_bonus + sector_bonus

Usage:
    from martin_quant.scanner.daily_scan_v2 import DailyScannerV2

    scanner = DailyScannerV2(equity=100_000)
    results = scanner.scan(
        watchlist_data={"NVDA": df_nvda, "AMD": df_amd},
        regime="BULL",
        watchlist_sectors={"NVDA": "semiconductors", "AMD": "semiconductors"},
        df_15m_map={"NVDA": df_nvda_15m},   # optional, for ORB
    )
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import logging

import pandas as pd

from martin_quant.anchors.avwap_scorer import AVWAPScorer, AVWAPScore
from martin_quant.regime.sector_regime_filter import SectorRegimeFilter
from martin_quant.timing.orb_15m_trigger import ORBTrigger, ORBSignal

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class ScanV2Config:
    min_setup_score: float = 0.55       # 基礎 setup 分門檻
    min_total_score: float = 0.60       # 總評分門檻
    regime_weight: dict = field(default_factory=lambda: {
        "BULL":      1.00,
        "WEAK_BULL": 0.80,
        "CHOPPY":    0.60,
        "BEAR":      0.30,
    })
    avwap_weight: float = 0.25          # AVWAP 評分在總評分的權重
    sector_bonus_enabled: bool = True   # 是否套用 sector 加成
    orb_enabled: bool = True            # 是否檢查 ORB trigger
    max_signals: int = 10               # 最多回傳幾個信號


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ScanV2Result:
    symbol: str
    setup_type: str           # "pullback" | "breakout" | "avwap_reclaim"
    setup_score: float        # 來自 V1 scanner
    avwap_score: float        # 來自 AVWAPScorer
    sector_bonus: float       # 來自 SectorRegimeFilter
    regime_weight: float
    total_score: float
    regime: str
    sector: str
    avwap_signals: list = field(default_factory=list)
    orb_signal: Optional[object] = None  # ORBSignal or None
    entry_note: str = ""

    def to_dict(self) -> dict:
        orb = self.orb_signal.to_dict() if self.orb_signal else None
        return {
            "symbol":         self.symbol,
            "setup_type":     self.setup_type,
            "setup_score":    round(self.setup_score, 3),
            "avwap_score":    round(self.avwap_score, 3),
            "sector_bonus":   round(self.sector_bonus, 3),
            "regime_weight":  round(self.regime_weight, 2),
            "total_score":    round(self.total_score, 3),
            "regime":         self.regime,
            "sector":         self.sector,
            "avwap_signals":  self.avwap_signals,
            "orb":            orb,
            "entry_note":     self.entry_note,
        }


# ---------------------------------------------------------------------------
# Scanner V2
# ---------------------------------------------------------------------------

class DailyScannerV2:
    """
    升級版每日掃描器：AVWAP + Sector + ORB 整合。

    Parameters
    ----------
    equity : float
    config : ScanV2Config, optional
    """

    def __init__(
        self,
        equity: float = 100_000.0,
        config: Optional[ScanV2Config] = None,
    ) -> None:
        self.equity = equity
        self.cfg    = config or ScanV2Config()
        self._avwap_scorer   = AVWAPScorer(auto_detect_anchors=True)
        self._sector_filter  = SectorRegimeFilter()
        self._orb_trigger    = ORBTrigger(equity=equity)

    def scan(
        self,
        watchlist_data: dict[str, pd.DataFrame],
        regime: str = "BULL",
        watchlist_sectors: Optional[dict[str, str]] = None,
        watchlist_setup_scores: Optional[dict[str, dict]] = None,
        df_15m_map: Optional[dict[str, pd.DataFrame]] = None,
    ) -> list[ScanV2Result]:
        """
        主掃描入口。

        Parameters
        ----------
        watchlist_data : dict[symbol, daily_ohlcv_df]
            每支股票的日線 OHLCV
        regime : str
            當前市場 regime（BULL / WEAK_BULL / CHOPPY / BEAR）
        watchlist_sectors : dict[symbol, sector_str], optional
            每支股票的 sector；若無則跳過 sector 過濾
        watchlist_setup_scores : dict[symbol, {"score": float, "type": str}], optional
            來自 V1 DailyScanner 的 setup 評分結果
        df_15m_map : dict[symbol, df_15m], optional
            15 分鐘 OHLCV（當日）；有提供才計算 ORB

        Returns
        -------
        list[ScanV2Result] sorted by total_score desc
        """
        cfg = self.cfg
        regime_w = cfg.regime_weight.get(regime, 0.70)
        sectors  = watchlist_sectors or {}
        scores   = watchlist_setup_scores or {}
        df15m    = df_15m_map or {}

        results: list[ScanV2Result] = []

        for symbol, df in watchlist_data.items():
            if df is None or len(df) < 30:
                continue

            # ── Setup score (from V1 or default) ─────────────────────────
            setup_info  = scores.get(symbol, {})
            setup_score = float(setup_info.get("score", 0.50))
            setup_type  = setup_info.get("type", "unknown")

            if setup_score < cfg.min_setup_score:
                continue

            # ── Sector filter ─────────────────────────────────────────────
            sector = sectors.get(symbol, "")
            sector_bonus = 0.0
            if cfg.sector_bonus_enabled and sector:
                if not self._sector_filter.allow(sector, regime):
                    log.debug("%s: sector '%s' avoided in %s regime", symbol, sector, regime)
                    continue
                sector_bonus = self._sector_filter.sector_score_bonus(sector, regime)

            # ── AVWAP score ───────────────────────────────────────────────
            avwap_result: AVWAPScore = self._avwap_scorer.score(symbol, df)
            avwap_contribution = avwap_result.total_score * cfg.avwap_weight

            # ── Total score ───────────────────────────────────────────────
            # Formula:
            # total = (setup_score × regime_weight) + avwap_contribution + sector_bonus
            total = (
                setup_score * regime_w
                + avwap_contribution
                + sector_bonus
            )
            total = round(min(total, 1.0), 3)

            if total < cfg.min_total_score:
                continue

            # ── ORB 15m trigger ───────────────────────────────────────────
            orb_signal: Optional[ORBSignal] = None
            if cfg.orb_enabled and symbol in df15m:
                orb_signal = self._orb_trigger.check(
                    symbol=symbol,
                    df_15m=df15m[symbol],
                    daily_setup_score=setup_score,
                )

            # ── Entry note ────────────────────────────────────────────────
            entry_notes = []
            if avwap_result.near_avwap_support:
                entry_notes.append("avwap_support")
            if avwap_result.avwap_reclaim:
                entry_notes.append("avwap_reclaim")
            if orb_signal:
                entry_notes.append(f"ORB_triggered@{orb_signal.entry_price:.2f}")

            results.append(ScanV2Result(
                symbol=symbol,
                setup_type=setup_type,
                setup_score=setup_score,
                avwap_score=avwap_result.total_score,
                sector_bonus=sector_bonus,
                regime_weight=regime_w,
                total_score=total,
                regime=regime,
                sector=sector or "unknown",
                avwap_signals=avwap_result.signals,
                orb_signal=orb_signal,
                entry_note=", ".join(entry_notes),
            ))

        # Sort by total_score desc, limit
        results.sort(key=lambda r: r.total_score, reverse=True)
        return results[:cfg.max_signals]

    def print_report(self, results: list[ScanV2Result], date: str = "") -> None:
        """Print formatted scan results to stdout."""
        print(f"\n{'='*60}")
        print(f"  DailyScannerV2 Report  {date}")
        print(f"{'='*60}")
        if not results:
            print("  No signals today.")
            return
        for i, r in enumerate(results, 1):
            print(f"\n{i}. {r.symbol:6s} [{r.setup_type}]  score={r.total_score:.3f}")
            print(f"   setup={r.setup_score:.3f} avwap={r.avwap_score:.3f} "
                  f"sector_bonus={r.sector_bonus:+.2f} regime_w={r.regime_weight:.2f}")
            if r.avwap_signals:
                print(f"   AVWAP: {' | '.join(r.avwap_signals)}")
            if r.orb_signal:
                o = r.orb_signal
                print(f"   ORB:  entry={o.entry_price:.2f} stop={o.stop_price:.2f} "
                      f"target={o.target_price:.2f} rvol={o.rvol:.1f}x")
            if r.entry_note:
                print(f"   Note: {r.entry_note}")
        print(f"\n{'='*60}\n")
