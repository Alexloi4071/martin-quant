"""Direction-aware V2 daily scanner with AVWAP and transcript-driven timing overlays."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import logging

import pandas as pd

from martin_quant.anchors.avwap_scorer import AVWAPScore, AVWAPScorer
from martin_quant.entry.close_confirmation import CloseConfirmation
from martin_quant.features.gap_context import GapContext, analyze_gap_context
from martin_quant.features.weekly_context import WeeklyContext
from martin_quant.regime.breadth_participation import BreadthParticipationSnapshot
from martin_quant.regime.sector_regime_filter import SectorRegimeFilter, normalize_sector_name
from martin_quant.regime.sector_relative_strength import SectorStrengthSnapshot
from martin_quant.timing.orb_15m_trigger import ORBSignal, ORBTrigger
from martin_quant.timing.short_retest_trigger import ShortRetestBreakdownTrigger

log = logging.getLogger(__name__)


@dataclass
class ScanV2Config:
    min_setup_score: float = 0.55
    min_total_score: float = 0.60
    regime_weight: dict[str, float] = field(
        default_factory=lambda: {
            "BULL": 1.00,
            "WEAK_BULL": 0.80,
            "CHOPPY": 0.60,
            "BEAR": 0.30,
        }
    )
    avwap_weight: float = 0.25
    sector_bonus_enabled: bool = True
    dynamic_sector_bonus_enabled: bool = True
    breadth_bonus_enabled: bool = True
    weekly_bonus_enabled: bool = True
    gap_bonus_enabled: bool = True
    orb_enabled: bool = True
    max_signals: int = 10


@dataclass
class ScanV2Result:
    symbol: str
    direction: str
    setup_type: str
    setup_score: float
    avwap_score: float
    sector_bonus: float
    dynamic_sector_bonus: float
    breadth_bonus: float
    weekly_bonus: float
    gap_bonus: float
    regime_weight: float
    total_score: float
    regime: str
    sector: str
    trade_quality_state: str = "GO"
    breadth_state: str = "UNKNOWN"
    sector_strength_state: str = "UNKNOWN"
    weekly_trend_state: str = "UNKNOWN"
    gap_label: str = "UNKNOWN"
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    avwap_signals: list[str] = field(default_factory=list)
    orb_signal: Optional[object] = None
    timing_signal: Optional[object] = None
    gap_context: Optional[dict[str, object]] = None
    entry_note: str = ""

    def to_dict(self) -> dict:
        orb = self.orb_signal.to_dict() if self.orb_signal else None
        timing = self.timing_signal.to_dict() if self.timing_signal else None
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "setup_type": self.setup_type,
            "setup_score": round(self.setup_score, 3),
            "avwap_score": round(self.avwap_score, 3),
            "sector_bonus": round(self.sector_bonus, 3),
            "dynamic_sector_bonus": round(self.dynamic_sector_bonus, 3),
            "breadth_bonus": round(self.breadth_bonus, 3),
            "weekly_bonus": round(self.weekly_bonus, 3),
            "gap_bonus": round(self.gap_bonus, 3),
            "regime_weight": round(self.regime_weight, 2),
            "total_score": round(self.total_score, 3),
            "regime": self.regime,
            "sector": self.sector,
            "trade_quality_state": self.trade_quality_state,
            "breadth_state": self.breadth_state,
            "sector_strength_state": self.sector_strength_state,
            "weekly_trend_state": self.weekly_trend_state,
            "gap_label": self.gap_label,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "avwap_signals": self.avwap_signals,
            "orb": orb,
            "timing": timing,
            "gap_context": self.gap_context,
            "entry_note": self.entry_note,
        }


class DailyScannerV2:
    def __init__(self, equity: float = 100_000.0, config: Optional[ScanV2Config] = None) -> None:
        self.equity = equity
        self.cfg = config or ScanV2Config()
        self._avwap_scorer = AVWAPScorer(auto_detect_anchors=True)
        self._sector_filter = SectorRegimeFilter()
        self._close_confirmation = CloseConfirmation()
        self._orb_trigger = ORBTrigger(equity=equity, close_confirmation=self._close_confirmation)
        self._short_trigger = ShortRetestBreakdownTrigger(close_confirmation=self._close_confirmation)

    def scan(
        self,
        watchlist_data: dict[str, pd.DataFrame],
        regime: str = "BULL",
        watchlist_sectors: Optional[dict[str, str]] = None,
        watchlist_setup_scores: Optional[dict[str, dict]] = None,
        df_15m_map: Optional[dict[str, pd.DataFrame]] = None,
        benchmark_15m_map: Optional[dict[str, pd.DataFrame]] = None,
        trade_quality_state: str = "GO",
        trade_quality_weight: float = 1.0,
        allow_longs: bool = True,
        allow_shorts: bool = True,
        breadth_snapshot: Optional[BreadthParticipationSnapshot] = None,
        sector_strength_map: Optional[dict[str, SectorStrengthSnapshot]] = None,
        weekly_context_map: Optional[dict[str, WeeklyContext]] = None,
    ) -> list[ScanV2Result]:
        if trade_quality_state == "OBSERVE_ONLY":
            return []

        cfg = self.cfg
        regime_weight = cfg.regime_weight.get(regime, 0.70)
        sectors = watchlist_sectors or {}
        setup_scores = watchlist_setup_scores or {}
        df15m_map = df_15m_map or {}
        benchmark_intraday = benchmark_15m_map or {}
        sector_strengths = sector_strength_map or {}
        weekly_map = weekly_context_map or {}
        benchmark_short_df = benchmark_intraday.get("QQQ") if benchmark_intraday.get("QQQ") is not None else benchmark_intraday.get("SPY")

        results: list[ScanV2Result] = []
        for symbol, df in watchlist_data.items():
            if df is None or len(df) < 30:
                continue
            if weekly_context_map is not None and symbol not in weekly_map:
                continue

            setup_info = setup_scores.get(symbol, {})
            setup_score = float(setup_info.get("score", 0.50))
            setup_type = str(setup_info.get("type", "unknown"))
            direction = str(setup_info.get("direction", "long"))
            if setup_score < cfg.min_setup_score:
                continue
            if direction == "long" and not allow_longs:
                continue
            if direction == "short" and not allow_shorts:
                continue

            weekly_context = weekly_map.get(symbol)
            weekly_bonus = 0.0
            weekly_trend_state = "UNKNOWN"
            if weekly_context is not None:
                weekly_trend_state = weekly_context.trend_state.upper()
                if direction == "long" and not weekly_context.is_long_favourable():
                    continue
                if direction == "short" and not weekly_context.is_short_favourable():
                    continue
                if cfg.weekly_bonus_enabled:
                    weekly_bonus = self._weekly_bonus(weekly_context, direction)

            sector = sectors.get(symbol, "")
            sector_bonus = 0.0
            if cfg.sector_bonus_enabled and sector:
                if direction == "short":
                    if not self._sector_filter.allow_short(sector, regime):
                        continue
                    sector_bonus = self._sector_filter.sector_score_bonus_short(sector, regime)
                else:
                    if not self._sector_filter.allow_long(sector, regime):
                        continue
                    sector_bonus = self._sector_filter.sector_score_bonus_long(sector, regime)

            dynamic_sector_bonus = 0.0
            sector_strength_state = "UNKNOWN"
            canonical_sector = normalize_sector_name(sector)
            sector_strength = sector_strengths.get(canonical_sector)
            if cfg.dynamic_sector_bonus_enabled and sector_strength is not None:
                dynamic_sector_bonus = sector_strength.bonus_for(direction)
                sector_strength_state = sector_strength.state

            breadth_bonus = 0.0
            breadth_state = "UNKNOWN"
            if cfg.breadth_bonus_enabled and breadth_snapshot is not None:
                breadth_bonus = breadth_snapshot.bonus_for(direction)
                breadth_state = breadth_snapshot.state

            avwap_result: AVWAPScore = self._avwap_scorer.score(symbol, df)
            avwap_contribution = avwap_result.total_score * cfg.avwap_weight

            intraday_df = df15m_map.get(symbol)
            gap_context = analyze_gap_context(daily_df=df, intraday_df=intraday_df)
            gap_payload = gap_context.to_dict() if gap_context is not None else None
            gap_label = gap_context.label if gap_context is not None else "UNKNOWN"
            gap_bonus = self._gap_bonus(gap_context, direction) if cfg.gap_bonus_enabled else 0.0

            raw_total = (
                setup_score * regime_weight
                + avwap_contribution
                + sector_bonus
                + dynamic_sector_bonus
                + breadth_bonus
                + weekly_bonus
                + gap_bonus
            )
            total_score = round(min(raw_total * trade_quality_weight, 1.0), 3)
            if total_score < cfg.min_total_score:
                continue

            orb_signal: Optional[ORBSignal] = None
            timing_signal = None
            if intraday_df is not None:
                if direction == "long" and cfg.orb_enabled:
                    orb_signal = self._orb_trigger.check(symbol=symbol, df_15m=intraday_df, daily_setup_score=setup_score)
                elif direction == "short":
                    timing_signal = self._short_trigger.detect(
                        symbol=symbol,
                        df_15m=intraday_df,
                        benchmark_df_15m=benchmark_short_df,
                        daily_df=df,
                    )

            entry_notes: list[str] = []
            if weekly_context is not None:
                entry_notes.append(f"weekly={weekly_context.trend_state}")
            if avwap_result.near_avwap_support:
                entry_notes.append("avwap_support")
            if avwap_result.avwap_reclaim:
                entry_notes.append("avwap_reclaim")
            if gap_context is not None and gap_context.label != "flat_open":
                entry_notes.append(gap_context.label)
            if trade_quality_state != "GO":
                entry_notes.append(f"trade_quality={trade_quality_state.lower()}")
            if breadth_state != "UNKNOWN":
                entry_notes.append(f"breadth={breadth_state.lower()}")
            if sector_strength_state != "UNKNOWN":
                entry_notes.append(f"sector_rs={sector_strength_state.lower()}")
            if direction == "short":
                entry_notes.append("short_bias")
            if orb_signal:
                entry_notes.append(f"ORB_triggered@{orb_signal.entry_price:.2f}")
            if timing_signal:
                entry_notes.append(f"short_trigger@{timing_signal.entry_price:.2f}")
            if setup_info.get("notes"):
                entry_notes.extend(str(item) for item in setup_info["notes"])

            results.append(
                ScanV2Result(
                    symbol=symbol,
                    direction=direction,
                    setup_type=setup_type,
                    setup_score=setup_score,
                    avwap_score=avwap_result.total_score,
                    sector_bonus=sector_bonus,
                    dynamic_sector_bonus=dynamic_sector_bonus,
                    breadth_bonus=breadth_bonus,
                    weekly_bonus=weekly_bonus,
                    gap_bonus=gap_bonus,
                    regime_weight=regime_weight,
                    total_score=total_score,
                    regime=regime,
                    sector=sector or "unknown",
                    trade_quality_state=trade_quality_state,
                    breadth_state=breadth_state,
                    sector_strength_state=sector_strength_state,
                    weekly_trend_state=weekly_trend_state,
                    gap_label=gap_label,
                    entry_price=setup_info.get("entry_price"),
                    stop_price=setup_info.get("stop_price"),
                    target_price=setup_info.get("target_price"),
                    avwap_signals=avwap_result.signals,
                    orb_signal=orb_signal,
                    timing_signal=timing_signal,
                    gap_context=gap_payload,
                    entry_note=", ".join(entry_notes),
                )
            )

        results.sort(key=lambda item: item.total_score, reverse=True)
        return results[: cfg.max_signals]

    @staticmethod
    def _weekly_bonus(weekly_context: WeeklyContext, direction: str) -> float:
        bonus = 0.0
        if direction == "long":
            if weekly_context.trend_state == "bull":
                bonus += 0.06
            elif weekly_context.trend_state == "neutral":
                bonus += 0.02
            if weekly_context.close_above_wema9:
                bonus += 0.02
            if weekly_context.close_above_wema21:
                bonus += 0.01
            if weekly_context.rs_vs_spy is not None and weekly_context.rs_vs_spy > 0:
                bonus += 0.01
        else:
            if weekly_context.trend_state == "bear":
                bonus += 0.06
            elif weekly_context.trend_state == "neutral":
                bonus += 0.02
            if not weekly_context.close_above_wema9:
                bonus += 0.02
            if not weekly_context.close_above_wema21:
                bonus += 0.01
            if weekly_context.rs_vs_spy is not None and weekly_context.rs_vs_spy < 0:
                bonus += 0.01
        return round(bonus, 3)

    @staticmethod
    def _gap_bonus(gap_context: GapContext | None, direction: str) -> float:
        if gap_context is None:
            return 0.0

        if direction == "long":
            base = {
                "gap_down_into_support": 0.06,
                "gap_up_clear": 0.02,
                "gap_down_clear": -0.03,
                "gap_up_into_resistance": -0.08,
                "flat_open": 0.0,
            }.get(gap_context.label, 0.0)
            if gap_context.filled_gap and gap_context.direction == "gap_down":
                base += 0.01
        else:
            base = {
                "gap_up_into_resistance": 0.08,
                "gap_up_clear": 0.03,
                "gap_down_clear": -0.04,
                "gap_down_into_support": -0.10,
                "flat_open": 0.0,
            }.get(gap_context.label, 0.0)
            if gap_context.filled_gap and gap_context.direction == "gap_up":
                base += 0.02
        return round(base, 3)

    def print_report(self, results: list[ScanV2Result], date: str = "") -> None:
        print(f"\n{'=' * 60}")
        print(f"  DailyScannerV2 Report  {date}")
        print(f"{'=' * 60}")
        if not results:
            print("  No signals today.")
            return
        for idx, result in enumerate(results, 1):
            print(f"\n{idx}. {result.symbol:6s} [{result.direction}:{result.setup_type}] score={result.total_score:.3f}")
            print(
                f"   setup={result.setup_score:.3f} avwap={result.avwap_score:.3f} "
                f"sector_static={result.sector_bonus:+.2f} sector_dyn={result.dynamic_sector_bonus:+.2f} "
                f"breadth={result.breadth_bonus:+.2f} weekly={result.weekly_bonus:+.2f} gap={result.gap_bonus:+.2f} "
                f"regime_w={result.regime_weight:.2f} quality={result.trade_quality_state}"
            )
            if result.entry_price is not None and result.stop_price is not None:
                levels = f"   levels: entry={result.entry_price:.2f} stop={result.stop_price:.2f}"
                if result.target_price is not None:
                    levels += f" target={result.target_price:.2f}"
                print(levels)
            if result.avwap_signals:
                print(f"   AVWAP: {' | '.join(result.avwap_signals)}")
            if result.gap_context:
                print(f"   Gap:  {result.gap_context['label']} gap={result.gap_context['gap_pct']:.2f}% fill={result.gap_context['fill_pct']:.2f}")
            if result.weekly_trend_state != "UNKNOWN":
                print(f"   Weekly: {result.weekly_trend_state}")
            if result.orb_signal:
                orb = result.orb_signal
                print(f"   ORB:  entry={orb.entry_price:.2f} stop={orb.stop_price:.2f} target={orb.target_price:.2f} rvol={orb.rvol:.1f}x")
            if result.timing_signal:
                sig = result.timing_signal
                print(f"   Timing: entry={sig.entry_price:.2f} stop={sig.stop_price:.2f} target={sig.target_price:.2f} score={sig.score:.2f}")
            if result.entry_note:
                print(f"   Note: {result.entry_note}")
        print(f"\n{'=' * 60}\n")

