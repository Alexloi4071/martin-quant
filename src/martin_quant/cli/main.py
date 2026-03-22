"""Martin Quant CLI entry point."""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Callable, Optional

log = logging.getLogger(__name__)


def cmd_version(args: argparse.Namespace) -> int:
    try:
        from importlib.metadata import version

        value = version("martin-quant")
    except Exception:
        value = "dev"
    print(f"martin-quant {value}")
    return 0


def cmd_regime(args: argparse.Namespace) -> int:
    try:
        from martin_quant.data.providers import get_provider
        from martin_quant.filters.market_regime import MarketRegimeFilter

        provider = get_provider()
        spy = provider.get_ohlcv("SPY", period="6mo")
        iwm = provider.get_ohlcv("IWM", period="6mo")
        result = MarketRegimeFilter().evaluate(spy, iwm)
        print(f"Regime : {result.regime.value}")
        print(f"SPY vs EMA21 : {result.spy_vs_ema21:+.2f}%")
        print(f"IWM vs EMA21 : {result.iwm_vs_ema21:+.2f}%")
        print(f"Above 50D%   : {result.pct_above_50d:.1f}%")
        print(f"New highs    : {result.new_highs}  New lows: {result.new_lows}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


def cmd_avwap(args: argparse.Namespace) -> int:
    try:
        from martin_quant.anchors import AVWAPAnchorManager
        from martin_quant.data.providers import get_provider

        provider = get_provider()
        df = provider.get_ohlcv(args.symbol, period="2y")
        anchors: dict[str, str] = {}
        if args.eps:
            anchors["eps"] = args.eps
        if args.breakout:
            anchors["breakout"] = args.breakout

        result = AVWAPAnchorManager().compute(args.symbol, df, anchors)
        print(result.summary())
        print(f"\nScore boost for daily scan: +{result.score_boost:.3f}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


def cmd_review(args: argparse.Namespace) -> int:
    try:
        csv_path = getattr(args, "csv", "data/trades.csv")
        week_end = getattr(args, "week_end", None)
        notes = getattr(args, "notes", None) or ""
        output = getattr(args, "output", None)

        if week_end is not None or output is not None or notes:
            from martin_quant.review import WeeklyReviewer

            reviewer = WeeklyReviewer(trades_csv=csv_path)
            report = reviewer.generate_weekly_report(
                week_end=week_end,
                extra_notes=notes,
            )
            print(report.markdown)
            if output:
                path = reviewer.save_report(report, output_dir=output)
                print(f"\nSaved: {path}")
            return 0

        from martin_quant.review.weekly_report import WeeklyReport

        result = WeeklyReport(csv_path=csv_path).generate(
            weeks=getattr(args, "weeks", 1),
            print_report=True,
        )
        return 0 if result is not None else 1
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


def cmd_report(args: argparse.Namespace) -> int:
    return cmd_review(args)


def cmd_scan(args: argparse.Namespace) -> int:
    try:
        from martin_quant.scripts.run_daily_scan import run_scan

        run_scan(
            equity=args.equity,
            date_str=args.date,
            no_alerts=args.no_alerts,
            output_dir=args.output or ".",
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


def cmd_scan_v2(args: argparse.Namespace) -> int:
    try:
        from martin_quant.scripts.run_daily_scan_v2 import run_scan_v2

        symbols = [item.strip().upper() for item in args.symbols.split(",")] if args.symbols else None
        run_scan_v2(
            send_alerts=not args.no_alerts,
            regime_override=args.regime,
            symbols=symbols,
            equity=args.equity,
            export_candidates=not args.no_candidate_export,
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


def cmd_live_v2(args: argparse.Namespace) -> int:
    try:
        from martin_quant.scripts.run_live_v2 import run_live_v2

        symbols = [item.strip().upper() for item in args.symbols.split(",")] if args.symbols else None
        run_live_v2(
            paper=not args.live,
            equity=args.equity,
            max_signals=args.max_signals,
            dry_run=args.dry_run,
            use_limit_entry=not args.market,
            monitor_interval=args.interval,
            force_refresh=args.force_refresh,
            symbols=symbols,
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


def cmd_serve_webhook(args: argparse.Namespace) -> int:
    try:
        from martin_quant.signals import WebhookRequestProcessor, run_webhook_server

        processor = WebhookRequestProcessor(
            journal_dir=args.journal_dir,
            shared_secret=args.secret or "",
            send_telegram=not args.no_telegram,
        )
        run_webhook_server(host=args.host, port=args.port, processor=processor)
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


def cmd_sectors(args: argparse.Namespace) -> int:
    regime = getattr(args, "regime", "BULL")
    print(f"Sector diagnostics placeholder | regime={regime}")
    return 0


def cmd_not_implemented(args: argparse.Namespace) -> int:
    print(f"Command '{args.command}' is not implemented yet.")
    return 0


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "scan": cmd_scan,
    "scan-v2": cmd_scan_v2,
    "review": cmd_review,
    "report": cmd_report,
    "regime": cmd_regime,
    "sectors": cmd_sectors,
    "orb": cmd_not_implemented,
}

EXTRA_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "serve-webhook": cmd_serve_webhook,
    "live-v2": cmd_live_v2,
    "avwap": cmd_avwap,
    "version": cmd_version,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="martin-quant",
        description="Martin Luk Strategy Quantitative Trading System",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Print version")
    sub.add_parser("regime", help="Check current market regime")
    p_sectors = sub.add_parser("sectors", help="Sector diagnostics")
    p_sectors.add_argument("--regime", type=str, default="BULL")
    sub.add_parser("orb", help="ORB diagnostics (stub)")

    p_avwap = sub.add_parser("avwap", help="Compute AVWAP for a symbol")
    p_avwap.add_argument("symbol", type=str)
    p_avwap.add_argument("--eps", type=str, default=None, help="EPS anchor date YYYY-MM-DD")
    p_avwap.add_argument("--breakout", type=str, default=None, help="Breakout anchor date")

    p_review = sub.add_parser("review", help="Generate weekly trade review")
    p_review.add_argument("--week-end", type=str, default=None, help="Week end date YYYY-MM-DD")
    p_review.add_argument("--weeks", type=int, default=1, help="Review window in weeks")
    p_review.add_argument("--csv", type=str, default="data/trades.csv")
    p_review.add_argument("--output", type=str, default="reports/")
    p_review.add_argument("--notes", type=str, default=None)

    p_report = sub.add_parser("report", help="Alias for review report generation")
    p_report.add_argument("--week-end", type=str, default=None)
    p_report.add_argument("--weeks", type=int, default=1)
    p_report.add_argument("--csv", type=str, default="data/trades.csv")
    p_report.add_argument("--output", type=str, default="reports/")
    p_report.add_argument("--notes", type=str, default=None)
    p_report.add_argument("--telegram", action="store_true")

    p_scan = sub.add_parser("scan", help="Run legacy daily scan")
    p_scan.add_argument("--equity", type=float, default=100_000)
    p_scan.add_argument("--date", type=str, default=None)
    p_scan.add_argument("--no-alerts", action="store_true")
    p_scan.add_argument("--output", type=str, default=".")

    p_scan_v2 = sub.add_parser("scan-v2", help="Run direction-aware V2 scan")
    p_scan_v2.add_argument("--equity", type=float, default=100_000)
    p_scan_v2.add_argument("--regime", type=str, default="BULL")
    p_scan_v2.add_argument("--symbols", type=str, default=None)
    p_scan_v2.add_argument("--no-alerts", action="store_true")
    p_scan_v2.add_argument("--no-candidate-export", action="store_true")

    p_live_v2 = sub.add_parser("live-v2", help="Run live/paper broker flow on scan-v2 execution plans")
    mode_live = p_live_v2.add_mutually_exclusive_group()
    mode_live.add_argument("--paper", action="store_true", default=True)
    mode_live.add_argument("--live", action="store_true", default=False)
    p_live_v2.add_argument("--equity", type=float, default=100_000)
    p_live_v2.add_argument("--max-signals", type=int, default=5)
    p_live_v2.add_argument("--dry-run", action="store_true")
    p_live_v2.add_argument("--limit", action="store_true", default=True)
    p_live_v2.add_argument("--market", action="store_true")
    p_live_v2.add_argument("--interval", type=float, default=60.0)
    p_live_v2.add_argument("--force-refresh", action="store_true")
    p_live_v2.add_argument("--symbols", type=str, default=None)

    p_webhook = sub.add_parser("serve-webhook", help="Run local webhook receiver")
    p_webhook.add_argument("--host", type=str, default="127.0.0.1")
    p_webhook.add_argument("--port", type=int, default=8787)
    p_webhook.add_argument("--secret", type=str, default="")
    p_webhook.add_argument("--journal-dir", type=str, default="outputs/signals")
    p_webhook.add_argument("--no-telegram", action="store_true")

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {**COMMANDS, **EXTRA_COMMANDS}
    code = handlers[args.command](args)
    if isinstance(code, int) and code != 0:
        sys.exit(code)


if __name__ == "__main__":
    main()
