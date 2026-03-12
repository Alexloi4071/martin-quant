"""
Batch 19 Smoke Tests
Tests for: PullbackScanner, LeaderScanner, PositionSizer, MultiTFConfirm, PartialExitManager
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def make_trending_df(n=120, start_price=100.0, trend=0.003) -> pd.DataFrame:
    """Generate a fake uptrending daily OHLCV DataFrame."""
    dates = pd.date_range(end=datetime.today(), periods=n, freq="B")
    closes = [start_price * (1 + trend) ** i for i in range(n)]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    opens = [c * 0.995 for c in closes]
    volumes = [1_000_000 * (0.5 + np.random.rand()) for _ in range(n)]
    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )
    return df


def make_pullback_df(n=120) -> pd.DataFrame:
    """Trending up, then small 5% pullback to EMA9 area."""
    df = make_trending_df(n=n - 5)
    # Add 5 bars pulling back ~5% from high
    last_close = df["close"].iloc[-1]
    pullback_closes = [last_close * (1 - 0.01 * i) for i in range(1, 6)]
    dates = pd.date_range(start=df.index[-1] + timedelta(days=1), periods=5, freq="B")
    pullback_df = pd.DataFrame(
        {
            "open": [c * 1.002 for c in pullback_closes],
            "high": [c * 1.008 for c in pullback_closes],
            "low": [c * 0.992 for c in pullback_closes],
            "close": pullback_closes,
            "volume": [400_000] * 5,   # Dry volume on pullback
        },
        index=dates,
    )
    return pd.concat([df, pullback_df])


# -----------------------------------------------------------------------
# PullbackScanner
# -----------------------------------------------------------------------

class TestPullbackScanner:
    def test_import(self):
        from martin_quant.scanners.pullback_scanner import PullbackScanner, PullbackConfig
        scanner = PullbackScanner()
        assert scanner.config is not None

    def test_scan_symbol_returns_none_for_flat_stock(self):
        from martin_quant.scanners.pullback_scanner import PullbackScanner
        scanner = PullbackScanner()
        flat_df = make_trending_df(n=120, trend=0.0001)   # Almost flat
        result = scanner.scan_symbol("FLAT", flat_df)
        assert result is None   # Flat stock should not qualify

    def test_scan_universe_returns_list(self):
        from martin_quant.scanners.pullback_scanner import PullbackScanner
        scanner = PullbackScanner()
        universe = {
            "TREND1": make_pullback_df(),
            "TREND2": make_pullback_df(),
            "FLAT": make_trending_df(trend=0.0001),
        }
        results = scanner.scan_universe(universe)
        assert isinstance(results, list)

    def test_signal_fields(self):
        from martin_quant.scanners.pullback_scanner import PullbackScanner
        scanner = PullbackScanner()
        df = make_pullback_df()
        sig = scanner.scan_symbol("TEST", df)
        if sig:   # May or may not trigger depending on exact prices
            assert 0 <= sig.score <= 1.0
            assert sig.entry_price > 0
            assert sig.stop_price < sig.entry_price
            assert sig.setup_type in ("ema9_pullback", "ema21_pullback", "avwap_pullback")


# -----------------------------------------------------------------------
# LeaderScanner
# -----------------------------------------------------------------------

class TestLeaderScanner:
    def test_import(self):
        from martin_quant.scanners.leader_scanner import LeaderScanner
        scanner = LeaderScanner()
        assert scanner is not None

    def test_build_leader_list_returns_tuple(self):
        from martin_quant.scanners.leader_scanner import LeaderScanner
        scanner = LeaderScanner()
        universe = {f"SYM{i}": make_trending_df(n=260, trend=0.002 * i) for i in range(1, 6)}
        leaders, health = scanner.build_leader_list(universe)
        assert isinstance(leaders, list)
        assert health is not None
        assert health.health_label in ("STRONG", "MODERATE", "WEAK", "VERY_WEAK")
        assert 0 < health.exposure_factor <= 1.0

    def test_health_exposure_factor_range(self):
        from martin_quant.scanners.leader_scanner import LeaderScanner, LeaderConfig
        cfg = LeaderConfig(strong_threshold=3, moderate_threshold=2, weak_threshold=1)
        scanner = LeaderScanner(config=cfg)
        universe = {f"SYM{i}": make_trending_df(n=260, trend=0.003 * i) for i in range(1, 8)}
        _, health = scanner.build_leader_list(universe)
        assert health.exposure_factor in (0.30, 0.50, 0.75, 0.85, 1.0)


# -----------------------------------------------------------------------
# PositionSizer
# -----------------------------------------------------------------------

class TestPositionSizer:
    def test_import(self):
        from martin_quant.risk.position_sizer import PositionSizer
        sizer = PositionSizer()
        assert sizer is not None

    def test_basic_sizing(self):
        from martin_quant.risk.position_sizer import PositionSizer
        sizer = PositionSizer()
        result = sizer.size(
            symbol="NVDA",
            entry_price=500.0,
            stop_price=492.5,    # 1.5% stop
            equity=100_000,
            regime="BULL",
        )
        assert result is not None
        assert result.shares >= 1
        # 0.5% risk / 1.5% stop = 33% → ~$33k position
        assert 0.25 <= result.position_pct <= 0.40
        assert result.risk_pct <= 0.01   # Never risk > 1%

    def test_small_cap_cap(self):
        from martin_quant.risk.position_sizer import PositionSizer
        sizer = PositionSizer()
        result = sizer.size(
            symbol="SMALL",
            entry_price=10.0,
            stop_price=9.5,
            equity=100_000,
            market_cap=300_000_000,   # Micro cap
        )
        assert result is not None
        assert result.position_pct <= 0.11   # Hard cap at micro cap

    def test_invalid_stop_returns_none(self):
        from martin_quant.risk.position_sizer import PositionSizer
        sizer = PositionSizer()
        result = sizer.size("BAD", 100.0, 105.0, 100_000)   # Stop above entry
        assert result is None

    def test_portfolio_sizing(self):
        from martin_quant.risk.position_sizer import PositionSizer
        sizer = PositionSizer()
        candidates = [
            {"symbol": f"S{i}", "entry": 100.0, "stop": 98.5}
            for i in range(10)
        ]
        results = sizer.size_portfolio(candidates, equity=100_000)
        assert len(results) <= sizer.config.max_positions


# -----------------------------------------------------------------------
# MultiTFConfirm
# -----------------------------------------------------------------------

class TestMultiTFConfirm:
    def test_import(self):
        from martin_quant.entry.multi_tf_confirm import MultiTFConfirm
        mtf = MultiTFConfirm()
        assert mtf is not None

    def test_confirm_daily_only(self):
        from martin_quant.entry.multi_tf_confirm import MultiTFConfirm
        mtf = MultiTFConfirm()
        daily = make_trending_df(n=60)
        result = mtf.confirm("TEST", daily_df=daily)
        # With only daily data, should return a result with confidence
        assert result is not None
        assert 0 <= result.confidence <= 1.0

    def test_str_output(self):
        from martin_quant.entry.multi_tf_confirm import MultiTFConfirm
        mtf = MultiTFConfirm()
        daily = make_trending_df(n=60)
        result = mtf.confirm("NVDA", daily_df=daily)
        s = str(result)
        assert "NVDA" in s
        assert "D=" in s


# -----------------------------------------------------------------------
# PartialExitManager
# -----------------------------------------------------------------------

class TestPartialExitManager:
    def test_import(self):
        from martin_quant.exit.partial_exit import PartialExitManager, ExitAction
        mgr = PartialExitManager()
        assert mgr is not None

    def test_hold_at_1r(self):
        from martin_quant.exit.partial_exit import PartialExitManager, ExitAction
        mgr = PartialExitManager()
        # 1R up → should HOLD in bull market
        result = mgr.evaluate("NVDA", entry_price=100, stop_price=98, current_price=102, regime="BULL")
        assert result.action == ExitAction.HOLD

    def test_partial_at_3r(self):
        from martin_quant.exit.partial_exit import PartialExitManager, ExitAction
        mgr = PartialExitManager()
        # 3R up → partial exit
        result = mgr.evaluate("NVDA", entry_price=100, stop_price=98, current_price=106)
        assert result.action in (ExitAction.PARTIAL_15, ExitAction.PARTIAL_10)
        assert result.sell_pct > 0
        assert result.new_stop is not None   # Stop moved to BE+

    def test_full_exit_at_stop(self):
        from martin_quant.exit.partial_exit import PartialExitManager, ExitAction
        mgr = PartialExitManager()
        result = mgr.evaluate("NVDA", entry_price=100, stop_price=98, current_price=97.5)
        assert result.action == ExitAction.FULL_EXIT
        assert result.sell_pct == 1.0
        assert "hit_stop" in result.reason

    def test_gap_down_immediate_exit(self):
        from martin_quant.exit.partial_exit import PartialExitManager, ExitAction
        mgr = PartialExitManager()
        result = mgr.evaluate(
            "NVDA", entry_price=100, stop_price=98, current_price=100,
            premarket_price=96.0   # 4% gap down
        )
        assert result.action == ExitAction.FULL_EXIT
        assert result.urgency == "immediate"

    def test_total_sold_pct_tracks(self):
        from martin_quant.exit.partial_exit import PartialExitManager
        mgr = PartialExitManager()
        # First partial at 3R
        mgr.evaluate("NVDA", 100, 98, 106)
        # Second partial at 5R
        mgr.evaluate("NVDA", 100, 98, 110)
        sold = mgr.get_total_sold_pct("NVDA")
        assert sold > 0.10   # At least 10% sold
        assert sold < 1.0    # Not fully sold
