"""run_daily_scan_v2.py

V2 一鍵執行腳本
================
整合：DataPipeline → RegimeDetector → DailyScannerV2 → ExitManager → AlertManager → CSV

Usage:
    python -m martin_quant.scripts.run_daily_scan_v2
    python -m martin_quant.scripts.run_daily_scan_v2 --no-alerts
    python -m martin_quant.scripts.run_daily_scan_v2 --regime BULL --symbols NVDA,AMD,MSFT
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_scan_v2(
    send_alerts: bool = True,
    regime_override: str | None = None,
    symbols: list[str] | None = None,
    equity: float = 100_000.0,
) -> int:
    """
    V2 完整掃描流程。

    Returns
    -------
    int  : 找到的信號數量
    """
    today = str(date.today())
    log.info("=== Martin Quant V2 Daily Scan  %s ===", today)

    # ------------------------------------------------------------------
    # Step 0: Load .env
    # ------------------------------------------------------------------
    try:
        from dotenv import load_dotenv
        load_dotenv()
        log.info("Step 0: .env loaded")
    except ImportError:
        pass

    # ------------------------------------------------------------------
    # Step 1: Detect Regime
    # ------------------------------------------------------------------
    regime = regime_override
    if not regime:
        try:
            from martin_quant.regime import MarketRegimeDetector
            detector = MarketRegimeDetector()
            result   = detector.detect()
            regime   = result.regime
            log.info("Step 1: Regime detected → %s (conf=%.0f%%)",
                     regime, result.confidence)
        except Exception as e:
            log.warning("Step 1: RegimeDetector failed (%s), defaulting to BULL", e)
            regime = "BULL"
    else:
        log.info("Step 1: Regime override → %s", regime)

    # ------------------------------------------------------------------
    # Step 2: Fetch Data
    # ------------------------------------------------------------------
    log.info("Step 2: Fetching market data...")
    try:
        from martin_quant.pipeline.data_pipeline import DataPipeline
        pipeline = DataPipeline(max_workers=8)
        sym_list = symbols or None          # None → DEFAULT_SYMBOLS
        daily_data, intraday_data = pipeline.fetch(
            symbols=sym_list,
            fetch_intraday=True,
            intraday_interval="15m",
        )
        watchlist_sectors = pipeline.get_sectors(list(daily_data.keys()))
        log.info("Step 2: daily=%d  intraday=%d",
                 len(daily_data), len(intraday_data))
    except Exception as e:
        log.error("Step 2: Data fetch failed: %s", e, exc_info=True)
        return 0

    # ------------------------------------------------------------------
    # Step 3: Run DailyScannerV2
    # ------------------------------------------------------------------
    log.info("Step 3: Running DailyScannerV2  regime=%s ...", regime)
    try:
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2
        scanner = DailyScannerV2(equity=equity)
        results = scanner.scan(
            watchlist_data=daily_data,
            regime=regime,
            watchlist_sectors=watchlist_sectors,
            intraday_data=intraday_data if intraday_data else None,
        )
        scanner.print_report(results, date=today)
        log.info("Step 3: %d signals found", len(results))
    except Exception as e:
        log.error("Step 3: Scanner failed: %s", e, exc_info=True)
        results = []

    # ------------------------------------------------------------------
    # Step 4: Exit Manager (check open positions)
    # ------------------------------------------------------------------
    log.info("Step 4: Running ExitManager...")
    exit_signals: list = []
    try:
        from martin_quant.risk import ExitManager
        em = ExitManager(equity=equity)
        exit_signals = em.check_all(daily_data)
        if exit_signals:
            log.info("Step 4: %d exit signals", len(exit_signals))
            for es in exit_signals:
                log.info("  EXIT %s → %s (%s)",
                         es.symbol, es.exit_type, es.action)
        else:
            log.info("Step 4: No exit signals")
    except Exception as e:
        log.warning("Step 4: ExitManager skipped: %s", e)

    # ------------------------------------------------------------------
    # Step 5: Save CSV
    # ------------------------------------------------------------------
    log.info("Step 5: Saving scan results...")
    try:
        import csv
        out_path = Path(f"scan_v2_{today}.csv")
        if results:
            fieldnames = [
                "symbol", "setup_type", "sector", "regime",
                "total_score", "entry_price", "stop", "target",
                "risk_reward", "orb_signal", "avwap_support",
            ]
            with out_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for r in results:
                    writer.writerow(vars(r) if hasattr(r, "__dict__") else r)
            log.info("Step 5: Saved → %s", out_path)
    except Exception as e:
        log.warning("Step 5: CSV save failed: %s", e)

    # ------------------------------------------------------------------
    # Step 6: Send Telegram Alerts
    # ------------------------------------------------------------------
    if send_alerts:
        log.info("Step 6: Sending Telegram alerts...")
        try:
            from martin_quant.utils.alert_manager import AlertManager
            am = AlertManager()

            # Entry signals
            if results:
                emoji = "📈" if regime in ("BULL", "WEAK_BULL") else "⚠️"
                lines = [f"{emoji} *V2 Scan {today}*  Regime: {regime}  Signals: {len(results)}"]
                for i, r in enumerate(results[:5], 1):
                    sym   = getattr(r, "symbol",     str(r))
                    stype = getattr(r, "setup_type", "")
                    score = getattr(r, "total_score", 0)
                    orb   = " 🟢ORB" if getattr(r, "orb_signal", False) else ""
                    lines.append(f"{i}. {sym} {stype} score={score:.3f}{orb}")
                am.send_message("\n".join(lines))

            # Exit signals
            if exit_signals:
                exit_lines = [f"🚨 *EXIT SIGNALS  {today}*"]
                for es in exit_signals:
                    exit_lines.append(
                        f"{es.symbol}: {es.exit_type} → {es.action}"
                    )
                am.send_message("\n".join(exit_lines))

            log.info("Step 6: Telegram sent")
        except Exception as e:
            log.warning("Step 6: Telegram failed: %s", e)
    else:
        log.info("Step 6: Alerts skipped (--no-alerts)")

    log.info("=== V2 Scan complete  signals=%d ===", len(results))
    return len(results)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="run_daily_scan_v2",
        description="Martin Quant V2 Daily Scan",
    )
    parser.add_argument("-v", "--verbose",    action="store_true")
    parser.add_argument("--no-alerts",        action="store_true")
    parser.add_argument("--regime",  default=None,
                        help="Override regime (BULL/WEAK_BULL/CHOPPY/BEAR)")
    parser.add_argument("--symbols", default=None,
                        help="Comma-separated symbols, e.g. NVDA,AMD,MSFT")
    parser.add_argument("--equity",  default="100000",
                        help="Account equity")
    args = parser.parse_args()

    _setup_logging(verbose=args.verbose)

    sym_list = [s.strip().upper() for s in args.symbols.split(",")] \
               if args.symbols else None

    n = run_scan_v2(
        send_alerts=not args.no_alerts,
        regime_override=args.regime,
        symbols=sym_list,
        equity=float(args.equity),
    )
    sys.exit(0 if n >= 0 else 1)


if __name__ == "__main__":
    main()
