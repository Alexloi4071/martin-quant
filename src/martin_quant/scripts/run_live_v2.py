"""run_live_v2.py

Live / paper trading runner built on scan-v2 + execution planner.
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


def run_live_v2(
    paper: bool = True,
    equity: float = 100_000.0,
    max_signals: int = 5,
    dry_run: bool = False,
    monitor_interval: float = 60.0,
    use_limit_entry: bool = True,
    force_refresh: bool = False,
    telegram_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    symbols: Optional[list[str]] = None,
) -> None:
    mode_str = "PAPER" if paper else "LIVE (REAL MONEY)"
    print(f"\n{'=' * 60}")
    print("  Martin Quant Live Runner V2")
    print(f"  Mode    : {mode_str}")
    print(f"  Equity  : ${equity:,.0f}")
    print(f"  Plans   : up to {max_signals}")
    print(f"  Dry Run : {dry_run}")
    print(f"{'=' * 60}\n")

    if not paper and not dry_run:
        confirm = input("LIVE TRADING MODE - type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    from martin_quant.broker import IBKRBridge, OrderManager, PositionMonitor
    from martin_quant.execution import ExecutionPlanner
    from martin_quant.pipeline.data_pipeline import DataPipeline
    from martin_quant.regime import MartinTradeQualityEvaluator
    from martin_quant.scanner.daily_scan_v2 import DailyScannerV2
    from martin_quant.scripts.run_daily_scan_v2 import (
        BENCHMARKS,
        _build_breadth_snapshot,
        _build_sector_strength_map,
        _build_setup_scores,
        _build_weekly_context_map,
        _detect_market_state,
        _sector_etf_requests,
    )

    bridge = IBKRBridge(paper=paper)
    print("Connecting to IBKR...")
    connected = bridge.connect(timeout=20)
    if not connected:
        log.error("Failed to connect to IBKR. Is TWS/Gateway running?")
        sys.exit(1)
    print(f"Connected! Account value: ${bridge.account_value:,.2f}")
    live_equity = bridge.account_value if bridge.account_value > 1000 else equity

    print("\nFetching market data...")
    pipeline = DataPipeline(
        max_workers=8,
        cache_ttl_hours=2.0 if not force_refresh else 0,
    )
    symbol_list = [item.upper() for item in symbols] if symbols else None
    daily_data, intraday_data = pipeline.fetch(symbols=symbol_list, fetch_intraday=True, intraday_interval="15m")
    ref_daily, ref_intraday = pipeline.fetch(symbols=BENCHMARKS, fetch_intraday=True, intraday_interval="15m")
    spy_df = ref_daily.get("SPY")
    qqq_df = ref_daily.get("QQQ")
    iwm_df = ref_daily.get("IWM")
    watchlist_sectors = pipeline.get_sectors(list(daily_data.keys()))

    if not daily_data:
        log.error("No daily data loaded")
        bridge.disconnect()
        sys.exit(1)

    breadth_snapshot = _build_breadth_snapshot(daily_data, watchlist_sectors, spy_df)
    detected_regime, market_context, trade_quality = _detect_market_state(qqq_df, iwm_df, spy_df=spy_df)
    trade_quality = MartinTradeQualityEvaluator().evaluate(market_context, breadth_snapshot=breadth_snapshot)
    regime = detected_regime
    weekly_context_map = _build_weekly_context_map(daily_data, spy_df)
    sector_etf_requests = _sector_etf_requests(watchlist_sectors)
    sector_etf_daily = {}
    if sector_etf_requests:
        sector_daily_by_ticker, _ = pipeline.fetch(symbols=sorted(set(sector_etf_requests.values())), fetch_intraday=False)
        sector_etf_daily = {
            canonical: sector_daily_by_ticker[ticker]
            for canonical, ticker in sector_etf_requests.items()
            if ticker in sector_daily_by_ticker
        }
    sector_strength_map = _build_sector_strength_map(
        daily_data=daily_data,
        watchlist_sectors=watchlist_sectors,
        sector_etf_daily=sector_etf_daily,
        benchmark_df=qqq_df if qqq_df is not None else spy_df,
    )
    setup_scores = _build_setup_scores(daily_data, weekly_context_map=weekly_context_map)
    filtered_daily = {symbol: df for symbol, df in daily_data.items() if symbol in setup_scores}
    filtered_intraday = {symbol: df for symbol, df in intraday_data.items() if symbol in setup_scores}
    filtered_sectors = {symbol: sector for symbol, sector in watchlist_sectors.items() if symbol in setup_scores}
    filtered_metadata = pipeline.get_metadata(list(filtered_daily.keys())) if filtered_daily else {}
    market_caps = {
        symbol: float(meta.get("market_cap", 0) or 0)
        for symbol, meta in filtered_metadata.items()
        if meta.get("market_cap") not in (None, "")
    }

    print("\nRunning DailyScannerV2...")
    scanner = DailyScannerV2(equity=live_equity)
    results = scanner.scan(
        watchlist_data=filtered_daily,
        regime=regime,
        watchlist_sectors=filtered_sectors,
        watchlist_setup_scores=setup_scores,
        df_15m_map=filtered_intraday if filtered_intraday else None,
        benchmark_15m_map=ref_intraday if ref_intraday else None,
        trade_quality_state=trade_quality.state,
        trade_quality_weight=trade_quality.quality_weight,
        allow_longs=trade_quality.allow_longs,
        allow_shorts=trade_quality.allow_shorts,
        breadth_snapshot=breadth_snapshot,
        sector_strength_map=sector_strength_map,
        weekly_context_map=weekly_context_map,
    )
    scanner.print_report(results, date=datetime.today().strftime("%Y-%m-%d"))
    if not results:
        print("No signals today.")
        bridge.disconnect()
        return

    planner = ExecutionPlanner()
    execution_bundle = planner.build_plan(
        results=results,
        as_of=datetime.today().strftime("%Y-%m-%d"),
        equity=live_equity,
        regime=regime,
        trade_quality_state=trade_quality.state,
        trade_quality_weight=trade_quality.quality_weight,
        breadth_snapshot=breadth_snapshot,
        market_caps=market_caps,
    )
    summary = execution_bundle.summary()
    print(
        f"Execution plan: {summary['active_count']} active / {summary['blocked_count']} blocked "
        f"| exposure={summary['planned_exposure_pct']:.1f}% risk={summary['planned_risk_pct']:.2f}% "
        f"| confirmed_entries={summary.get('confirmed_entry_count', 0)}"
    )
    for plan in execution_bundle.active_plans[:max_signals]:
        confirmation_text = _format_plan_confirmation(plan)
        if confirmation_text:
            print(f"  PLAN {plan.symbol} {plan.direction} confirm={confirmation_text}")

    if not execution_bundle.active_plans:
        print("No active execution plans.")
        bridge.disconnect()
        return

    tg_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat = telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    order_mgr = OrderManager(
        bridge=bridge,
        equity=live_equity,
        max_signals=max_signals,
        use_limit_entry=use_limit_entry,
        dry_run=dry_run,
        telegram_token=tg_token,
        telegram_chat_id=tg_chat,
        allow_shorts=trade_quality.allow_shorts,
    )
    print(f"\nExecuting top {max_signals} plans...")
    exec_results = order_mgr.execute_plans(execution_bundle.active_plans)

    submitted = [r for r in exec_results if r.status == "submitted"]
    skipped = [r for r in exec_results if r.status == "skipped"]
    print(f"\nExecution complete: {len(submitted)} submitted, {len(skipped)} skipped")
    for r in submitted:
        confirmation_text = _format_execution_confirmation(r)
        print(
            f"  {'[DRY]' if dry_run else '':6s} #{r.order_id} {r.action} {r.symbol} "
            f"x{r.quantity} entry={r.entry_price:.2f} stop={r.stop_price:.2f} "
            f"target={r.target_price:.2f}{(' | confirm=' + confirmation_text) if confirmation_text else ''}"
        )

    def ohlcv_getter(symbol: str):
        fresh = pipeline._read_local_frame(symbol, "1d")
        return fresh if fresh is not None else daily_data.get(symbol)

    monitor = PositionMonitor(
        bridge=bridge,
        order_manager=order_mgr,
        ohlcv_getter=ohlcv_getter,
        interval=monitor_interval,
        regime=regime,
    )
    for plan in execution_bundle.active_plans:
        if any(item.symbol == plan.symbol and item.status == "submitted" for item in submitted):
            monitor.register_execution_plan(plan)
    monitor.start()
    print(f"\nPosition monitor running (interval={monitor_interval:.0f}s)")
    print("Press Ctrl+C to stop.\n")

    def _shutdown(sig, frame):
        print("\nShutting down...")
        monitor.stop()
        bridge.disconnect()
        _print_final_report(bridge, order_mgr, monitor)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        _shutdown(None, None)


def _format_plan_confirmation(plan: object) -> str:
    reason = str(getattr(plan, "entry_confirmation_reason", "") or "").strip()
    bars = int(getattr(plan, "entry_confirmation_bars", 0) or 0)
    mode = str(getattr(plan, "entry_confirmation_mode", "") or "").strip()
    if not reason:
        return ""
    prefix = f"{mode}:{bars}" if mode else (f"{bars}bar" if bars else "")
    return f"{prefix} {reason}".strip()


def _format_execution_confirmation(result: object) -> str:
    reason = str(getattr(result, "confirmation_reason", "") or "").strip()
    bars = int(getattr(result, "confirmation_bars", 0) or 0)
    mode = str(getattr(result, "confirmation_mode", "") or "").strip()
    if not reason:
        return ""
    prefix = f"{mode}:{bars}" if mode else (f"{bars}bar" if bars else "")
    return f"{prefix} {reason}".strip()


def _print_final_report(bridge: "IBKRBridge", order_mgr: "OrderManager", monitor: "PositionMonitor") -> None:
    print("\n" + "=" * 60)
    print("  Final Report")
    print("=" * 60)
    print(f"  Account Value : ${bridge.account_value:,.2f}")
    print(f"  Open Positions: {len([p for p in bridge.positions.values() if p.quantity != 0])}")
    total_unrealized = sum(p.unrealized_pnl for p in bridge.positions.values())
    print(f"  Unrealized P&L: ${total_unrealized:+,.2f}")
    print(f"  Monitor actions: {len(monitor.action_log)}")
    for action in monitor.action_log[-5:]:
        exit_confirmation = action.get("exit_confirmation") or {}
        confirmation_reason = str(exit_confirmation.get("reason", "") or "").strip()
        extra = f" | exit_confirm={confirmation_reason}" if confirmation_reason else ""
        print(f"    {action['time']} {action['symbol']} {action['action']} {action['reason']}{extra}")
    print("=" * 60)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Martin Quant Live/Paper Trading Runner V2")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", action="store_true", default=True, help="Paper trading (default)")
    mode.add_argument("--live", action="store_true", default=False, help="LIVE trading (real money)")
    parser.add_argument("--equity", type=float, default=100_000)
    parser.add_argument("--max-signals", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", help="Log only, no real orders")
    parser.add_argument("--limit", action="store_true", default=True, help="Use limit entry orders")
    parser.add_argument("--market", action="store_true", help="Use market entry orders")
    parser.add_argument("--interval", type=float, default=60.0, help="Monitor interval (seconds)")
    parser.add_argument("--force-refresh", action="store_true", help="Force data re-fetch")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols")
    args = parser.parse_args()

    symbol_list = [item.strip().upper() for item in args.symbols.split(",")] if args.symbols else None
    run_live_v2(
        paper=not args.live,
        equity=args.equity,
        max_signals=args.max_signals,
        dry_run=args.dry_run,
        use_limit_entry=not args.market,
        monitor_interval=args.interval,
        force_refresh=args.force_refresh,
        symbols=symbol_list,
    )


if __name__ == "__main__":
    main()
