"""run_live.py

Full live / paper trading runner for the Martin Luk strategy.

Workflow:
  1. Connect to IBKR (paper or live)
  2. Run DataPipeline to fetch all market data
  3. Run DailyScanner to generate signals
  4. Filter signals vs. existing positions
  5. Execute top N signals via OrderManager
  6. Start PositionMonitor background thread
  7. Keep running until market close or manual stop
  8. Print final report

Usage:
    # Paper trading
    python -m martin_quant.scripts.run_live --paper

    # Live trading (real money!)
    python -m martin_quant.scripts.run_live --live --equity 150000

    # Dry run (no real orders, just log)
    python -m martin_quant.scripts.run_live --paper --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_live(
    paper: bool = True,
    equity: float = 100_000.0,
    max_signals: int = 5,
    dry_run: bool = False,
    monitor_interval: float = 60.0,
    use_limit_entry: bool = True,
    force_refresh: bool = False,
    telegram_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
) -> None:
    mode_str = "PAPER" if paper else "\u26a0\ufe0f  LIVE (REAL MONEY)"
    print(f"\n{'='*60}")
    print(f"  Martin Quant Live Runner")
    print(f"  Mode    : {mode_str}")
    print(f"  Equity  : ${equity:,.0f}")
    print(f"  Signals : up to {max_signals}")
    print(f"  Dry Run : {dry_run}")
    print(f"{'='*60}\n")

    if not paper and not dry_run:
        confirm = input("\u26a0\ufe0f  LIVE TRADING MODE — type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    # ------------------------------------------------------------------
    # Step 1: Connect to IBKR
    # ------------------------------------------------------------------
    from martin_quant.broker import IBKRBridge, OrderManager, PositionMonitor

    bridge = IBKRBridge(paper=paper)
    print("Connecting to IBKR...")
    connected = bridge.connect(timeout=20)
    if not connected:
        log.error("Failed to connect to IBKR. Is TWS/Gateway running?")
        sys.exit(1)
    print(f"Connected! Account value: ${bridge.account_value:,.2f}")

    # Use live account value if available
    live_equity = bridge.account_value if bridge.account_value > 1000 else equity

    # ------------------------------------------------------------------
    # Step 2: Fetch market data
    # ------------------------------------------------------------------
    print("\nFetching market data...")
    from martin_quant.pipeline import DataPipeline

    pipeline = DataPipeline(
        universe="combined",
        cache_ttl_hours=2.0 if not force_refresh else 0,
    )
    data = pipeline.run(
        force_refresh=force_refresh,
        include_metadata=True,
        include_earnings=True,
    )
    if not data.is_valid():
        log.error("Pipeline failed — SPY data missing")
        bridge.disconnect()
        sys.exit(1)
    print(f"Data ready: {data.symbols_fetched} symbols fetched at {data.fetched_at}")

    # ------------------------------------------------------------------
    # Step 3: Run scanner
    # ------------------------------------------------------------------
    print("\nRunning DailyScanner...")
    from martin_quant.daily_scan import DailyScanner, DailyScanConfig

    scanner = DailyScanner(config=DailyScanConfig(
        equity=live_equity,
        max_signals=max_signals * 3,   # extra candidates for filtering
    ))
    scan_result = scanner.run(
        spy_df=data.spy_df,
        iwm_df=data.iwm_df,
        ohlcv_map=data.ohlcv_map,
        metadata=data.metadata,
        eps_catalyst_set=data.eps_catalyst_set,
        premarket_prices=data.premarket_prices,
        date_str=datetime.today().strftime("%Y-%m-%d"),
    )
    print(scan_result.summary())

    if not scan_result.signals:
        print("No signals today.")
        bridge.disconnect()
        return

    # ------------------------------------------------------------------
    # Step 4 + 5: Execute signals
    # ------------------------------------------------------------------
    tg_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat  = telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    order_mgr = OrderManager(
        bridge=bridge,
        equity=live_equity,
        max_signals=max_signals,
        use_limit_entry=use_limit_entry,
        dry_run=dry_run,
        telegram_token=tg_token,
        telegram_chat_id=tg_chat,
    )
    print(f"\nExecuting top {max_signals} signals...")
    exec_results = order_mgr.execute_signals(scan_result.signals)

    submitted = [r for r in exec_results if r.status == "submitted"]
    skipped   = [r for r in exec_results if r.status == "skipped"]
    print(f"\nExecution complete: {len(submitted)} submitted, {len(skipped)} skipped")
    for r in submitted:
        print(
            f"  {'[DRY]' if dry_run else '':6s} #{r.order_id} {r.action} {r.symbol} "
            f"x{r.quantity} entry={r.entry_price:.2f} stop={r.stop_price:.2f} "
            f"target={r.target_price:.2f}"
        )

    # ------------------------------------------------------------------
    # Step 6: Start position monitor
    # ------------------------------------------------------------------
    entry_px = {r.symbol: r.entry_price for r in submitted if r.entry_price > 0}
    stop_px  = {r.symbol: r.stop_price  for r in submitted if r.stop_price  > 0}

    def ohlcv_getter(symbol: str):
        return data.ohlcv_map.get(symbol)

    monitor = PositionMonitor(
        bridge=bridge,
        order_manager=order_mgr,
        ohlcv_getter=ohlcv_getter,
        entry_prices=entry_px,
        stop_prices=stop_px,
        interval=monitor_interval,
        regime=scan_result.regime.value,
    )
    monitor.start()
    print(f"\nPosition monitor running (interval={monitor_interval:.0f}s)")
    print("Press Ctrl+C to stop.\n")

    # ------------------------------------------------------------------
    # Step 7: Keep running
    # ------------------------------------------------------------------
    def _shutdown(sig, frame):
        print("\nShutting down...")
        monitor.stop()
        bridge.disconnect()
        _print_final_report(bridge, order_mgr, monitor)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            time.sleep(30)
            # Refresh regime every 30 min
            if int(time.time()) % 1800 < 30:
                try:
                    from martin_quant.filters.market_regime import MarketRegimeFilter
                    flt    = MarketRegimeFilter()
                    regime = flt.evaluate(data.spy_df, data.iwm_df)
                    monitor.update_regime(regime.regime.value)
                    log.info("Regime refreshed: %s", regime.regime.value)
                except Exception:
                    pass
    except KeyboardInterrupt:
        _shutdown(None, None)


def _print_final_report(
    bridge: "IBKRBridge",
    order_mgr: "OrderManager",
    monitor: "PositionMonitor",
) -> None:
    print("\n" + "="*60)
    print("  Final Report")
    print("="*60)
    print(f"  Account Value : ${bridge.account_value:,.2f}")
    print(f"  Open Positions: {len([p for p in bridge.positions.values() if p.quantity != 0])}")
    total_unrealized = sum(p.unrealized_pnl for p in bridge.positions.values())
    print(f"  Unrealized P&L: ${total_unrealized:+,.2f}")
    print(f"  Monitor actions: {len(monitor.action_log)}")
    for action in monitor.action_log[-5:]:
        print(f"    {action['time']} {action['symbol']} {action['action']} {action['reason']}")
    print("="*60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Martin Quant Live/Paper Trading Runner"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", action="store_true", default=True,  help="Paper trading (default)")
    mode.add_argument("--live",  action="store_true", default=False, help="LIVE trading (real money)")
    parser.add_argument("--equity",     type=float, default=100_000)
    parser.add_argument("--max-signals",type=int,   default=5)
    parser.add_argument("--dry-run",    action="store_true", help="Log only, no real orders")
    parser.add_argument("--limit",      action="store_true", default=True, help="Use limit entry orders")
    parser.add_argument("--market",     action="store_true", help="Use market entry orders")
    parser.add_argument("--interval",   type=float, default=60.0, help="Monitor interval (seconds)")
    parser.add_argument("--force-refresh", action="store_true", help="Force data re-fetch")
    args = parser.parse_args()

    run_live(
        paper=not args.live,
        equity=args.equity,
        max_signals=args.max_signals,
        dry_run=args.dry_run,
        use_limit_entry=not args.market,
        monitor_interval=args.interval,
        force_refresh=args.force_refresh,
    )


if __name__ == "__main__":
    main()
