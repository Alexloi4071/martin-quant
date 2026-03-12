"""test_batch20.py — Unit tests for Batch 20 modules."""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ohlcv_df() -> pd.DataFrame:
    """400-bar synthetic OHLCV with realistic price action."""
    np.random.seed(42)
    n = 400
    close = 100 * np.cumprod(1 + np.random.normal(0.0005, 0.015, n))
    high  = close * (1 + np.abs(np.random.normal(0, 0.008, n)))
    low   = close * (1 - np.abs(np.random.normal(0, 0.008, n)))
    opn   = close * (1 + np.random.normal(0, 0.005, n))
    vol   = np.random.randint(500_000, 5_000_000, n).astype(float)
    idx   = pd.date_range(end=date.today(), periods=n, freq="B")
    return pd.DataFrame(
        {"open": opn, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


@pytest.fixture
def sample_trades_csv(tmp_path) -> str:
    """Create a minimal trades.csv for WeeklyReviewer tests."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    rows = [
        "date,symbol,setup_type,direction,entry_price,exit_price,stop_price,"
        "shares,pnl_dollars,r_realized,exit_reason,regime,score,notes\n",
    ]
    test_cases = [
        (monday,       "NVDA", "pullback", "long",  500.0, 520.0, 492.0, 10,  2000.0,  2.5,  "profit_target", "BULL", 0.85, ""),
        (monday + timedelta(1), "AMD",  "breakout", "long",  120.0, 118.0, 116.5, 20, -300.0, -0.8, "stop_hit",     "BULL", 0.72, ""),
        (monday + timedelta(2), "META", "eps",      "long",  450.0, 475.0, 440.0,  5,  1250.0,  2.5,  "profit_target", "BULL", 0.91, ""),
        (monday + timedelta(3), "TSLA", "pullback", "long",  200.0, 199.5, 196.0,  8,   -40.0, -0.1, "breakeven",    "CAUTION", 0.78, ""),
    ]
    for dt, sym, setup, dirn, entry, exit_, stop, sh, pnl, r, reason, regime, score, notes in test_cases:
        rows.append(
            f"{dt},{sym},{setup},{dirn},{entry},{exit_},{stop},{sh},{pnl},{r},{reason},{regime},{score},{notes}\n"
        )
    csv_path = tmp_path / "trades.csv"
    csv_path.write_text("".join(rows))
    return str(csv_path)


# ---------------------------------------------------------------------------
# AVWAPAnchorManager tests
# ---------------------------------------------------------------------------

class TestAVWAPAnchorManager:

    def test_basic_compute(self, ohlcv_df):
        from martin_quant.anchors import AVWAPAnchorManager
        mgr = AVWAPAnchorManager()
        result = mgr.compute("TEST", ohlcv_df)
        assert result.symbol == "TEST"
        assert result.current_price > 0
        assert len(result.avwap_lines) >= 1   # at least swing_low or base detected

    def test_eps_anchor(self, ohlcv_df):
        from martin_quant.anchors import AVWAPAnchorManager
        # Use a date 100 bars ago as EPS anchor
        anchor_date = str(ohlcv_df.index[-100])[:10]
        mgr = AVWAPAnchorManager()
        result = mgr.compute("NVDA", ohlcv_df, {"eps": anchor_date})
        eps_lines = [a for a in result.avwap_lines if a.anchor_type == "eps"]
        assert len(eps_lines) == 1
        assert eps_lines[0].current_value > 0

    def test_support_resistance_classification(self, ohlcv_df):
        from martin_quant.anchors import AVWAPAnchorManager
        mgr = AVWAPAnchorManager()
        result = mgr.compute("TEST", ohlcv_df)
        for line in result.avwap_lines:
            # Support and resistance should be mutually exclusive
            assert not (line.is_support and line.is_resistance)

    def test_batch_compute(self, ohlcv_df):
        from martin_quant.anchors import AVWAPAnchorManager
        mgr = AVWAPAnchorManager()
        results = mgr.batch_compute(["AAA", "BBB", "CCC"], {"AAA": ohlcv_df, "BBB": ohlcv_df})
        assert "AAA" in results
        assert "BBB" in results
        assert "CCC" not in results  # no data for CCC

    def test_score_boost_in_range(self, ohlcv_df):
        from martin_quant.anchors import AVWAPAnchorManager
        mgr = AVWAPAnchorManager()
        result = mgr.compute("TEST", ohlcv_df)
        assert 0.0 <= result.score_boost <= 0.5


# ---------------------------------------------------------------------------
# WeeklyReviewer tests
# ---------------------------------------------------------------------------

class TestWeeklyReviewer:

    def test_load_trades(self, sample_trades_csv):
        from martin_quant.review import WeeklyReviewer
        reviewer = WeeklyReviewer(trades_csv=sample_trades_csv)
        trades = reviewer.load_trades()
        assert len(trades) == 4

    def test_generate_report(self, sample_trades_csv):
        from martin_quant.review import WeeklyReviewer
        reviewer = WeeklyReviewer(trades_csv=sample_trades_csv)
        report = reviewer.generate_weekly_report()
        assert report.total_trades == 4
        assert report.winners >= 2
        assert report.grade in ("A", "B", "C", "D")
        assert "Weekly Review" in report.markdown

    def test_setup_breakdown(self, sample_trades_csv):
        from martin_quant.review import WeeklyReviewer
        reviewer = WeeklyReviewer(trades_csv=sample_trades_csv)
        report = reviewer.generate_weekly_report()
        setup_types = {s.setup_type for s in report.setup_breakdown}
        assert "pullback" in setup_types
        assert "eps" in setup_types

    def test_mistake_detection(self, sample_trades_csv):
        from martin_quant.review import WeeklyReviewer
        reviewer = WeeklyReviewer(trades_csv=sample_trades_csv)
        report = reviewer.generate_weekly_report()
        # TSLA BE exit on high-score setup should trigger a mistake
        assert report.mistake_count >= 1

    def test_save_report(self, sample_trades_csv, tmp_path):
        from martin_quant.review import WeeklyReviewer
        reviewer = WeeklyReviewer(trades_csv=sample_trades_csv)
        report = reviewer.generate_weekly_report()
        path = reviewer.save_report(report, output_dir=str(tmp_path))
        assert (tmp_path / f"weekly_{report.week_end}.md").exists()

    def test_expectancy_calculation(self, sample_trades_csv):
        from martin_quant.review import WeeklyReviewer
        reviewer = WeeklyReviewer(trades_csv=sample_trades_csv)
        report = reviewer.generate_weekly_report()
        # 3 winners (2.5R, 2.5R, -0.1R≈BE) + 1 loser (-0.8R)
        # expectancy should be positive with 2 big wins
        assert report.expectancy > 0


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLI:

    def test_version(self, capsys):
        from martin_quant.cli.main import main
        main(["version"])
        captured = capsys.readouterr()
        assert "martin-quant" in captured.out

    def test_parser_commands(self):
        from martin_quant.cli.main import build_parser
        parser = build_parser()
        # scan command
        args = parser.parse_args(["scan", "--equity", "150000", "--no-alerts"])
        assert args.equity == 150000
        assert args.no_alerts is True
        # review command
        args = parser.parse_args(["review", "--week-end", "2026-03-14"])
        assert args.week_end == "2026-03-14"
        # avwap command
        args = parser.parse_args(["avwap", "NVDA", "--eps", "2025-08-28"])
        assert args.symbol == "NVDA"
        assert args.eps == "2025-08-28"

    def test_review_cli(self, sample_trades_csv, capsys, tmp_path):
        from martin_quant.cli.main import main
        main(["review", "--csv", sample_trades_csv, "--output", str(tmp_path)])
        captured = capsys.readouterr()
        assert "Weekly Review" in captured.out


# ---------------------------------------------------------------------------
# DataPipeline tests (offline / mock)
# ---------------------------------------------------------------------------

class TestDataPipeline:

    def test_build_universe_combined(self):
        from martin_quant.pipeline import DataPipeline
        p = DataPipeline(universe="combined")
        syms = p.build_universe()
        assert "SPY" in syms
        assert "NVDA" in syms
        assert len(syms) > 50

    def test_build_universe_custom(self):
        from martin_quant.pipeline import DataPipeline
        p = DataPipeline(universe="sp500", custom_symbols=["MYSTOCK"])
        syms = p.build_universe()
        assert "MYSTOCK" in syms

    def test_cache_ops(self, tmp_path, ohlcv_df):
        from martin_quant.pipeline import DataPipeline
        p = DataPipeline(cache_dir=str(tmp_path))
        p._save_cache("TESTX", ohlcv_df)
        loaded = p._load_cache("TESTX")
        assert loaded is not None
        assert len(loaded) == len(ohlcv_df)

    def test_pipeline_data_validity(self):
        from martin_quant.pipeline import PipelineData
        import pandas as pd
        d = PipelineData()
        assert not d.is_valid()
        d.spy_df = pd.DataFrame({"close": [1, 2, 3]})
        d.ohlcv_map = {"NVDA": pd.DataFrame({"close": [1]})}
        assert d.is_valid()
