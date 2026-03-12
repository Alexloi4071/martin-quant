"""cli/main.py

Martin Quant CLI — entry point for all command-line operations.

Commands:
    scan        Run daily scan and print/save results
    review      Generate weekly trade review report
    avwap       Compute AVWAP for a symbol
    regime      Check current market regime
    version     Print version info

Usage:
    martin-quant scan --equity 150000 --date 2026-03-13
    martin-quant review --week-end 2026-03-14 --output reports/
    martin-quant avwap NVDA --eps 2025-08-28
    martin-quant regime
    martin-quant version
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_version(args: argparse.Namespace) -> None:
    try:
        from importlib.metadata import version
        v = version("martin-quant")
    except Exception:
        v = "dev"
    print(f"martin-quant {v}")


def cmd_regime(args: argparse.Namespace) -> None:
    """Quick regime check using cached / live SPY + IWM data."""
    try:
        from martin_quant.data.providers import get_provider
        from martin_quant.filters.market_regime import MarketRegimeFilter

        provider = get_provider()
        spy = provider.get_ohlcv("SPY", period="6mo")
        iwm = provider.get_ohlcv("IWM", period="6mo")
        flt = MarketRegimeFilter()
        result = flt.evaluate(spy, iwm)
        print(f"Regime : {result.regime.value}")
        print(f"SPY vs EMA21 : {result.spy_vs_ema21:+.2f}%")
        print(f"IWM vs EMA21 : {result.iwm_vs_ema21:+.2f}%")
        print(f"Above 50D%   : {result.pct_above_50d:.1f}%")
        print(f"New highs    : {result.new_highs}  New lows: {result.new_lows}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_avwap(args: argparse.Namespace) -> None:
    """Compute AVWAP for a symbol with optional anchor dates."""
    try:
        from martin_quant.data.providers import get_provider
        from martin_quant.anchors import AVWAPAnchorManager

        provider = get_provider()
        df = provider.get_ohlcv(args.symbol, period="2y")
        anchors: dict[str, str] = {}
        if args.eps:
            anchors["eps"] = args.eps
        if args.breakout:
            anchors["breakout"] = args.breakout

        mgr = AVWAPAnchorManager()
        result = mgr.compute(args.symbol, df, anchors)
        print(result.summary())
        print(f"\nScore boost for daily scan: +{result.score_boost:.3f}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_review(args: argparse.Namespace) -> None:
    """Generate weekly trade review report."""
    try:
        from martin_quant.review import WeeklyReviewer

        reviewer = WeeklyReviewer(trades_csv=args.csv)
        report   = reviewer.generate_weekly_report(
            week_end=args.week_end,
            extra_notes=args.notes or "",
        )
        print(report.markdown)
        if args.output:
            path = reviewer.save_report(report, output_dir=args.output)
            print(f"\nSaved: {path}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_scan(args: argparse.Namespace) -> None:
    """Run daily scan."""
    try:
        from martin_quant.scripts.run_daily_scan import run_scan
        run_scan(
            equity=args.equity,
            date_str=args.date,
            no_alerts=args.no_alerts,
            output_dir=args.output or ".",
        )
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="martin-quant",
        description="Martin Luk Strategy — Quantitative Trading System",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # version
    sub.add_parser("version", help="Print version")

    # regime
    sub.add_parser("regime", help="Check current market regime")

    # avwap
    p_avwap = sub.add_parser("avwap", help="Compute AVWAP for a symbol")
    p_avwap.add_argument("symbol", type=str)
    p_avwap.add_argument("--eps",      type=str, default=None, help="EPS anchor date YYYY-MM-DD")
    p_avwap.add_argument("--breakout", type=str, default=None, help="Breakout anchor date")

    # review
    p_rev = sub.add_parser("review", help="Generate weekly trade review")
    p_rev.add_argument("--week-end", type=str, default=None,
                       help="Week end date YYYY-MM-DD (default: today)")
    p_rev.add_argument("--csv",    type=str, default="data/trades.csv")
    p_rev.add_argument("--output", type=str, default="reports/")
    p_rev.add_argument("--notes",  type=str, default=None)

    # scan
    p_scan = sub.add_parser("scan", help="Run daily scan")
    p_scan.add_argument("--equity",    type=float, default=100_000)
    p_scan.add_argument("--date",      type=str,   default=None)
    p_scan.add_argument("--no-alerts", action="store_true")
    p_scan.add_argument("--output",    type=str,   default=".")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = build_parser()
    args   = parser.parse_args(argv)

    dispatch = {
        "version": cmd_version,
        "regime":  cmd_regime,
        "avwap":   cmd_avwap,
        "review":  cmd_review,
        "scan":    cmd_scan,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
