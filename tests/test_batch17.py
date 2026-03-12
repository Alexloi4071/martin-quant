"""test_batch17.py — Batch 17 unit tests"""
import pytest
import pandas as pd
import numpy as np
from io import StringIO
from pathlib import Path
import tempfile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CSV = """date,symbol,setup_type,sector,entry,exit_price,shares,pnl_dollars,pnl_pct,r_multiple,outcome,exit_reason,holding_days,regime
2026-03-03,NVDA,pullback,semiconductors,125.0,131.25,100,625,5.0,2.5,win,ema9_exit,3,BULL
2026-03-03,AMD,breakout,semiconductors,95.0,93.1,200,-380,-2.0,-1.0,loss,stop_loss,1,BULL
2026-03-04,MSFT,pullback,technology,415.0,423.3,50,415,2.0,1.5,win,target_hit,2,BULL
2026-03-05,AAPL,breakout,technology,220.0,216.0,80,-320,-1.8,-1.0,loss,stop_loss,1,BULL
2026-03-06,GOOGL,pullback,technology,185.0,194.25,100,925,5.0,3.0,win,ema9_exit,4,WEAK_BULL
2026-03-07,META,avwap_reclaim,technology,590.0,601.8,40,472,2.0,1.2,win,target_hit,2,WEAK_BULL
"""


@pytest.fixture
def tmp_csv(tmp_path):
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(SAMPLE_CSV)
    return str(csv_file)


# ---------------------------------------------------------------------------
# TradeReviewer Tests
# ---------------------------------------------------------------------------

class TestTradeReviewer:
    def test_import(self):
        from martin_quant.review.trade_reviewer import TradeReviewer
        r = TradeReviewer()
        assert r is not None

    def test_load_csv(self, tmp_csv):
        from martin_quant.review.trade_reviewer import TradeReviewer
        r = TradeReviewer(csv_path=tmp_csv)
        df = r.load()
        assert len(df) == 6
        assert "r_multiple" in df.columns
        assert "outcome" in df.columns

    def test_review_week(self, tmp_csv):
        from martin_quant.review.trade_reviewer import TradeReviewer
        r = TradeReviewer(csv_path=tmp_csv)
        result = r.review(
            start_date="2026-03-03",
            end_date="2026-03-07",
        )
        assert result.n_trades == 6
        assert result.n_wins == 4
        assert result.n_losses == 2
        assert abs(result.win_rate - 4/6) < 0.01
        assert result.total_r == pytest.approx(2.5 - 1.0 + 1.5 - 1.0 + 3.0 + 1.2, abs=0.1)

    def test_summary_output(self, tmp_csv):
        from martin_quant.review.trade_reviewer import TradeReviewer
        r  = TradeReviewer(csv_path=tmp_csv)
        result = r.review(start_date="2026-03-03", end_date="2026-03-07")
        summary = result.summary()
        assert "Win %" in summary
        assert "Avg R" in summary
        assert "PnL" in summary

    def test_by_setup(self, tmp_csv):
        from martin_quant.review.trade_reviewer import TradeReviewer
        r = TradeReviewer(csv_path=tmp_csv)
        result = r.review(start_date="2026-03-03", end_date="2026-03-07")
        assert len(result.by_setup) >= 2  # pullback + breakout + avwap_reclaim
        setup_names = [s.setup_type for s in result.by_setup]
        assert "pullback" in setup_names

    def test_by_regime(self, tmp_csv):
        from martin_quant.review.trade_reviewer import TradeReviewer
        r = TradeReviewer(csv_path=tmp_csv)
        result = r.review(start_date="2026-03-03", end_date="2026-03-07")
        assert "BULL" in result.by_regime or "WEAK_BULL" in result.by_regime

    def test_missing_file(self):
        from martin_quant.review.trade_reviewer import TradeReviewer
        r  = TradeReviewer(csv_path="/nonexistent/trades.csv")
        result = r.review()
        assert result.n_trades == 0

    def test_all_time(self, tmp_csv):
        from martin_quant.review.trade_reviewer import TradeReviewer
        r = TradeReviewer(csv_path=tmp_csv)
        result = r.all_time()
        assert result.n_trades == 6


# ---------------------------------------------------------------------------
# WeeklyReport Tests
# ---------------------------------------------------------------------------

class TestWeeklyReport:
    def test_import(self):
        from martin_quant.review.weekly_report import WeeklyReport
        w = WeeklyReport()
        assert w is not None

    def test_generate(self, tmp_csv, capsys):
        from martin_quant.review.weekly_report import WeeklyReport
        w = WeeklyReport(csv_path=tmp_csv)
        result = w.generate(
            start_date="2026-03-03",
            end_date="2026-03-07",
            print_report=True,
        )
        captured = capsys.readouterr()
        assert "Win %" in captured.out
        assert result.n_trades == 6

    def test_save_markdown(self, tmp_csv, tmp_path):
        from martin_quant.review.weekly_report import WeeklyReport
        w = WeeklyReport(csv_path=tmp_csv, output_dir=str(tmp_path / "reports"))
        path = w.save_markdown(
            start_date="2026-03-03",
            end_date="2026-03-07",
        )
        assert path.exists()
        content = path.read_text()
        assert "# Martin Quant Weekly Report" in content
        assert "| Win Rate |" in content
        assert "| Metric |" in content

    def test_markdown_has_setup_table(self, tmp_csv, tmp_path):
        from martin_quant.review.weekly_report import WeeklyReport
        w = WeeklyReport(csv_path=tmp_csv, output_dir=str(tmp_path / "reports"))
        path = w.save_markdown(start_date="2026-03-03", end_date="2026-03-07")
        content = path.read_text()
        assert "By Setup Type" in content


# ---------------------------------------------------------------------------
# DataPipeline Tests
# ---------------------------------------------------------------------------

class TestDataPipeline:
    def test_import(self):
        from martin_quant.pipeline.data_pipeline import DataPipeline
        p = DataPipeline()
        assert p is not None

    def test_get_sectors(self):
        from martin_quant.pipeline.data_pipeline import DataPipeline, DEFAULT_SECTOR_MAP
        p = DataPipeline()
        sectors = p.get_sectors(["NVDA", "AMD", "JPM"])
        assert sectors["NVDA"] == "semiconductors"
        assert sectors["AMD"]  == "semiconductors"
        assert sectors["JPM"]  == "financials"

    def test_default_sector_map(self):
        from martin_quant.pipeline.data_pipeline import DEFAULT_SECTOR_MAP
        assert "NVDA" in DEFAULT_SECTOR_MAP
        assert "MSFT" in DEFAULT_SECTOR_MAP
        assert DEFAULT_SECTOR_MAP["NVDA"] == "semiconductors"


# ---------------------------------------------------------------------------
# CLI Tests
# ---------------------------------------------------------------------------

class TestCLI:
    def test_import(self):
        from martin_quant.cli.main import build_parser, main
        parser = build_parser()
        assert parser is not None

    def test_parser_commands(self):
        from martin_quant.cli.main import build_parser
        parser = build_parser()
        # Test review command parses correctly
        args = parser.parse_args(["review", "--weeks", "2", "--csv", "trades.csv"])
        assert args.command == "review"
        assert args.weeks == 2
        assert args.csv == "trades.csv"

    def test_parser_scan_v2(self):
        from martin_quant.cli.main import build_parser
        parser = build_parser()
        args = parser.parse_args(["scan-v2", "--regime", "CHOPPY", "--no-alerts"])
        assert args.command == "scan-v2"
        assert args.regime == "CHOPPY"
        assert args.no_alerts is True

    def test_parser_sectors(self):
        from martin_quant.cli.main import build_parser
        parser = build_parser()
        args = parser.parse_args(["sectors", "--regime", "BULL"])
        assert args.regime == "BULL"

    def test_cmd_review_with_csv(self, tmp_csv):
        from martin_quant.cli.main import cmd_review
        import argparse
        args = argparse.Namespace(
            weeks=1,
            csv=tmp_csv,
            verbose=False,
        )
        # Should not raise; returns int exit code
        code = cmd_review(args)
        assert code == 0

    def test_cmd_sectors(self):
        from martin_quant.cli.main import cmd_sectors
        import argparse
        args = argparse.Namespace(regime="BULL", verbose=False)
        code = cmd_sectors(args)
        assert code == 0
