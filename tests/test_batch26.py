from __future__ import annotations

import numpy as np
import pandas as pd


def _trend_df(start: float, end: float, n: int = 260) -> pd.DataFrame:
    closes = np.linspace(start, end, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": closes * 0.995,
            "high": closes * 1.03,
            "low": closes * 0.97,
            "close": closes,
            "volume": [1_500_000] * n,
        },
        index=dates,
    )


class TestSectorNormalizationAliases:
    def test_real_metadata_labels_map_to_core_sectors(self):
        from martin_quant.regime import normalize_sector_name

        assert normalize_sector_name("Health Care") == "healthcare"
        assert normalize_sector_name("Financial Services") == "financials"
        assert normalize_sector_name("Aerospace & Defense") == "industrials"


class TestBreadthParticipationAnalyzer:
    def test_expanding_breadth_detected(self):
        from martin_quant.regime import BreadthParticipationAnalyzer

        spy_df = _trend_df(100, 140)
        universe = {
            "NVDA": _trend_df(100, 240),
            "AMD": _trend_df(90, 200),
            "AVGO": _trend_df(110, 230),
            "MSFT": _trend_df(120, 210),
            "META": _trend_df(130, 220),
        }
        sectors = {symbol: "Technology" for symbol in universe}
        snapshot = BreadthParticipationAnalyzer().analyze(universe, sector_map=sectors, spy_df=spy_df)

        assert snapshot.state == "EXPANDING"
        assert snapshot.leader_count >= 1
        assert snapshot.pct_above_ema21 > 0.8
        assert snapshot.bonus_for("long") > 0

    def test_very_weak_breadth_detected(self):
        from martin_quant.regime import BreadthParticipationAnalyzer

        spy_df = _trend_df(140, 100)
        universe = {
            "UTIL1": _trend_df(220, 120),
            "UTIL2": _trend_df(210, 110),
            "UTIL3": _trend_df(200, 105),
            "UTIL4": _trend_df(190, 100),
            "UTIL5": _trend_df(180, 95),
        }
        sectors = {symbol: "Utilities" for symbol in universe}
        snapshot = BreadthParticipationAnalyzer().analyze(universe, sector_map=sectors, spy_df=spy_df)

        assert snapshot.state == "VERY_WEAK"
        assert snapshot.pct_above_ema50 < 0.3
        assert snapshot.bonus_for("long") < 0
        assert snapshot.bonus_for("short") > 0


class TestTradeQualityWithBreadth:
    def test_very_weak_breadth_can_force_observe_only(self):
        from martin_quant.regime import BreadthParticipationSnapshot, MartinMarketContext, MartinTradeQualityEvaluator

        context = MartinMarketContext(
            regime="BULL",
            breakout_friendly=True,
            trade_less=False,
            short_bias_ok=False,
            avoid_new_shorts_on_open=False,
            qqq_above_ema50=True,
            iwm_above_ema50=True,
            qqq_ema50_slope_pct=1.0,
            iwm_ema50_slope_pct=0.9,
            qqq_day_change_pct=0.8,
            iwm_day_change_pct=0.6,
            notes=[],
        )
        breadth = BreadthParticipationSnapshot(
            state="VERY_WEAK",
            universe_size=50,
            pct_above_ema21=0.2,
            pct_above_ema50=0.1,
            pct_bull_stack=0.05,
            leader_count=1,
            leader_ratio=0.02,
            exposure_factor=0.35,
            notes=["breadth collapsed"],
        )
        state = MartinTradeQualityEvaluator().evaluate(context, breadth_snapshot=breadth)

        assert state.state == "OBSERVE_ONLY"
        assert state.allow_longs is False
        assert state.quality_weight == 0.0


class TestDynamicSectorRelativeStrength:
    def test_sector_strength_classifies_strong_and_weak_groups(self):
        from martin_quant.regime import DynamicSectorRelativeStrengthAnalyzer

        benchmark_df = _trend_df(100, 110, n=90)
        universe = {
            "NVDA": _trend_df(100, 180, n=90),
            "AMD": _trend_df(100, 170, n=90),
            "JNJ": _trend_df(120, 112, n=90),
            "PFE": _trend_df(115, 100, n=90),
        }
        sectors = {
            "NVDA": "Semiconductors",
            "AMD": "Semiconductors",
            "JNJ": "Health Care",
            "PFE": "Health Care",
        }
        sector_etf_data = {
            "semiconductors": _trend_df(100, 150, n=90),
            "healthcare": _trend_df(100, 92, n=90),
        }
        snapshots = DynamicSectorRelativeStrengthAnalyzer().analyze_universe(
            universe=universe,
            sector_map=sectors,
            sector_etf_data=sector_etf_data,
            benchmark_df=benchmark_df,
        )

        assert snapshots["semiconductors"].state == "STRONG"
        assert snapshots["healthcare"].state == "WEAK"
        assert snapshots["semiconductors"].bonus_for("long") > 0
        assert snapshots["healthcare"].bonus_for("short") > 0


class TestScannerDynamicOverlays:
    def test_scanner_ranks_strong_sector_above_weak_sector(self):
        from martin_quant.regime import BreadthParticipationSnapshot, SectorStrengthSnapshot
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2

        scanner = DailyScannerV2()
        breadth = BreadthParticipationSnapshot(
            state="EXPANDING",
            universe_size=20,
            pct_above_ema21=0.8,
            pct_above_ema50=0.7,
            pct_bull_stack=0.6,
            leader_count=4,
            leader_ratio=0.2,
            exposure_factor=1.0,
            notes=[],
        )
        sector_strength_map = {
            "semiconductors": SectorStrengthSnapshot(
                sector="Semiconductors",
                canonical_sector="semiconductors",
                etf_symbol="SOXX",
                member_count=8,
                sector_relative_score=0.12,
                avg_member_return=0.18,
                avg_member_relative_to_sector=0.04,
                pct_members_above_ema21=0.85,
                leading_member_ratio=0.5,
                state="STRONG",
                notes=[],
            ),
            "healthcare": SectorStrengthSnapshot(
                sector="Health Care",
                canonical_sector="healthcare",
                etf_symbol="XLV",
                member_count=8,
                sector_relative_score=-0.08,
                avg_member_return=-0.02,
                avg_member_relative_to_sector=-0.03,
                pct_members_above_ema21=0.25,
                leading_member_ratio=0.1,
                state="WEAK",
                notes=[],
            ),
        }
        results = scanner.scan(
            watchlist_data={
                "NVDA": _trend_df(100, 180, n=120),
                "LLY": _trend_df(100, 140, n=120),
            },
            regime="BULL",
            watchlist_sectors={"NVDA": "Semiconductors", "LLY": "Health Care"},
            watchlist_setup_scores={
                "NVDA": {"score": 0.8, "type": "pullback", "direction": "long"},
                "LLY": {"score": 0.8, "type": "pullback", "direction": "long"},
            },
            breadth_snapshot=breadth,
            sector_strength_map=sector_strength_map,
        )

        assert len(results) == 2
        assert results[0].symbol == "NVDA"
        assert results[0].dynamic_sector_bonus > results[1].dynamic_sector_bonus
        assert results[0].breadth_state == "EXPANDING"
        assert results[1].sector_strength_state == "WEAK"
