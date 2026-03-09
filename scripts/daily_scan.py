#!/usr/bin/env python3
"""
daily_scan.py
-------------
Full end-to-end daily scan pipeline:

  1. Load watchlist from data/outputs/watchlist.csv
  2. Fetch / load daily OHLCV for all symbols
  3. Run PullbackSetupDetector  + BreakoutSetupDetector
  4. Run ReclaimTrigger + OpeningRangeTrigger + AvwapReclaimTrigger  (on 15m data)
  5. Size positions via PositionSizer
  6. Check PortfolioLimitsChecker
  7. Save signals to data/outputs/signals.parquet and signals.csv
  8. Print a ranked signal summary to stdout

Usage:
  python scripts/daily_scan.py [--config config/research.yaml] [--equity 100000]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from martin_quant.core.datatypes import TriggerSignal
from martin_quant.setups.breakout_setup import BreakoutConfig, BreakoutSetupDetector
from martin_quant.setups.pullback_setup import PullbackConfig, PullbackSetupDetector
from martin_quant.timing.avwap_reclaim_trigger import AvwapReclaimConfig, AvwapReclaimTrigger
from martin_quant.timing.opening_range_trigger import OrbConfig, OpeningRangeTrigger
from martin_quant.timing.reclaim_trigger import ReclaimConfig, ReclaimTrigger
from martin_quant.risk.position_sizer import PositionSizer, PositionSizerConfig
from martin_quant.risk.portfolio_limits import (
    OpenPosition,
    PortfolioLimitsChecker,
    PortfolioLimitsConfig,
)

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("daily_scan")

# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Martin Quant · Daily Scan")
    p.add_argument("--config",  default=str(ROOT / "config" / "research.yaml"))
    p.add_argument("--equity",  type=float, default=100_000.0)
    p.add_argument("--outdir",  default=str(ROOT / "data" / "outputs"))
    p.add_argument("--daily-dir",  default=str(ROOT / "data" / "outputs" / "daily"))
    p.add_argument("--intraday-dir", default=str(ROOT / "data" / "outputs" / "15m"))
    p.add_argument("--1h-dir",  default=str(ROOT / "data" / "outputs" / "1h"))
    return p.parse_args()


# --------------------------------------------------------------------------- #
# Data loaders
# --------------------------------------------------------------------------- #

def load_watchlist(outdir: Path) -> list[str]:
    path = outdir / "watchlist.csv"
    if not path.exists():
        log.warning("watchlist.csv not found at %s", path)
        return []
    df = pd.read_csv(path)
    return df["symbol"].str.upper().tolist() if "symbol" in df.columns else []


def load_ohlcv_map(
    symbols: list[str],
    data_dir: Path,
    suffix: str = "_daily.parquet",
) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        path = data_dir / f"{sym}{suffix}"
        if path.exists():
            df = pd.read_parquet(path)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            result[sym] = df
    log.info("Loaded %d OHLCV files from %s", len(result), data_dir)
    return result


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #

def run_setup_scan(
    symbols: list[str],
    ohlcv_daily: dict[str, pd.DataFrame],
    cfg: dict,
) -> dict[str, object]:
    pb_cfg = PullbackConfig(
        min_pullback_depth_pct=cfg.get("pullback_min_depth_pct", 5.0),
        max_pullback_depth_pct=cfg.get("pullback_max_depth_pct", 25.0),
        max_support_distance_pct=cfg.get("pullback_max_support_dist_pct", 2.5),
        require_close_above_ema50=cfg.get("require_close_above_ema50", True),
        require_ema_stack=cfg.get("require_ema_stack", True),
    )
    bo_cfg = BreakoutConfig(
        lookback_high_days=cfg.get("breakout_lookback_high_days", 20),
        min_base_days=cfg.get("breakout_min_base_days", 3),
        min_rvol_on_breakout=cfg.get("breakout_min_rvol", 1.5),
    )

    pb_signals = PullbackSetupDetector(pb_cfg).scan_universe(symbols, ohlcv_daily)
    bo_signals = BreakoutSetupDetector(bo_cfg).scan_universe(symbols, ohlcv_daily)

    log.info("Setup scan: %d pullback | %d breakout", len(pb_signals), len(bo_signals))
    setup_map = {s.symbol: s for s in pb_signals + bo_signals}
    return setup_map


def run_trigger_scan(
    symbols: list[str],
    ohlcv_15m: dict[str, pd.DataFrame],
    ohlcv_1h: dict[str, pd.DataFrame],
    setup_map: dict,
    cfg: dict,
) -> list[TriggerSignal]:
    reclaim_cfg = ReclaimConfig(
        min_trigger_rvol=cfg.get("reclaim_min_rvol", 1.5),
        max_stop_distance_pct=cfg.get("max_stop_distance_pct", 5.0),
    )
    orb_cfg = OrbConfig(
        orb_minutes=cfg.get("orb_minutes", 30),
        min_trigger_rvol=cfg.get("orb_min_rvol", 1.5),
        max_stop_distance_pct=cfg.get("max_stop_distance_pct", 5.0),
    )
    avwap_cfg = AvwapReclaimConfig(
        min_trigger_rvol=cfg.get("avwap_min_rvol", 1.2),
        max_stop_distance_pct=cfg.get("max_stop_distance_pct", 5.0),
    )

    reclaim_sigs = ReclaimTrigger(reclaim_cfg).scan_universe(
        symbols, ohlcv_15m, ohlcv_1h, setup_map
    )
    orb_sigs = OpeningRangeTrigger(orb_cfg).scan_universe(
        symbols, ohlcv_15m, ohlcv_1h, setup_map
    )
    avwap_sigs = AvwapReclaimTrigger(avwap_cfg).scan_universe(
        symbols, ohlcv_15m, ohlcv_1h, setup_map
    )

    all_triggers = reclaim_sigs + orb_sigs + avwap_sigs
    log.info(
        "Trigger scan: %d reclaim | %d ORB | %d AVWAP | total %d",
        len(reclaim_sigs), len(orb_sigs), len(avwap_sigs), len(all_triggers),
    )
    return sorted(all_triggers, key=lambda s: s.score, reverse=True)


def apply_risk_filters(
    triggers: list[TriggerSignal],
    equity: float,
    watchlist_df: pd.DataFrame,
    sizer_cfg: PositionSizerConfig,
    limits_cfg: PortfolioLimitsConfig,
) -> list[dict]:
    sizer   = PositionSizer(sizer_cfg)
    checker = PortfolioLimitsChecker(limits_cfg)
    output_rows: list[dict] = []

    meta = (
        watchlist_df.set_index("symbol")[["sector"]].to_dict(orient="index")
        if not watchlist_df.empty and "sector" in watchlist_df.columns
        else {}
    )

    for sig in triggers:
        if sig.entry_price is None or sig.stop_price is None:
            continue

        size_result = sizer.size(
            symbol=sig.symbol,
            equity=equity,
            entry_price=sig.entry_price,
            stop_price=sig.stop_price,
            target_price=sig.target_price,
        )
        if size_result is None:
            continue

        sector = meta.get(sig.symbol.upper(), {}).get("sector", "Unknown")
        can_trade, reasons = checker.can_add_trade(
            symbol=sig.symbol,
            sector=sector,
            new_position_value=size_result.position_value,
            equity=equity,
        )
        if not can_trade:
            log.debug("Skipping %s: %s", sig.symbol, reasons)
            continue

        checker.add_position(OpenPosition(
            symbol=sig.symbol,
            sector=sector,
            position_value=size_result.position_value,
            entry_price=sig.entry_price,
            shares=size_result.shares,
        ))

        row = sig.to_dict()
        row.update(size_result.to_dict())
        row["sector"] = sector
        row["scan_timestamp"] = datetime.now(timezone.utc).isoformat()
        output_rows.append(row)

    return output_rows


# --------------------------------------------------------------------------- #
# Save
# --------------------------------------------------------------------------- #

def save_signals(rows: list[dict], outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    if not rows:
        log.warning("No signals to save.")
        return
    df = pd.DataFrame(rows)
    df.to_csv(outdir / "signals.csv", index=False)
    df.to_parquet(outdir / "signals.parquet", index=False)
    log.info("Saved %d signals to %s", len(df), outdir)


# --------------------------------------------------------------------------- #
# Print summary
# --------------------------------------------------------------------------- #

def print_summary(rows: list[dict]) -> None:
    if not rows:
        print("\n  No actionable signals today.\n")
        return

    print("\n" + "=" * 90)
    print(f"  DAILY SCAN SUMMARY  ·  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 90)
    header = f"{'#':<3} {'Symbol':<8} {'Type':<8} {'Trigger':<22} {'Entry':>7} {'Stop':>7} {'Target':>7} {'Shares':>6} {'Risk$':>7} {'Score':>5}"
    print(header)
    print("-" * 90)
    for i, r in enumerate(rows, 1):
        print(
            f"{i:<3} {r.get('symbol',''):<8} "
            f"{r.get('linked_setup_type',''):<8} "
            f"{r.get('trigger_type',''):<22} "
            f"{r.get('entry_price', 0):>7.2f} "
            f"{r.get('stop_price', 0):>7.2f} "
            f"{r.get('target_price', 0):>7.2f} "
            f"{r.get('shares', 0):>6} "
            f"{r.get('risk_per_trade', 0):>7.0f} "
            f"{r.get('score', 0):>5.3f}"
        )
    print("=" * 90 + "\n")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    args    = parse_args()
    outdir  = Path(args.outdir)
    daily_dir   = Path(args.daily_dir)
    intraday_dir = Path(args.intraday_dir)
    h1_dir  = Path(getattr(args, "1h_dir", str(ROOT / "data" / "outputs" / "1h")))

    # Load config
    cfg_path = Path(args.config)
    cfg: dict = {}
    if cfg_path.exists():
        with cfg_path.open() as f:
            cfg = yaml.safe_load(f) or {}
        log.info("Loaded config from %s", cfg_path)

    equity = args.equity
    log.info("Starting daily scan  |  equity=$%.0f", equity)

    # 1. Watchlist
    symbols = load_watchlist(outdir)
    if not symbols:
        log.error("No symbols in watchlist. Run update_universe_and_data.py first.")
        sys.exit(1)
    log.info("Universe: %d symbols", len(symbols))

    watchlist_df_path = outdir / "watchlist.csv"
    watchlist_df = pd.read_csv(watchlist_df_path) if watchlist_df_path.exists() else pd.DataFrame()

    # 2. OHLCV
    ohlcv_daily   = load_ohlcv_map(symbols, daily_dir,    suffix="_daily.parquet")
    ohlcv_15m     = load_ohlcv_map(symbols, intraday_dir, suffix="_15m.parquet")
    ohlcv_1h      = load_ohlcv_map(symbols, h1_dir,       suffix="_1h.parquet")

    # 3. Setup scan
    setup_map = run_setup_scan(symbols, ohlcv_daily, cfg)

    # 4. Trigger scan
    triggers = run_trigger_scan(
        symbols=symbols,
        ohlcv_15m=ohlcv_15m,
        ohlcv_1h=ohlcv_1h,
        setup_map=setup_map,
        cfg=cfg,
    )

    # 5 & 6. Risk filters
    sizer_cfg = PositionSizerConfig(
        per_trade_risk_pct=cfg.get("per_trade_risk_pct", 0.5),
        max_position_pct=cfg.get("max_position_pct", 30.0),
    )
    limits_cfg = PortfolioLimitsConfig(
        max_open_trades=cfg.get("max_open_trades", 10),
        max_sector_concentration_pct=cfg.get("max_sector_concentration_pct", 40.0),
        max_gross_exposure_pct=cfg.get("max_gross_exposure_pct", 280.0),
    )
    output_rows = apply_risk_filters(
        triggers=triggers,
        equity=equity,
        watchlist_df=watchlist_df,
        sizer_cfg=sizer_cfg,
        limits_cfg=limits_cfg,
    )

    # 7. Save
    save_signals(output_rows, outdir)

    # 8. Print
    print_summary(output_rows)


if __name__ == "__main__":
    main()
