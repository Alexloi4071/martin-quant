#!/usr/bin/env python
"""update_universe_and_data.py

End-to-end pipeline:
  1. Run Finviz screens from config/research.yaml -> candidate symbols
  2. Fetch Finnhub metadata + earnings + OHLCV (1d/1h/15m)
  3. Write parquet files to data_root
  4. Write auto_candidates.txt for downstream Martin pipeline

Usage:
  export FINNHUB_API_KEY="your_key"
  python scripts/update_universe_and_data.py
  python scripts/update_universe_and_data.py --config config/research.yaml --max-symbols 120
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

from martin_quant.data.providers.finviz_provider import (
    FinvizProvider,
    FinvizProviderConfig,
    FinvizScreenDefinition,
)
from martin_quant.data.providers.finnhub_provider import (
    FinnhubProvider,
    FinnhubProviderConfig,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update candidate universe and refresh OHLCV/metadata via Finviz + Finnhub."
    )
    parser.add_argument("--config", default="config/research.yaml")
    parser.add_argument("--data-root", default="")
    parser.add_argument("--max-symbols", type=int, default=150)
    parser.add_argument("--per-screen-cap", type=int, default=40)
    parser.add_argument("--earnings-lookahead-days", type=int, default=45)
    parser.add_argument("--daily-days",  type=int, default=450)
    parser.add_argument("--hourly-days", type=int, default=120)
    parser.add_argument("--m15-days",    type=int, default=45)
    return parser.parse_args()


def load_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def build_screens_from_cfg(cfg: dict) -> list[FinvizScreenDefinition]:
    screens_cfg = cfg.get("providers", {}).get("finviz", {}).get("screens", [])
    if not screens_cfg:
        raise ValueError("No Finviz screens in config. Add providers.finviz.screens to config/research.yaml.")
    result: list[FinvizScreenDefinition] = []
    for item in screens_cfg:
        if not item.get("enabled", True):
            continue
        result.append(FinvizScreenDefinition(
            name=item["name"],
            filters=list(item.get("filters", [])),
            order=item.get("order", "-marketcap"),
            signal=item.get("signal", ""),
            limit=int(item.get("limit", 120)),
        ))
    return result


def cap_symbols_per_screen(df: pd.DataFrame, per_screen_cap: int) -> pd.DataFrame:
    if df.empty or "screen_name" not in df.columns:
        return df
    return (
        df.groupby("screen_name", group_keys=False)
        .head(per_screen_cap)
        .reset_index(drop=True)
    )


def save_candidate_outputs(
    project_root: Path,
    candidates_df: pd.DataFrame,
    symbols: list[str],
) -> None:
    universe_dir = ensure_dir(project_root / "config" / "universe")
    outputs_dir  = ensure_dir(project_root / "outputs" / "universe_updates")

    (universe_dir / "auto_candidates.txt").write_text("\n".join(symbols) + "\n", encoding="utf-8")

    if not candidates_df.empty:
        candidates_df.to_csv(outputs_dir / "finviz_candidates.csv", index=False)
        candidates_df.to_parquet(outputs_dir / "finviz_candidates.parquet", index=False)


def main() -> None:
    args = parse_args()
    cfg  = load_yaml(args.config)

    project_root = Path(".").resolve()
    data_root = args.data_root or (
        cfg.get("data", {}).get("provider", {}).get("params", {}).get("data_root", "data/us_equities")
    )
    data_root = ensure_dir(data_root)

    benchmark_symbol = cfg.get("universe", {}).get("benchmark_symbol", "SPY").upper()

    # ── 1. Finviz: generate candidate pool ───────────────────────────────────
    finviz_cfg = cfg.get("providers", {}).get("finviz", {})
    finviz = FinvizProvider(FinvizProviderConfig(
        pause_seconds=float(finviz_cfg.get("pause_seconds", 0.8)),
        timeout=int(finviz_cfg.get("timeout", 20)),
    ))

    screens = build_screens_from_cfg(cfg)
    candidates_df = finviz.screen_many(screens, dedupe=False)

    if candidates_df.empty or "symbol" not in candidates_df.columns:
        print("[ERROR] No candidate symbols from Finviz.", file=sys.stderr)
        sys.exit(1)

    per_cap = args.per_screen_cap or int(finviz_cfg.get("per_screen_cap", 40))
    candidates_df = cap_symbols_per_screen(candidates_df, per_cap)

    symbols = (
        candidates_df["symbol"]
        .dropna().astype(str).str.upper()
        .drop_duplicates()
        .head(args.max_symbols)
        .tolist()
    )
    if benchmark_symbol not in symbols:
        symbols.append(benchmark_symbol)

    save_candidate_outputs(project_root, candidates_df, symbols)
    print(f"[OK] Finviz: {len(symbols)} candidate symbols")

    # ── 2. Finnhub: metadata + earnings + OHLCV ─────────────────────────────
    finnhub_cfg = cfg.get("providers", {}).get("finnhub", {})
    finnhub = FinnhubProvider(FinnhubProviderConfig(
        api_key=finnhub_cfg.get("api_key", ""),
        api_key_env=finnhub_cfg.get("api_key_env", "FINNHUB_API_KEY"),
        pause_seconds=float(finnhub_cfg.get("pause_seconds", 0.35)),
        timeout=int(finnhub_cfg.get("timeout", 20)),
    ))

    ohlcv_cfg    = finnhub_cfg.get("ohlcv", {})
    earnings_cfg = finnhub_cfg.get("earnings", {})

    metadata_df = finnhub.build_metadata_frame(symbols)
    if not metadata_df.empty:
        metadata_df.to_parquet(data_root / "metadata.parquet", index=False)
        metadata_df.to_csv(data_root / "metadata.csv", index=False)
        print(f"[OK] Finnhub metadata: {len(metadata_df)} rows")

    lookahead = args.earnings_lookahead_days or int(earnings_cfg.get("lookahead_days", 45))
    earnings_df = finnhub.build_earnings_frame(
        symbols=symbols,
        from_date=date.today(),
        to_date=date.today() + timedelta(days=lookahead),
    )
    if not earnings_df.empty:
        earnings_df.to_parquet(data_root / "earnings.parquet", index=False)
        earnings_df.to_csv(data_root / "earnings.csv", index=False)
        print(f"[OK] Finnhub earnings: {len(earnings_df)} rows")

    daily_days  = args.daily_days  or int(ohlcv_cfg.get("daily_days", 450))
    hourly_days = args.hourly_days or int(ohlcv_cfg.get("hourly_days", 120))
    m15_days    = args.m15_days    or int(ohlcv_cfg.get("m15_days", 45))

    ok, failed = [], []
    for symbol in symbols:
        try:
            frames = finnhub.fetch_ohlcv_frames(
                symbol=symbol,
                daily_days=daily_days,
                hourly_days=hourly_days,
                m15_days=m15_days,
            )
            finnhub.write_symbol_parquets(data_root=data_root, symbol=symbol, frames=frames)
            ok.append(symbol)
        except Exception as exc:
            failed.append({"symbol": symbol, "error": str(exc)})

    report_dir = ensure_dir(project_root / "outputs" / "universe_updates")
    pd.DataFrame({"symbol": ok}).to_csv(report_dir / "download_ok.csv", index=False)
    if failed:
        pd.DataFrame(failed).to_csv(report_dir / "download_failed.csv", index=False)

    print(f"[OK] OHLCV success: {len(ok)}  failed: {len(failed)}")
    print(f"[OK] data_root: {data_root}")
    print(f"[OK] universe file: config/universe/auto_candidates.txt")


if __name__ == "__main__":
    main()
