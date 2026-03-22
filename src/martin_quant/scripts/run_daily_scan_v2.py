"""V2 daily scan runner using local parquet data and direction-aware candidate export."""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

BENCHMARKS = ["SPY", "QQQ", "IWM"]
SCAN_BENCHMARK_EXCLUDES = {"SPY", "IWM", "QQQ", "SMH"}


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _detect_market_state(qqq_df, iwm_df, spy_df=None):
    from martin_quant.regime import MartinMarketContextEvaluator, MartinTradeQualityEvaluator

    primary_df = qqq_df if qqq_df is not None else spy_df
    if primary_df is None or iwm_df is None:
        context = None
        quality = MartinTradeQualityEvaluator().evaluate(None)
        return "BULL", context, quality

    context = MartinMarketContextEvaluator().evaluate(qqq_df=primary_df, iwm_df=iwm_df)
    quality = MartinTradeQualityEvaluator().evaluate(context)
    return context.regime, context, quality


def _signal_to_setup_info(signal) -> dict:
    setup_type = getattr(signal.setup_type, "value", str(signal.setup_type))
    return {
        "score": float(getattr(signal, "score", 0.0)),
        "type": setup_type,
        "direction": str(getattr(signal, "direction", "long")),
        "entry_price": getattr(signal, "entry_price", None),
        "stop_price": getattr(signal, "stop_price", None),
        "target_price": getattr(signal, "target_price", None),
        "notes": getattr(signal, "notes", []),
    }


def _build_weekly_context_map(daily_data: dict[str, pd.DataFrame], spy_df: pd.DataFrame | None) -> dict[str, object]:
    from martin_quant.features.weekly_context import get_weekly_context

    weekly_map: dict[str, object] = {}
    if spy_df is None:
        return weekly_map
    for symbol, df in daily_data.items():
        if symbol in SCAN_BENCHMARK_EXCLUDES:
            continue
        try:
            ctx = get_weekly_context(symbol, df, spy_df)
        except Exception as exc:
            log.debug("Weekly context failed for %s: %s", symbol, exc)
            continue
        if ctx is not None:
            weekly_map[symbol] = ctx
    return weekly_map


def _build_setup_scores(daily_data: dict, weekly_context_map: dict[str, object] | None = None) -> dict[str, dict]:
    from martin_quant.setups.breakout_setup import BreakoutConfig, BreakoutSetupDetector
    from martin_quant.setups.pullback_setup import PullbackConfig, PullbackSetupDetector
    from martin_quant.setups.short_setup import ShortSetupConfig, ShortSetupDetector

    detectors = (
        PullbackSetupDetector(PullbackConfig(require_weekly_context=True)),
        BreakoutSetupDetector(BreakoutConfig(require_weekly_context=True)),
        ShortSetupDetector(ShortSetupConfig(require_weekly_context=True, require_weekly_bear_for_short=True)),
    )
    scores: dict[str, dict] = {}
    weekly_map = weekly_context_map or {}

    for symbol, df in daily_data.items():
        if symbol in SCAN_BENCHMARK_EXCLUDES:
            continue
        best: dict | None = None
        weekly_context = weekly_map.get(symbol)
        for detector in detectors:
            try:
                if isinstance(detector, ShortSetupDetector):
                    signal = detector.detect(symbol=symbol, df=df, weekly_context=weekly_context)
                else:
                    signal = detector.detect(symbol=symbol, df=df, timeframe="1d", weekly_context=weekly_context)
            except Exception as exc:
                log.debug("Setup detector failed for %s via %s: %s", symbol, detector.__class__.__name__, exc)
                continue
            if signal is None:
                continue
            candidate = _signal_to_setup_info(signal)
            if best is None or candidate["score"] > best["score"]:
                best = candidate
        if best is not None:
            scores[symbol] = best
    return scores


def _scan_symbol_excludes() -> set[str]:
    from martin_quant.regime import SECTOR_ETF_MAP

    return set(SCAN_BENCHMARK_EXCLUDES) | set(SECTOR_ETF_MAP.values())


def _build_breadth_snapshot(
    daily_data: dict[str, pd.DataFrame],
    watchlist_sectors: dict[str, str],
    spy_df: pd.DataFrame | None,
):
    from martin_quant.regime import BreadthParticipationAnalyzer

    filtered_daily = {symbol: df for symbol, df in daily_data.items() if symbol not in _scan_symbol_excludes()}
    filtered_sectors = {symbol: sector for symbol, sector in watchlist_sectors.items() if symbol in filtered_daily}
    return BreadthParticipationAnalyzer().analyze(filtered_daily, sector_map=filtered_sectors, spy_df=spy_df)


def _sector_etf_requests(watchlist_sectors: dict[str, str]) -> dict[str, str]:
    from martin_quant.regime import SECTOR_ETF_MAP, normalize_sector_name

    requests: dict[str, str] = {}
    for sector in watchlist_sectors.values():
        canonical = normalize_sector_name(sector)
        etf = SECTOR_ETF_MAP.get(canonical)
        if etf:
            requests[canonical] = etf
    return requests


def _build_sector_strength_map(
    daily_data: dict[str, pd.DataFrame],
    watchlist_sectors: dict[str, str],
    sector_etf_daily: dict[str, pd.DataFrame],
    benchmark_df: pd.DataFrame | None,
):
    from martin_quant.regime import DynamicSectorRelativeStrengthAnalyzer

    filtered_daily = {symbol: df for symbol, df in daily_data.items() if symbol not in _scan_symbol_excludes()}
    filtered_sectors = {symbol: sector for symbol, sector in watchlist_sectors.items() if symbol in filtered_daily}
    return DynamicSectorRelativeStrengthAnalyzer().analyze_universe(
        universe=filtered_daily,
        sector_map=filtered_sectors,
        sector_etf_data=sector_etf_daily,
        benchmark_df=benchmark_df,
    )


def run_scan_v2(
    send_alerts: bool = True,
    regime_override: str | None = None,
    symbols: list[str] | None = None,
    equity: float = 100_000.0,
    export_candidates: bool = True,
) -> int:
    today = str(date.today())
    log.info("=== Martin Quant V2 Daily Scan %s ===", today)

    try:
        from dotenv import load_dotenv
        load_dotenv()
        log.info("Step 0: .env loaded")
    except ImportError:
        pass

    log.info("Step 1: Fetching market data...")
    try:
        from martin_quant.pipeline.data_pipeline import DataPipeline

        pipeline = DataPipeline(max_workers=8)
        symbol_list = [item.upper() for item in symbols] if symbols else None
        daily_data, intraday_data = pipeline.fetch(symbols=symbol_list, fetch_intraday=True, intraday_interval="15m")
        ref_daily, ref_intraday = pipeline.fetch(symbols=BENCHMARKS, fetch_intraday=True, intraday_interval="15m")
        spy_df = ref_daily.get("SPY")
        qqq_df = ref_daily.get("QQQ")
        iwm_df = ref_daily.get("IWM")
        watchlist_sectors = pipeline.get_sectors(list(daily_data.keys()))
        log.info("Step 1: daily=%d intraday=%d", len(daily_data), len(intraday_data))
    except Exception as exc:
        log.error("Step 1: Data fetch failed: %s", exc, exc_info=True)
        return 0

    if not daily_data:
        log.warning("No daily data loaded. Exiting.")
        return 0

    breadth_snapshot = _build_breadth_snapshot(daily_data, watchlist_sectors, spy_df)
    log.info(
        "Step 1b: Breadth=%s leaders=%d ratio=%.1f%% top=%s",
        breadth_snapshot.state,
        breadth_snapshot.leader_count,
        breadth_snapshot.leader_ratio * 100.0,
        ", ".join(breadth_snapshot.top_sectors[:3]) if breadth_snapshot.top_sectors else "n/a",
    )

    detected_regime, market_context, trade_quality = _detect_market_state(qqq_df, iwm_df, spy_df=spy_df)
    from martin_quant.regime import MartinTradeQualityEvaluator
    trade_quality = MartinTradeQualityEvaluator().evaluate(market_context, breadth_snapshot=breadth_snapshot)
    regime = regime_override.upper() if regime_override else detected_regime
    log.info("Step 2: Regime=%s trade_quality=%s", regime, trade_quality.state)
    if market_context is not None:
        log.info("Step 2b: Market context notes=%s", " | ".join(market_context.notes))
    log.info("Step 2c: Trade quality notes=%s", " | ".join(trade_quality.notes))

    weekly_context_map = _build_weekly_context_map(daily_data, spy_df)
    log.info("Step 2d: Weekly contexts built=%d", len(weekly_context_map))

    sector_etf_requests = _sector_etf_requests(watchlist_sectors)
    sector_etf_daily: dict[str, pd.DataFrame] = {}
    if sector_etf_requests:
        try:
            sector_daily_by_ticker, _ = pipeline.fetch(symbols=sorted(set(sector_etf_requests.values())), fetch_intraday=False)
            sector_etf_daily = {
                canonical: sector_daily_by_ticker[ticker]
                for canonical, ticker in sector_etf_requests.items()
                if ticker in sector_daily_by_ticker
            }
            log.info("Step 2e: Sector ETF snapshots fetched=%d", len(sector_etf_daily))
        except Exception as exc:
            log.warning("Step 2e: Sector ETF fetch failed: %s", exc)

    sector_strength_map = _build_sector_strength_map(
        daily_data=daily_data,
        watchlist_sectors=watchlist_sectors,
        sector_etf_daily=sector_etf_daily,
        benchmark_df=qqq_df if qqq_df is not None else spy_df,
    )
    strong_sector_count = sum(1 for item in sector_strength_map.values() if item.state == "STRONG")
    weak_sector_count = sum(1 for item in sector_strength_map.values() if item.state == "WEAK")
    log.info("Step 2f: Dynamic sector RS built=%d strong=%d weak=%d", len(sector_strength_map), strong_sector_count, weak_sector_count)

    log.info("Step 3: Building setup scores...")
    setup_scores = _build_setup_scores(daily_data, weekly_context_map=weekly_context_map)
    long_count = sum(1 for item in setup_scores.values() if item.get("direction") == "long")
    short_count = sum(1 for item in setup_scores.values() if item.get("direction") == "short")
    log.info("Step 3: %d symbols passed base setup detection (long=%d short=%d)", len(setup_scores), long_count, short_count)

    filtered_daily = {symbol: df for symbol, df in daily_data.items() if symbol in setup_scores}
    filtered_intraday = {symbol: df for symbol, df in intraday_data.items() if symbol in setup_scores}
    filtered_sectors = {symbol: sector for symbol, sector in watchlist_sectors.items() if symbol in setup_scores}
    filtered_metadata = pipeline.get_metadata(list(filtered_daily.keys())) if filtered_daily else {}
    market_caps = {
        symbol: float(meta.get("market_cap", 0) or 0)
        for symbol, meta in filtered_metadata.items()
        if meta.get("market_cap") not in (None, "")
    }

    log.info("Step 4: Running DailyScannerV2 regime=%s quality=%s ...", regime, trade_quality.state)
    try:
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2

        scanner = DailyScannerV2(equity=equity)
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
        )
        scanner.print_report(results, date=today)
        log.info("Step 4: %d signals found", len(results))
    except Exception as exc:
        log.error("Step 4: Scanner failed: %s", exc, exc_info=True)
        results = []

    execution_bundle = None
    execution_paths: dict[str, str] = {}
    if results:
        try:
            from martin_quant.execution import ExecutionPlanner, export_execution_plan_bundle

            execution_bundle = ExecutionPlanner().build_plan(
                results=results,
                as_of=today,
                equity=equity,
                regime=regime,
                trade_quality_state=trade_quality.state,
                trade_quality_weight=trade_quality.quality_weight,
                breadth_snapshot=breadth_snapshot,
                market_caps=market_caps,
            )
            execution_paths = export_execution_plan_bundle(execution_bundle, out_dir="outputs/signals")
            summary = execution_bundle.summary()
            log.info(
                "Step 5: Execution plan active=%d blocked=%d exposure=%.1f%% risk=%.2f%%",
                summary["active_count"],
                summary["blocked_count"],
                summary["planned_exposure_pct"],
                summary["planned_risk_pct"],
            )
        except Exception as exc:
            log.warning("Step 5: Execution planning failed: %s", exc, exc_info=True)
    else:
        log.info("Step 5: Execution planning skipped (no signals)")

    log.info("Step 6: Saving scan results...")
    try:
        out_path = Path(f"scan_v2_{today}.csv")
        fieldnames = [
            "symbol", "direction", "setup_type", "sector", "regime",
            "setup_score", "avwap_score", "sector_bonus", "dynamic_sector_bonus", "breadth_bonus", "total_score",
            "trade_quality_state", "breadth_state", "sector_strength_state",
            "entry_price", "stop_price", "target_price", "entry_note",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for result in results:
                payload = result.to_dict() if hasattr(result, "to_dict") else vars(result)
                writer.writerow(payload)
        log.info("Step 6: Saved %s", out_path)
    except Exception as exc:
        log.warning("Step 6: CSV save failed: %s", exc)

    if export_candidates:
        try:
            from martin_quant.signals import export_scan_candidates
            paths = export_scan_candidates(
                results,
                out_dir="outputs/signals",
                as_of=today,
                metadata={
                    "regime": regime,
                    "trade_quality_state": trade_quality.state,
                    "trade_quality_weight": trade_quality.quality_weight,
                    "trade_quality_notes": trade_quality.notes,
                    "equity": equity,
                    "base_setup_count": len(setup_scores),
                    "long_base_count": long_count,
                    "short_base_count": short_count,
                    "weekly_context_count": len(weekly_context_map),
                    "breadth_snapshot": breadth_snapshot.to_dict(),
                    "dynamic_sector_states": {key: value.to_dict() for key, value in sector_strength_map.items()},
                    "execution_plan_summary": execution_bundle.summary() if execution_bundle is not None else {},
                    "execution_plan_paths": execution_paths,
                    "final_signal_count": len(results),
                },
            )
            log.info("Step 6b: Candidate bundle exported to %s", paths["json"])
        except Exception as exc:
            log.warning("Step 6b: Candidate export failed: %s", exc)

    if send_alerts:
        log.info("Step 7: Sending Telegram alerts...")
        try:
            from martin_quant.utils.alert_manager import AlertManager
            manager = AlertManager()
            if results:
                lines = [f"*V2 Scan {today}* Regime: {regime} Quality: {trade_quality.state} Signals: {len(results)} Breadth: {breadth_snapshot.state}"]
                for idx, result in enumerate(results[:5], 1):
                    lines.append(f"{idx}. {result.symbol} {result.direction} {result.setup_type} score={result.total_score:.3f} {result.entry_note}".strip())
                manager.send_text("\n".join(lines))
            log.info("Step 7: Telegram sent")
        except Exception as exc:
            log.warning("Step 7: Telegram failed: %s", exc)
    else:
        log.info("Step 7: Alerts skipped (--no-alerts)")

    log.info("=== V2 Scan complete signals=%d ===", len(results))
    return len(results)


def main() -> None:
    parser = argparse.ArgumentParser(prog="run_daily_scan_v2", description="Martin Quant V2 Daily Scan")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--no-alerts", action="store_true")
    parser.add_argument("--no-candidate-export", action="store_true")
    parser.add_argument("--regime", default=None, help="Override regime (BULL/WEAK_BULL/CHOPPY/BEAR)")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols, e.g. NVDA,AMD,MSFT")
    parser.add_argument("--equity", default="100000", help="Account equity")
    args = parser.parse_args()

    _setup_logging(verbose=args.verbose)
    symbol_list = [item.strip().upper() for item in args.symbols.split(",")] if args.symbols else None

    count = run_scan_v2(
        send_alerts=not args.no_alerts,
        regime_override=args.regime,
        symbols=symbol_list,
        equity=float(args.equity),
        export_candidates=not args.no_candidate_export,
    )
    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()

