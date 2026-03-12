"""cli/main.py

Martin Quant CLI v2
===================
命令列工具，支援：
  martin-scan scan      # 每日掃描（V1）
  martin-scan scan-v2   # 每日掃描（V2 含 AVWAP + Sector + ORB）
  martin-scan review    # 週報 P&L 分析
  martin-scan report    # 生成 Markdown 週報
  martin-scan regime    # 顯示當前市場 regime
  martin-scan orb       # 顯示 ORB 開盤區間（需傳入 symbol）
  martin-scan sectors   # 顯示當前 regime 的推薦 sector

Usage:
    martin-scan scan
    martin-scan scan-v2 --regime BULL
    martin-scan review --weeks 4
    martin-scan report --weeks 1 --telegram
    martin-scan regime
    martin-scan sectors --regime BULL
    martin-scan orb --symbol NVDA
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _load_config():
    """載入 .env 設定"""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    try:
        from martin_quant.config import settings
        return settings
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> int:
    """martin-scan scan — 執行 V1 每日掃描"""
    try:
        from martin_quant.scripts.run_daily_scan import run_scan
        run_scan(send_alerts=not args.no_alerts)
        return 0
    except Exception as e:
        log.error("scan failed: %s", e)
        return 1


def cmd_scan_v2(args: argparse.Namespace) -> int:
    """martin-scan scan-v2 — 執行 V2 掃描（AVWAP + Sector + ORB）"""
    try:
        from martin_quant.scanner.daily_scan_v2 import DailyScannerV2
        from martin_quant.data import get_provider

        regime   = args.regime.upper()
        symbols  = (args.symbols or "").split(",") if args.symbols else _default_watchlist()

        print(f"\n🔍 DailyScannerV2  regime={regime}  symbols={len(symbols)}")

        provider = get_provider()
        watchlist_data = {}
        watchlist_sectors = {}
        for sym in symbols:
            sym = sym.strip().upper()
            try:
                df = provider.get_daily(sym)
                watchlist_data[sym]    = df
                watchlist_sectors[sym] = provider.get_sector(sym) if hasattr(provider, "get_sector") else ""
            except Exception as e:
                log.warning("Skip %s: %s", sym, e)

        scanner = DailyScannerV2(equity=float(args.equity))
        results = scanner.scan(
            watchlist_data=watchlist_data,
            regime=regime,
            watchlist_sectors=watchlist_sectors,
        )
        scanner.print_report(results, date=str(__import__("datetime").date.today()))

        if not args.no_alerts and results:
            _send_scan_v2_alert(results, regime)
        return 0
    except Exception as e:
        log.error("scan-v2 failed: %s", e, exc_info=True)
        return 1


def cmd_review(args: argparse.Namespace) -> int:
    """martin-scan review — 印出 P&L 週報"""
    try:
        from martin_quant.review.trade_reviewer import TradeReviewer
        csv_path = args.csv or "trades.csv"
        reviewer = TradeReviewer(csv_path=csv_path)
        result   = reviewer.review(weeks=args.weeks)
        print(result.summary())
        return 0
    except Exception as e:
        log.error("review failed: %s", e)
        return 1


def cmd_report(args: argparse.Namespace) -> int:
    """martin-scan report — 生成 Markdown 週報並可選傳送 Telegram"""
    try:
        from martin_quant.review.weekly_report import WeeklyReport
        csv_path = args.csv or "trades.csv"
        report   = WeeklyReport(csv_path=csv_path, output_dir="reports")

        if args.telegram:
            report.send_telegram(weeks=args.weeks)
            print("✅ Weekly report sent to Telegram")
        else:
            path = report.save_markdown(weeks=args.weeks)
            print(f"✅ Report saved: {path}")
        return 0
    except Exception as e:
        log.error("report failed: %s", e)
        return 1


def cmd_regime(args: argparse.Namespace) -> int:
    """martin-scan regime — 顯示當前市場 regime"""
    try:
        from martin_quant.regime import MarketRegimeDetector
        if MarketRegimeDetector is None:
            print("MarketRegimeDetector not available")
            return 1
        detector = MarketRegimeDetector()
        result   = detector.detect()
        print(f"\n📊 Current Regime: {result.regime}")
        print(f"   SPY  vs EMA21: {result.spy_vs_ema:.1f}%")
        print(f"   IWM  vs EMA21: {result.iwm_vs_ema:.1f}%")
        print(f"   A/D Line:      {result.ad_trend}")
        print(f"   Confidence:    {result.confidence:.0f}%\n")
        return 0
    except Exception as e:
        log.error("regime failed: %s", e)
        return 1


def cmd_sectors(args: argparse.Namespace) -> int:
    """martin-scan sectors — 顯示指定 regime 的推薦 sector 和 ETF"""
    try:
        from martin_quant.regime.sector_regime_filter import SectorRegimeFilter
        regime = args.regime.upper()
        filt   = SectorRegimeFilter()
        ranked = filt.rank_sectors(regime)
        etfs   = filt.get_preferred_etfs(regime)
        cfg    = filt._get_regime_cfg(regime)

        print(f"\n🗂  Sector Rankings for regime: {regime}")
        print(f"{'─'*45}")
        for s in cfg.get("preferred", []):
            print(f"  ✅ PREFERRED  {s}")
        for s in cfg.get("allowed", []):
            print(f"  ✔️  ALLOWED    {s}")
        for s in cfg.get("avoid", [])[:5]:
            print(f"  ❌ AVOID      {s}")
        print(f"\n  Preferred ETFs: {', '.join(etfs) or 'None'}\n")
        return 0
    except Exception as e:
        log.error("sectors failed: %s", e)
        return 1


def cmd_orb(args: argparse.Namespace) -> int:
    """martin-scan orb --symbol NVDA — 顯示今日 ORB 開盤區間"""
    try:
        if not args.symbol:
            print("Usage: martin-scan orb --symbol NVDA")
            return 1
        from martin_quant.timing.orb_15m_trigger import ORBTrigger
        from martin_quant.data import get_provider

        sym      = args.symbol.upper()
        provider = get_provider()
        df_15m   = provider.get_intraday(sym, interval="15m") if hasattr(provider, "get_intraday") else None

        trigger = ORBTrigger(equity=float(args.equity))
        if df_15m is not None:
            levels = trigger.get_or_levels(df_15m)
            print(f"\n📐 ORB Levels for {sym}")
            print(f"   OR High    : {levels.get('or_high','N/A')}")
            print(f"   OR Low     : {levels.get('or_low','N/A')}")
            print(f"   OR Range % : {levels.get('or_range_pct','N/A')}%")
            print(f"   ORB Target : {levels.get('or_target','N/A')}")
            print(f"   ORB Stop   : {levels.get('or_stop','N/A')}\n")
        else:
            print(f"No 15m data available for {sym}")
        return 0
    except Exception as e:
        log.error("orb failed: %s", e)
        return 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_watchlist() -> list[str]:
    return [
        "SPY", "QQQ", "NVDA", "AMD", "MSFT", "AAPL",
        "AMZN", "META", "GOOGL", "TSM", "AVGO",
    ]


def _send_scan_v2_alert(results, regime: str) -> None:
    try:
        from martin_quant.utils.alert_manager import AlertManager
        am  = AlertManager()
        top = results[:5]
        lines = [f"🔍 *ScanV2 {regime}* — {len(results)} signals"]
        for i, r in enumerate(top, 1):
            orb_tag = " 🟢ORB" if r.orb_signal else ""
            lines.append(f"{i}. {r.symbol} {r.setup_type} score={r.total_score:.3f}{orb_tag}")
        am.send_message("\n".join(lines))
    except Exception as e:
        log.debug("Telegram alert skipped: %s", e)


# ---------------------------------------------------------------------------
# Argument Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="martin-scan",
        description="Martin Quant CLI v2 — Daily scan, review & analysis",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Run V1 daily scan")
    p_scan.add_argument("--no-alerts", action="store_true")

    # scan-v2
    p_v2 = sub.add_parser("scan-v2", help="Run V2 scan (AVWAP + Sector + ORB)")
    p_v2.add_argument("--regime",  default="BULL", help="BULL/WEAK_BULL/CHOPPY/BEAR")
    p_v2.add_argument("--symbols", default="",    help="Comma-separated symbols")
    p_v2.add_argument("--equity",  default="100000", help="Account equity")
    p_v2.add_argument("--no-alerts", action="store_true")

    # review
    p_rev = sub.add_parser("review", help="Print P&L review")
    p_rev.add_argument("--weeks", type=int, default=1)
    p_rev.add_argument("--csv",   default="trades.csv")

    # report
    p_rpt = sub.add_parser("report", help="Generate Markdown weekly report")
    p_rpt.add_argument("--weeks",    type=int, default=1)
    p_rpt.add_argument("--csv",      default="trades.csv")
    p_rpt.add_argument("--telegram", action="store_true")

    # regime
    sub.add_parser("regime", help="Show current market regime")

    # sectors
    p_sec = sub.add_parser("sectors", help="Show sector rankings for regime")
    p_sec.add_argument("--regime", default="BULL")

    # orb
    p_orb = sub.add_parser("orb", help="Show ORB levels for a symbol")
    p_orb.add_argument("--symbol", required=True)
    p_orb.add_argument("--equity", default="100000")

    return parser


COMMANDS = {
    "scan":     cmd_scan,
    "scan-v2":  cmd_scan_v2,
    "review":   cmd_review,
    "report":   cmd_report,
    "regime":   cmd_regime,
    "sectors":  cmd_sectors,
    "orb":      cmd_orb,
}


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args   = parser.parse_args(argv)
    _setup_logging(verbose=args.verbose)
    _load_config()
    handler = COMMANDS.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
