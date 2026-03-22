#!/usr/bin/env python
"""update_universe_and_data.py

End-to-end pipeline:
  1. Run Finviz screens from config/research.yaml -> candidate symbols
  2. Fetch Finnhub metadata + earnings
  3. Fetch daily OHLCV from Polygon
  4. Fallback to yfinance for hourly / 15m if Polygon intraday is unavailable
  5. Write parquet files to data_root
  6. Write auto_candidates.txt for downstream Martin pipeline
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import yaml
import yfinance as yf

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

from martin_quant.data.providers.finviz_provider import (
    FinvizProvider,
    FinvizProviderConfig,
    FinvizScreenDefinition,
)
from martin_quant.data.providers.finnhub_provider import (
    FinnhubProvider,
    FinnhubProviderConfig,
)


POLYGON_BASE_URL = "https://api.polygon.io/v2/aggs/ticker"
TWELVEDATA_BASE_URL = "https://api.twelvedata.com/time_series"
DEFAULT_POLYGON_TIMEOUT = 30
DEFAULT_POLYGON_PAUSE_SECONDS = 12.0
DEFAULT_POLYGON_MAX_RETRIES = 4
EXPECTED_TIMEFRAMES = ("1d", "1h", "15m")
TIMEFRAME_ORDER = {tf: idx for idx, tf in enumerate(EXPECTED_TIMEFRAMES)}
STATE_COLUMNS = [
    "symbol",
    "status",
    "last_action",
    "attempts",
    "last_started_at",
    "last_finished_at",
    "existing_timeframes",
    "written_timeframes",
    "error",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update candidate universe via Finviz, metadata/earnings via Finnhub, and OHLCV via Polygon/yfinance."
    )
    parser.add_argument("--config", default="config/research.yaml")
    parser.add_argument("--data-root", default="")
    parser.add_argument("--progress-dir", default="")
    parser.add_argument("--max-symbols", type=int, default=150)
    parser.add_argument("--per-screen-cap", type=int, default=40)
    parser.add_argument("--earnings-lookahead-days", type=int, default=45)
    parser.add_argument("--daily-days", type=int, default=450)
    parser.add_argument("--hourly-days", type=int, default=120)
    parser.add_argument("--m15-days", type=int, default=45)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip symbols whose local 1d/1h/15m parquet files are already present and readable.",
    )
    parser.add_argument(
        "--allow-partial-frames",
        action="store_true",
        help="Allow a symbol to be marked successful even if one or more required timeframes are missing.",
    )
    return parser.parse_args()


def load_env_file() -> None:
    if load_dotenv is not None:
        load_dotenv(override=True)


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


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable not set: {name}")
    return value


def utc_now_iso() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def sort_timeframes(timeframes: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    return sorted({str(tf) for tf in timeframes if tf}, key=lambda tf: TIMEFRAME_ORDER.get(tf, 999))


def serialize_timeframes(timeframes: list[str] | tuple[str, ...] | set[str]) -> str:
    ordered = sort_timeframes(timeframes)
    return ",".join(ordered)


def atomic_write_text(path: str | Path, content: str) -> None:
    final_path = Path(path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_name(f".{final_path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        os.replace(temp_path, final_path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def atomic_write_dataframe(df: pd.DataFrame, path: str | Path) -> None:
    final_path = Path(path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_name(f".{final_path.name}.{os.getpid()}.tmp")
    try:
        suffix = final_path.suffix.lower()
        if suffix == ".csv":
            df.to_csv(temp_path, index=False)
        elif suffix == ".parquet":
            df.to_parquet(temp_path, index=False)
        else:
            raise ValueError(f"Unsupported dataframe output path: {final_path}")
        os.replace(temp_path, final_path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def write_report_csv(path: str | Path, rows: list[dict[str, object]], columns: list[str]) -> None:
    if rows:
        df = pd.DataFrame(rows)
        for column in columns:
            if column not in df.columns:
                df[column] = ""
        df = df[columns].copy()
    else:
        df = pd.DataFrame(columns=columns)
    atomic_write_dataframe(df, path)


def write_table_outputs(root: Path, name: str, df: pd.DataFrame) -> None:
    atomic_write_dataframe(df, root / f"{name}.parquet")
    atomic_write_dataframe(df, root / f"{name}.csv")


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
    outputs_dir = ensure_dir(project_root / "outputs" / "universe_updates")

    atomic_write_text(universe_dir / "auto_candidates.txt", "\n".join(symbols) + "\n")

    if not candidates_df.empty:
        atomic_write_dataframe(candidates_df, outputs_dir / "finviz_candidates.csv")
        atomic_write_dataframe(candidates_df, outputs_dir / "finviz_candidates.parquet")


def load_refresh_state(path: str | Path) -> dict[str, dict[str, object]]:
    state_path = Path(path)
    if not state_path.exists():
        return {}
    try:
        df = pd.read_csv(state_path)
    except Exception:
        return {}
    if df.empty or "symbol" not in df.columns:
        return {}

    df["symbol"] = df["symbol"].astype(str).str.upper()
    state: dict[str, dict[str, object]] = {}
    for row in df.to_dict(orient="records"):
        symbol = str(row.pop("symbol", "")).upper().strip()
        if not symbol:
            continue
        record: dict[str, object] = {}
        for key, value in row.items():
            if pd.isna(value):
                record[key] = ""
            elif key == "attempts":
                try:
                    record[key] = int(value)
                except (TypeError, ValueError):
                    record[key] = 0
            else:
                record[key] = value
        state[symbol] = record
    return state


def persist_refresh_state(path: str | Path, state: dict[str, dict[str, object]]) -> None:
    rows: list[dict[str, object]] = []
    for symbol in sorted(state):
        row = {column: "" for column in STATE_COLUMNS}
        row["symbol"] = symbol
        row.update(state[symbol])
        rows.append(row)
    atomic_write_dataframe(pd.DataFrame(rows, columns=STATE_COLUMNS), path)


def append_progress_event(path: str | Path, event: dict[str, object]) -> None:
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")


def symbol_frame_paths(data_root: Path, symbol: str, timeframe: str) -> list[Path]:
    root = Path(data_root) / timeframe
    symbol_upper = symbol.upper()
    candidates = [
        root / f"{symbol_upper}.parquet",
        root / f"{symbol_upper.replace('-', '_')}.parquet",
    ]
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            deduped.append(candidate)
            seen.add(key)
    return deduped


def parquet_has_rows(path: Path) -> bool:
    try:
        df = pd.read_parquet(path)
    except Exception:
        return False
    return df is not None and not df.empty


def existing_symbol_timeframes(data_root: str | Path, symbol: str) -> list[str]:
    root = Path(data_root)
    found: list[str] = []
    for timeframe in EXPECTED_TIMEFRAMES:
        for path in symbol_frame_paths(root, symbol, timeframe):
            if path.exists() and parquet_has_rows(path):
                found.append(timeframe)
                break
    return found


def symbol_refresh_complete(data_root: str | Path, symbol: str) -> bool:
    return set(existing_symbol_timeframes(data_root, symbol)) >= set(EXPECTED_TIMEFRAMES)


def build_state_row(
    prior_state: dict[str, object],
    status: str,
    last_action: str,
    started_at: str,
    finished_at: str,
    existing_timeframes: list[str],
    written_timeframes: list[str],
    error: str = "",
    increment_attempt: bool = False,
) -> dict[str, object]:
    attempts = int(prior_state.get("attempts", 0) or 0)
    if increment_attempt:
        attempts += 1
    return {
        "status": status,
        "last_action": last_action,
        "attempts": attempts,
        "last_started_at": started_at,
        "last_finished_at": finished_at,
        "existing_timeframes": serialize_timeframes(existing_timeframes),
        "written_timeframes": serialize_timeframes(written_timeframes),
        "error": error,
    }


def mask_error(message: object, secrets: list[str]) -> str:
    masked = str(message)
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, "***")
    return masked


def _polygon_range_url(symbol: str, multiplier: int, timespan: str, start_date: str, end_date: str) -> str:
    return f"{POLYGON_BASE_URL}/{symbol.upper()}/range/{multiplier}/{timespan}/{start_date}/{end_date}"


def _polygon_get_json(url: str, params: dict[str, object], timeout: int) -> dict:
    last_error: Exception | None = None
    for attempt in range(DEFAULT_POLYGON_MAX_RETRIES + 1):
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "")
            try:
                sleep_seconds = float(retry_after)
            except ValueError:
                sleep_seconds = DEFAULT_POLYGON_PAUSE_SECONDS * max(1, attempt + 1)
            time.sleep(max(sleep_seconds, DEFAULT_POLYGON_PAUSE_SECONDS))
            last_error = requests.HTTPError(f"429 Too Many Requests for url: {resp.url}")
            continue
        try:
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_error = exc
            break
    if last_error is None:
        raise RuntimeError("Polygon request failed without a response")
    raise last_error


def fetch_polygon_ohlcv(
    symbol: str,
    multiplier: int,
    timespan: str,
    start_date: str,
    end_date: str,
    api_key: str,
    timeout: int = DEFAULT_POLYGON_TIMEOUT,
) -> pd.DataFrame:
    payload = _polygon_get_json(
        _polygon_range_url(symbol, multiplier, timespan, start_date, end_date),
        {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": api_key,
        },
        timeout=timeout,
    )
    results = payload.get("results", [])
    if payload.get("status") != "OK" or not results:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    if timespan == "day":
        time.sleep(float(os.getenv("POLYGON_PAUSE_SECONDS", str(DEFAULT_POLYGON_PAUSE_SECONDS))))

    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime([row["t"] for row in results], unit="ms", utc=True),
            "open": [row["o"] for row in results],
            "high": [row["h"] for row in results],
            "low": [row["l"] for row in results],
            "close": [row["c"] for row in results],
            "volume": [row["v"] for row in results],
        }
    ).sort_values("timestamp").reset_index(drop=True)


def fetch_yfinance_ohlcv(symbol: str, interval: str, period: str) -> pd.DataFrame:
    raw = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True, prepost=False)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    ts = pd.to_datetime(df.index)
    if getattr(ts, "tz", None) is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    df.insert(0, "timestamp", ts)
    return df.reset_index(drop=True)


def empty_ohlcv_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])


def safe_fetch_ohlcv_frame(fetcher, *args, **kwargs) -> pd.DataFrame:
    try:
        df = fetcher(*args, **kwargs)
    except Exception:
        return empty_ohlcv_frame()
    if df is None:
        return empty_ohlcv_frame()
    return df


def twelvedata_outputsize(timeframe: str, daily_days: int, hourly_days: int, m15_days: int) -> int:
    if timeframe == "1d":
        return min(max(daily_days, 30), 5000)
    if timeframe == "1h":
        return min(max(hourly_days * 8, 120), 5000)
    if timeframe == "15m":
        return min(max(m15_days * 26, 240), 5000)
    return 500


def fetch_twelvedata_ohlcv(
    symbol: str,
    interval: str,
    outputsize: int,
    api_key: str,
    timeout: int = DEFAULT_POLYGON_TIMEOUT,
) -> pd.DataFrame:
    if not api_key:
        return empty_ohlcv_frame()

    resp = requests.get(
        TWELVEDATA_BASE_URL,
        params={
            "symbol": symbol.upper(),
            "interval": interval,
            "outputsize": int(outputsize),
            "apikey": api_key,
            "format": "JSON",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    values = payload.get("values", []) if isinstance(payload, dict) else []
    if not isinstance(values, list) or not values:
        return empty_ohlcv_frame()

    df = pd.DataFrame(values)
    if df.empty or "datetime" not in df.columns:
        return empty_ohlcv_frame()

    ts = pd.to_datetime(df["datetime"], errors="coerce")
    exchange_tz = str(payload.get("meta", {}).get("exchange_timezone") or "UTC")
    try:
        ts = ts.dt.tz_localize(exchange_tz, ambiguous="NaT", nonexistent="shift_forward")
    except Exception:
        ts = ts.dt.tz_localize("UTC", ambiguous="NaT", nonexistent="shift_forward")
    ts = ts.dt.tz_convert("UTC")

    out = pd.DataFrame(
        {
            "timestamp": ts,
            "open": pd.to_numeric(df.get("open"), errors="coerce"),
            "high": pd.to_numeric(df.get("high"), errors="coerce"),
            "low": pd.to_numeric(df.get("low"), errors="coerce"),
            "close": pd.to_numeric(df.get("close"), errors="coerce"),
            "volume": pd.to_numeric(df.get("volume"), errors="coerce"),
        }
    )
    out = out.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    if out.empty:
        return empty_ohlcv_frame()
    return out.sort_values("timestamp").reset_index(drop=True)


def fetch_market_frames(
    symbol: str,
    api_key: str,
    daily_days: int,
    hourly_days: int,
    m15_days: int,
    finnhub_provider: FinnhubProvider | None = None,
) -> dict[str, pd.DataFrame]:
    today = date.today()

    daily_df = safe_fetch_ohlcv_frame(
        fetch_polygon_ohlcv,
        symbol,
        multiplier=1,
        timespan="day",
        start_date=(today - timedelta(days=max(30, daily_days * 2))).isoformat(),
        end_date=today.isoformat(),
        api_key=api_key,
    ).tail(daily_days).reset_index(drop=True)
    if daily_df.empty:
        daily_df = safe_fetch_ohlcv_frame(symbol=symbol, fetcher=fetch_yfinance_ohlcv, interval="1d", period="2y").tail(daily_days).reset_index(drop=True)

    hourly_df = safe_fetch_ohlcv_frame(
        fetch_polygon_ohlcv,
        symbol,
        multiplier=1,
        timespan="hour",
        start_date=(today - timedelta(days=max(10, hourly_days))).isoformat(),
        end_date=today.isoformat(),
        api_key=api_key,
    )
    if hourly_df.empty:
        hourly_df = safe_fetch_ohlcv_frame(fetch_yfinance_ohlcv, symbol, interval="60m", period="60d")

    m15_df = safe_fetch_ohlcv_frame(
        fetch_polygon_ohlcv,
        symbol,
        multiplier=15,
        timespan="minute",
        start_date=(today - timedelta(days=max(5, m15_days))).isoformat(),
        end_date=today.isoformat(),
        api_key=api_key,
    )
    if m15_df.empty:
        m15_df = safe_fetch_ohlcv_frame(fetch_yfinance_ohlcv, symbol, interval="15m", period="60d")

    frames = {
        "1d": daily_df,
        "1h": hourly_df,
        "15m": m15_df,
    }

    missing_timeframes = [tf for tf, df in frames.items() if df is None or df.empty]
    if missing_timeframes and finnhub_provider is not None:
        try:
            finnhub_frames = finnhub_provider.fetch_ohlcv_frames(
                symbol,
                daily_days=daily_days,
                hourly_days=hourly_days,
                m15_days=m15_days,
            )
        except Exception:
            finnhub_frames = {}
        for timeframe in missing_timeframes:
            fallback_df = finnhub_frames.get(timeframe)
            if fallback_df is not None and not fallback_df.empty:
                frames[timeframe] = fallback_df.reset_index(drop=True)

    missing_timeframes = [tf for tf, df in frames.items() if df is None or df.empty]
    if missing_timeframes:
        twelvedata_api_key = os.getenv("TWELVEDATA_API_KEY", "").strip()
        interval_map = {"1d": "1day", "1h": "1h", "15m": "15min"}
        for timeframe in missing_timeframes:
            fallback_df = safe_fetch_ohlcv_frame(
                fetch_twelvedata_ohlcv,
                symbol,
                interval=interval_map[timeframe],
                outputsize=twelvedata_outputsize(timeframe, daily_days, hourly_days, m15_days),
                api_key=twelvedata_api_key,
            )
            if fallback_df is not None and not fallback_df.empty:
                frames[timeframe] = fallback_df.reset_index(drop=True)

    return frames


def main() -> None:
    load_env_file()
    args = parse_args()
    cfg = load_yaml(args.config)

    project_root = Path(".").resolve()
    data_root = args.data_root or (
        cfg.get("data", {}).get("provider", {}).get("params", {}).get("data_root", "data/us_equities")
    )
    data_root = ensure_dir(data_root)
    progress_dir = ensure_dir(args.progress_dir or (project_root / "outputs" / "universe_updates"))
    state_path = progress_dir / "refresh_state.csv"
    progress_log_path = progress_dir / "symbol_progress.jsonl"
    state = load_refresh_state(state_path)
    run_started_at = utc_now_iso()

    benchmark_symbol = cfg.get("universe", {}).get("benchmark_symbol", "SPY").upper()

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
        .dropna()
        .astype(str)
        .str.upper()
        .drop_duplicates()
        .head(args.max_symbols)
        .tolist()
    )
    if benchmark_symbol not in symbols:
        symbols.append(benchmark_symbol)

    save_candidate_outputs(project_root, candidates_df, symbols)
    print(f"[OK] Finviz: {len(symbols)} candidate symbols")

    finnhub_cfg = cfg.get("providers", {}).get("finnhub", {})
    finnhub = FinnhubProvider(FinnhubProviderConfig(
        api_key=finnhub_cfg.get("api_key", ""),
        api_key_env=finnhub_cfg.get("api_key_env", "FINNHUB_API_KEY"),
        pause_seconds=float(finnhub_cfg.get("pause_seconds", 0.35)),
        timeout=int(finnhub_cfg.get("timeout", 20)),
    ))

    ohlcv_cfg = finnhub_cfg.get("ohlcv", {})
    earnings_cfg = finnhub_cfg.get("earnings", {})

    metadata_df = finnhub.build_metadata_frame(symbols)
    if not metadata_df.empty:
        write_table_outputs(data_root, "metadata", metadata_df)
        print(f"[OK] Finnhub metadata: {len(metadata_df)} rows")

    lookahead = args.earnings_lookahead_days or int(earnings_cfg.get("lookahead_days", 45))
    earnings_df = finnhub.build_earnings_frame(
        symbols=symbols,
        from_date=date.today(),
        to_date=date.today() + timedelta(days=lookahead),
    )
    if not earnings_df.empty:
        write_table_outputs(data_root, "earnings", earnings_df)
        print(f"[OK] Finnhub earnings: {len(earnings_df)} rows")

    polygon_api_key = require_env("POLYGON_API_KEY")
    secrets = [polygon_api_key, getattr(finnhub, "api_key", "")]
    daily_days = args.daily_days or int(ohlcv_cfg.get("daily_days", 450))
    hourly_days = args.hourly_days or int(ohlcv_cfg.get("hourly_days", 120))
    m15_days = args.m15_days or int(ohlcv_cfg.get("m15_days", 45))

    ok: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    total_symbols = len(symbols)

    for index, symbol in enumerate(symbols, start=1):
        prior_state = state.get(symbol, {})
        existing = existing_symbol_timeframes(data_root, symbol)
        prefix = f"[{index}/{total_symbols}]"

        if args.resume and symbol_refresh_complete(data_root, symbol):
            finished_at = utc_now_iso()
            state[symbol] = build_state_row(
                prior_state=prior_state,
                status="ok",
                last_action="resume_skip",
                started_at=str(prior_state.get("last_started_at", "")),
                finished_at=finished_at,
                existing_timeframes=existing,
                written_timeframes=existing,
            )
            persist_refresh_state(state_path, state)
            append_progress_event(
                progress_log_path,
                {
                    "event": "resume_skip",
                    "symbol": symbol,
                    "index": index,
                    "total": total_symbols,
                    "existing_timeframes": sort_timeframes(existing),
                    "finished_at": finished_at,
                },
            )
            skipped.append({"symbol": symbol, "timeframes": serialize_timeframes(existing)})
            print(f"{prefix} SKIP {symbol}: existing frames={serialize_timeframes(existing)}")
            continue

        started_at = utc_now_iso()
        started_perf = time.perf_counter()
        append_progress_event(
            progress_log_path,
            {
                "event": "start",
                "symbol": symbol,
                "index": index,
                "total": total_symbols,
                "existing_timeframes": sort_timeframes(existing),
                "started_at": started_at,
            },
        )
        print(f"{prefix} FETCH {symbol}: existing={serialize_timeframes(existing) or '-'}")

        try:
            frames = fetch_market_frames(
                symbol=symbol,
                api_key=polygon_api_key,
                daily_days=daily_days,
                hourly_days=hourly_days,
                m15_days=m15_days,
                finnhub_provider=finnhub,
            )
            written_timeframes = [tf for tf, df in frames.items() if df is not None and not df.empty]
            missing_required = [tf for tf in EXPECTED_TIMEFRAMES if tf not in written_timeframes]
            if missing_required and not args.allow_partial_frames:
                available = serialize_timeframes(written_timeframes) or "-"
                raise ValueError(
                    f"Missing required OHLCV frames: {', '.join(missing_required)} "
                    f"(available: {available})"
                )
            if not written_timeframes:
                raise ValueError("No OHLCV rows returned from Polygon/yfinance/Finnhub")

            finnhub.write_symbol_parquets(data_root=data_root, symbol=symbol, frames=frames)

            duration_seconds = round(time.perf_counter() - started_perf, 2)
            finished_at = utc_now_iso()
            state[symbol] = build_state_row(
                prior_state=prior_state,
                status="ok",
                last_action="downloaded",
                started_at=started_at,
                finished_at=finished_at,
                existing_timeframes=existing,
                written_timeframes=written_timeframes,
                increment_attempt=True,
            )
            persist_refresh_state(state_path, state)
            append_progress_event(
                progress_log_path,
                {
                    "event": "ok",
                    "symbol": symbol,
                    "index": index,
                    "total": total_symbols,
                    "existing_timeframes": sort_timeframes(existing),
                    "written_timeframes": sort_timeframes(written_timeframes),
                    "duration_seconds": duration_seconds,
                    "finished_at": finished_at,
                },
            )
            ok.append({"symbol": symbol, "timeframes": serialize_timeframes(written_timeframes)})
            print(
                f"{prefix} OK {symbol}: wrote={serialize_timeframes(written_timeframes)} "
                f"duration={duration_seconds:.2f}s"
            )
        except Exception as exc:
            duration_seconds = round(time.perf_counter() - started_perf, 2)
            finished_at = utc_now_iso()
            masked_error = mask_error(exc, secrets)
            state[symbol] = build_state_row(
                prior_state=prior_state,
                status="failed",
                last_action="failed",
                started_at=started_at,
                finished_at=finished_at,
                existing_timeframes=existing,
                written_timeframes=[],
                error=masked_error,
                increment_attempt=True,
            )
            persist_refresh_state(state_path, state)
            append_progress_event(
                progress_log_path,
                {
                    "event": "failed",
                    "symbol": symbol,
                    "index": index,
                    "total": total_symbols,
                    "existing_timeframes": sort_timeframes(existing),
                    "duration_seconds": duration_seconds,
                    "error": masked_error,
                    "finished_at": finished_at,
                },
            )
            failed.append({
                "symbol": symbol,
                "error": masked_error,
                "existing_timeframes": serialize_timeframes(existing),
            })
            print(f"{prefix} FAIL {symbol}: {masked_error}", file=sys.stderr)

    write_report_csv(progress_dir / "download_ok.csv", ok, ["symbol", "timeframes"])
    write_report_csv(progress_dir / "download_skipped.csv", skipped, ["symbol", "timeframes"])
    write_report_csv(progress_dir / "download_failed.csv", failed, ["symbol", "error", "existing_timeframes"])

    summary = {
        "run_started_at": run_started_at,
        "run_finished_at": utc_now_iso(),
        "resume": bool(args.resume),
        "allow_partial_frames": bool(args.allow_partial_frames),
        "symbols_total": total_symbols,
        "downloaded": len(ok),
        "skipped": len(skipped),
        "failed": len(failed),
        "data_root": str(data_root),
        "state_file": str(state_path),
        "progress_log": str(progress_log_path),
    }
    atomic_write_text(progress_dir / "refresh_summary.json", json.dumps(summary, indent=2) + "\n")

    print(f"[OK] OHLCV success: {len(ok)}  skipped: {len(skipped)}  failed: {len(failed)}")
    print(f"[OK] data_root: {data_root}")
    print(f"[OK] refresh state: {state_path}")
    print(f"[OK] progress log: {progress_log_path}")
    print("[OK] universe file: config/universe/auto_candidates.txt")


if __name__ == "__main__":
    main()





