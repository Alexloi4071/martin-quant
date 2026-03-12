"""test_batch18.py — Batch 18 smoke tests"""
import pytest
from pathlib import Path


class TestProjectStructure:
    """Verify all key modules are importable after Batch 18"""

    def test_cli_import(self):
        from martin_quant.cli.main import main, build_parser
        assert callable(main)

    def test_scanner_import(self):
        from martin_quant.scanner import DailyScannerV2
        assert DailyScannerV2 is not None

    def test_review_import(self):
        from martin_quant.review import TradeReviewer, WeeklyReport
        assert TradeReviewer is not None
        assert WeeklyReport is not None

    def test_pipeline_import(self):
        from martin_quant.pipeline import DataPipeline
        assert DataPipeline is not None

    def test_pipeline_sector_map(self):
        from martin_quant.pipeline.data_pipeline import DEFAULT_SECTOR_MAP
        assert "NVDA" in DEFAULT_SECTOR_MAP
        assert DEFAULT_SECTOR_MAP["NVDA"] == "semiconductors"

    def test_run_scan_v2_import(self):
        from martin_quant.scripts.run_daily_scan_v2 import run_scan_v2
        assert callable(run_scan_v2)


class TestEnvExample:
    def test_env_example_exists(self):
        p = Path(".env.example")
        # In CI, check relative to repo root
        candidates = [
            Path(".env.example"),
            Path("../../.env.example"),
            Path("../../../.env.example"),
        ]
        found = any(c.exists() for c in candidates)
        assert found, ".env.example not found"


class TestChangeLog:
    def test_changelog_exists(self):
        candidates = [
            Path("CHANGELOG.md"),
            Path("../../CHANGELOG.md"),
            Path("../../../CHANGELOG.md"),
        ]
        found = any(c.exists() for c in candidates)
        assert found, "CHANGELOG.md not found"


class TestMakefile:
    def test_makefile_exists(self):
        candidates = [
            Path("Makefile"),
            Path("../../Makefile"),
            Path("../../../Makefile"),
        ]
        found = any(c.exists() for c in candidates)
        assert found, "Makefile not found"


class TestCLIParser:
    def test_all_commands_registered(self):
        from martin_quant.cli.main import COMMANDS
        expected = {"scan", "scan-v2", "review", "report", "regime", "sectors", "orb"}
        assert expected == set(COMMANDS.keys())

    def test_review_args(self):
        from martin_quant.cli.main import build_parser
        p    = build_parser()
        args = p.parse_args(["review", "--weeks", "4"])
        assert args.weeks == 4

    def test_report_telegram_flag(self):
        from martin_quant.cli.main import build_parser
        p    = build_parser()
        args = p.parse_args(["report", "--telegram"])
        assert args.telegram is True

    def test_scan_v2_defaults(self):
        from martin_quant.cli.main import build_parser
        p    = build_parser()
        args = p.parse_args(["scan-v2"])
        assert args.regime == "BULL"
        assert args.no_alerts is False
