"""test_batch16.py — Batch 16 unit tests"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


# ── Fixtures ────────────────────────────────────────────────────────────────

def make_daily_df(n: int = 120, start_price: float = 100.0, trend: str = "up") -> pd.DataFrame:
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
    prices = []
    p = start_price
    for i in range(n):
        if trend == "up":
            p *= (1 + np.random.uniform(0, 0.015))
        elif trend == "down":
            p *= (1 - np.random.uniform(0, 0.015))
        else:
            p *= (1 + np.random.uniform(-0.01, 0.01))
        prices.append(p)
    df = pd.DataFrame({
        "open":   [p * 0.99 for p in prices],
        "high":   [p * 1.02 for p in prices],
        "low":    [p * 0.98 for p in prices],
        "close":  prices,
        "volume": [np.random.randint(800_000, 2_000_000) for _ in range(n)],
    }, index=dates)
    return df


def make_15m_df(n_bars: int = 30, or_high: float = 105.0, or_low: float = 103.0,
                breakout: bool = True) -> pd.DataFrame:
    """Simulate today's 15m data with optional ORB breakout"""
    times = pd.date_range("2026-03-12 09:30", periods=n_bars, freq="15min")
    closes = [or_low + (or_high - or_low) * 0.5] * n_bars
    highs  = [or_high * 0.99] * n_bars
    lows   = [or_low * 1.01] * n_bars
    vols   = [500_000] * n_bars

    if breakout and n_bars >= 3:
        # bar index 2 breaks out
        closes[2] = or_high * 1.015
        highs[2]  = or_high * 1.02
        vols[2]   = 1_500_000   # high volume

    return pd.DataFrame({
        "open":   [or_low + 0.5] * n_bars,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": vols,
    }, index=times)


# ── AVWAPScorer Tests ────────────────────────────────────────────────────────

class TestAVWAPScorer:
    def test_import(self):
        from martin_quant.anchors.avwap_scorer import AVWAPScorer
        scorer = AVWAPScorer()
        assert scorer is not None

    def test_score_returns_result(self):
        from martin_quant.anchors.avwap_scorer import AVWAPScorer
        df = make_daily_df(120)
        scorer = AVWAPScorer(auto_detect_anchors=True)
        result = scorer.score("TEST", df)
        assert 0.0 <= result.total_score <= 1.0
        assert result.symbol == "TEST"

    def test_score_empty_df(self):
        from martin_quant.anchors.avwap_scorer import AVWAPScorer
        scorer = AVWAPScorer()
        result = scorer.score("EMPTY", pd.DataFrame())
        assert result.total_score == 0.0


# ── ORBTrigger Tests ─────────────────────────────────────────────────────────

class TestORBTrigger:
    def test_import(self):
        from martin_quant.timing.orb_15m_trigger import ORBTrigger
        t = ORBTrigger(equity=100_000)
        assert t is not None

    def test_breakout_detected(self):
        from martin_quant.timing.orb_15m_trigger import ORBTrigger
        df15m = make_15m_df(breakout=True)
        trigger = ORBTrigger(equity=100_000)
        signal = trigger.check("NVDA", df15m, daily_setup_score=0.75)
        assert signal is not None
        assert signal.entry_price > signal.or_high
        assert signal.stop_price == signal.or_low
        assert signal.r_potential >= 1.5

    def test_no_breakout(self):
        from martin_quant.timing.orb_15m_trigger import ORBTrigger
        df15m = make_15m_df(breakout=False)
        trigger = ORBTrigger(equity=100_000)
        signal = trigger.check("NVDA", df15m, daily_setup_score=0.75)
        assert signal is None

    def test_low_daily_score_blocked(self):
        from martin_quant.timing.orb_15m_trigger import ORBTrigger
        df15m = make_15m_df(breakout=True)
        trigger = ORBTrigger(equity=100_000)
        signal = trigger.check("NVDA", df15m, daily_setup_score=0.2)  # too low
        assert signal is None

    def test_get_or_levels(self):
        from martin_quant.timing.orb_15m_trigger import ORBTrigger
        df15m = make_15m_df(or_high=105.0, or_low=103.0)
        trigger = ORBTrigger()
        levels = trigger.get_or_levels(df15m)
        assert "or_high" in levels
        assert "or_low" in levels
        assert levels["or_high"] > levels["or_low"]


# ── SectorRegimeFilter Tests ─────────────────────────────────────────────────

class TestSectorRegimeFilter:
    def test_import(self):
        from martin_quant.regime.sector_regime_filter import SectorRegimeFilter
        f = SectorRegimeFilter()
        assert f is not None

    def test_bull_allows_tech(self):
        from martin_quant.regime.sector_regime_filter import SectorRegimeFilter
        f = SectorRegimeFilter()
        assert f.allow("technology", "BULL") is True

    def test_bull_avoids_utilities(self):
        from martin_quant.regime.sector_regime_filter import SectorRegimeFilter
        f = SectorRegimeFilter()
        assert f.allow("utilities", "BULL") is False

    def test_sector_bonus_preferred(self):
        from martin_quant.regime.sector_regime_filter import SectorRegimeFilter
        f = SectorRegimeFilter()
        bonus = f.sector_score_bonus("semiconductors", "BULL")
        assert bonus == 0.15

    def test_sector_bonus_avoid(self):
        from martin_quant.regime.sector_regime_filter import SectorRegimeFilter
        f = SectorRegimeFilter()
        bonus = f.sector_score_bonus("utilities", "BULL")
        assert bonus == -0.30

    def test_filter_watchlist(self):
        from martin_quant.regime.sector_regime_filter import SectorRegimeFilter
        f = SectorRegimeFilter()
        wl = [
            {"symbol": "NVDA", "sector": "semiconductors"},
            {"symbol": "XLU",  "sector": "utilities"},
            {"symbol": "AMZN", "sector": "consumer_discretionary"},
        ]
        allowed = f.filter_watchlist(wl, "BULL")
        symbols = [r.symbol for r in allowed]
        assert "NVDA" in symbols
        assert "AMZN" in symbols
        assert "XLU" not in symbols

    def test_bear_avoids_all(self):
        from martin_quant.regime.sector_regime_filter import SectorRegimeFilter
        f = SectorRegimeFilter()
        assert f.allow("technology", "BEAR") is False
        assert f.allow("healthcare", "BEAR") is False


# ── DailyScannerV2 Tests ──────────────────────────────────────────────────────

class TestDailyScannerV2:
    def test_import(self):
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2
        scanner = DailyScannerV2(equity=100_000)
        assert scanner is not None

    def test_scan_returns_list(self):
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2
        scanner = DailyScannerV2(equity=100_000)
        data = {
            "NVDA": make_daily_df(120, trend="up"),
            "AMD":  make_daily_df(120, trend="up"),
        }
        results = scanner.scan(
            watchlist_data=data,
            regime="BULL",
            watchlist_sectors={"NVDA": "semiconductors", "AMD": "semiconductors"},
            watchlist_setup_scores={
                "NVDA": {"score": 0.75, "type": "pullback"},
                "AMD":  {"score": 0.68, "type": "breakout"},
            },
        )
        assert isinstance(results, list)
        # Both should pass (tech in BULL is preferred)
        assert len(results) >= 1
        # Sorted by total_score desc
        if len(results) > 1:
            assert results[0].total_score >= results[1].total_score

    def test_bear_regime_blocks_all(self):
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2
        scanner = DailyScannerV2(equity=100_000)
        data = {"NVDA": make_daily_df(120)}
        results = scanner.scan(
            watchlist_data=data,
            regime="BEAR",
            watchlist_sectors={"NVDA": "semiconductors"},
            watchlist_setup_scores={"NVDA": {"score": 0.80, "type": "pullback"}},
        )
        # BEAR regime: sector avoided → no results
        assert results == []

    def test_orb_integration(self):
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2
        scanner = DailyScannerV2(equity=100_000)
        data   = {"NVDA": make_daily_df(120)}
        df15m  = {"NVDA": make_15m_df(breakout=True)}
        results = scanner.scan(
            watchlist_data=data,
            regime="BULL",
            watchlist_sectors={"NVDA": "semiconductors"},
            watchlist_setup_scores={"NVDA": {"score": 0.75, "type": "pullback"}},
            df_15m_map=df15m,
        )
        # If any result returned, ORB should be populated
        if results:
            orb = results[0].orb_signal
            assert orb is not None
            assert orb.r_potential >= 1.5
