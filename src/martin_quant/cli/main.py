"""cli/main.py

Martin Quant CLI
================
`martin-scan` 命令行入口。

命令列表:
  martin-scan run          — 執行每日掃描
  martin-scan status       — 顯示持倉狀態 + 出場評估
  martin-scan review       — 產生週報
  martin-scan watchlist    — 更新 watchlist
  martin-scan backtest     — 跑回測 (尚未實作)

Usage:
  pip install -e .
  martin-scan run
  martin-scan run --equity 200000 --no-alerts
  martin-scan status
  martin-scan review --weeks 4
  martin-scan watchlist --update
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    """執行每日掃描並輸出結果"""
    _setup_logging(args.verbose)
    log.info("Martin Quant — Daily Scan Starting...")

    try:
        # Load .env
        _load_dotenv()

        equity = args.equity or float(os.getenv("EQUITY", "100000"))
        send_alerts = not args.no_alerts

        log.info("Equity: $%.0f | Alerts: %s", equity, send_alerts)

        # --- Import pipeline ---
        from martin_quant.pipeline.data_pipeline import DataPipeline
        from martin_quant.daily_scan import DailyScanner, DailyScanConfig
        from martin_quant.utils.alert_manager import AlertManager
        from martin_quant.utils.trade_logger import TradeLogger
        from martin_quant.risk.exit_manager import ExitManager, Position

        pipeline = DataPipeline()
        scanner  = DailyScanner(config=DailyScanConfig(equity=equity))
        alert    = AlertManager()
        logger   = TradeLogger(filepath=args.trades_file)
        exit_mgr = ExitManager()

        # --- Fetch data ---
        log.info("Step 1/3: Fetching market data...")
        data = pipeline.fetch_all()

        # --- Run scan ---
        log.info("Step 2/3: Running DailyScanner...")
        result = scanner.run(
            spy_df=data.spy_df,
            iwm_df=data.iwm_df,
            ohlcv_map=data.ohlcv_map,
            metadata=data.metadata,
            premarket_prices=data.premarket_prices,
            eps_catalyst_set=data.eps_catalyst_set,
        )

        # --- Print summary ---
        print("\n" + result.summary())

        # --- Save CSV ---
        import datetime
        out_file = f"scan_{datetime.date.today()}.csv"
        df = result.to_dataframe()
        if not df.empty:
            df.to_csv(out_file, index=False)
            log.info("Saved: %s (%d signals)", out_file, len(df))

        # --- Exit signals for open positions ---
        log.info("Step 3/3: Evaluating open positions...")
        open_trades = logger.get_open_trades()
        if open_trades:
            exit_signals = []
            for t in open_trades:
                sym = t["symbol"]
                df_sym = data.ohlcv_map.get(sym)
                price  = data.ohlcv_map.get(sym, {"close": t["entry_price"]})
                if df_sym is not None and not df_sym.empty:
                    cur_price = float(df_sym["close"].iloc[-1])
                    pos = Position(
                        symbol=sym,
                        entry_price=float(t["entry_price"]),
                        stop_price=float(t["stop_price"]),
                        target_price=float(t["target_price"]),
                        shares=int(float(t["shares"])),
                        entry_date=t["entry_date"],
                        direction=t.get("direction", "long"),
                        partial_taken=t.get("status") == "partial",
                    )
                    sig = exit_mgr.evaluate(pos, df_sym, cur_price)
                    if sig.should_exit:
                        exit_signals.append(sig)
                        print(f"\n🚨 EXIT: {sig.symbol} — {sig.exit_type} ({int(sig.exit_pct*100)}%)")
                        print(f"   Reason: {sig.reason}")
                        if send_alerts:
                            alert.send_exit_signal(sig)

        # --- Telegram scan result ---
        if send_alerts and result.signals:
            alert.send_scan_result(result)
            log.info("Telegram alert sent.")

        log.info("Done.")
        return 0

    except ImportError as e:
        log.error("Import error: %s", e)
        log.error("Run: pip install -e . yfinance")
        return 1
    except Exception as e:
        log.error("Scan failed: %s", e, exc_info=args.verbose)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """顯示持倉狀態"""
    _setup_logging(args.verbose)
    _load_dotenv()
    from martin_quant.utils.trade_logger import TradeLogger
    logger = TradeLogger(filepath=args.trades_file)
    stats  = logger.get_stats()
    open_t = logger.get_open_trades()

    print("\n=== Martin Quant Portfolio Status ===")
    print(f"Open Positions : {stats.get('open_trades', 0)}")
    print(f"Closed Trades  : {stats.get('total_trades', 0)}")
    if stats.get("total_trades", 0) > 0:
        print(f"Win Rate       : {stats['win_rate_pct']}%")
        print(f"Total R        : {stats['total_r']:+.1f}R")
        print(f"Profit Factor  : {stats['profit_factor']}")
        print(f"Total PnL      : ${stats['total_pnl_$']:,.0f}")

    if open_t:
        print("\nOpen Positions:")
        for t in open_t:
            print(f"  {t['symbol']:6s} {t['setup_type']:12s} "
                  f"entry=${float(t['entry_price']):.2f} "
                  f"stop=${float(t['stop_price']):.2f} "
                  f"({t['entry_date']})")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """產生週報"""
    _setup_logging(args.verbose)
    _load_dotenv()
    from martin_quant.review.weekly_report import WeeklyReport
    report = WeeklyReport(trades_file=args.trades_file)
    report.print_report(weeks=args.weeks)
    if args.save:
        path = report.save_report(weeks=args.weeks)
        print(f"\nReport saved: {path}")
    return 0


def cmd_watchlist(args: argparse.Namespace) -> int:
    """更新 watchlist"""
    _setup_logging(args.verbose)
    _load_dotenv()
    if args.update:
        from martin_quant.pipeline.watchlist_updater import WatchlistUpdater
        updater = WatchlistUpdater()
        symbols = updater.update()
        print(f"Watchlist updated: {len(symbols)} symbols")
        print(", ".join(symbols[:20]), "..." if len(symbols) > 20 else "")
    else:
        from martin_quant.pipeline.watchlist_updater import WatchlistUpdater
        symbols = WatchlistUpdater().load()
        print(f"Current watchlist: {len(symbols)} symbols")
        print(", ".join(symbols[:30]))
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="martin-scan",
        description="Martin Quant — Pullback Strategy Scanner",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--trades-file", default="trades.csv",
        help="Path to trades CSV (default: trades.csv)",
    )

    sub = parser.add_subparsers(dest="command")

    # --- run ---
    p_run = sub.add_parser("run", help="Run daily scan")
    p_run.add_argument("--equity", type=float, default=None)
    p_run.add_argument("--no-alerts", action="store_true", help="Skip Telegram")
    p_run.set_defaults(func=cmd_run)

    # --- status ---
    p_status = sub.add_parser("status", help="Show portfolio status")
    p_status.set_defaults(func=cmd_status)

    # --- review ---
    p_review = sub.add_parser("review", help="Generate weekly report")
    p_review.add_argument("--weeks", type=int, default=4)
    p_review.add_argument("--save", action="store_true")
    p_review.set_defaults(func=cmd_review)

    # --- watchlist ---
    p_wl = sub.add_parser("watchlist", help="Manage watchlist")
    p_wl.add_argument("--update", action="store_true")
    p_wl.set_defaults(func=cmd_watchlist)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


def _load_dotenv() -> None:
    """Load .env file if exists (no python-dotenv required)"""
    env_path = Path(".env")
    if not env_path.exists():
        env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


if __name__ == "__main__":
    sys.exit(main())
