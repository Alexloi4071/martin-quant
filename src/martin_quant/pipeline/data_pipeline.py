"""data_pipeline.py

Data pipeline with two modes:
- `run()` for legacy yfinance-style full fetch
- `fetch()` / `get_sectors()` compatibility methods for local parquet data used by V2
"""
from __future__ import annotations

import os
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

SP500_TICKERS: list[str] = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","BRK-B",
    "JPM","LLY","V","UNH","XOM","MA","JNJ","PG","HD","COST","MRK",
    "ABBV","CVX","BAC","KO","PEP","ADBE","CRM","NFLX","WMT","ACN",
    "MCD","AMD","CSCO","LIN","DIS","ABT","DHR","TXN","INTU","VZ",
    "PM","NEE","RTX","HON","AMGN","SPGI","T","IBM","GE","CAT",
    "GS","BLK","NOW","ISRG","UBER","SHW","AMAT","MU","LRCX","KLAC",
    "PANW","CRWD","SNOW","DDOG","NET","ZS","ENPH","FSLR","CEG","VST",
    "SMCI","MRVL","ARM","PLTR","SOFI","HOOD","COIN","MSTR",
    "SPY","QQQ","IWM","SMH","XLK","XLF","XLE","XLV","XLI","XLY",
]

NASDAQ_GROWTH: list[str] = [
    "CELH","DUOL","CPNG","DOCS","AXON","FTNT","GDDY","CSGP","VEEV",
    "HUBS","APP","TTD","GLBE","SQ","PYPL","SHOP","SPOT","PINS",
    "RBLX","U","RIVN","LCID","NIO","XPEV","LI",
]

DEFAULT_SECTOR_MAP: dict[str, str] = {
    "NVDA": "semiconductors",
    "AMD": "semiconductors",
    "AVGO": "semiconductors",
    "MU": "semiconductors",
    "LRCX": "semiconductors",
    "KLAC": "semiconductors",
    "AMAT": "semiconductors",
    "MRVL": "semiconductors",
    "ARM": "semiconductors",
    "MSFT": "technology",
    "AAPL": "technology",
    "GOOGL": "technology",
    "META": "technology",
    "AMZN": "consumer_discretionary",
    "TSLA": "consumer_discretionary",
    "JPM": "financials",
    "BAC": "financials",
    "GS": "financials",
    "XOM": "energy",
    "CVX": "energy",
    "LLY": "healthcare",
    "UNH": "healthcare",
}

@dataclass
class PipelineData:
    ohlcv_map: dict[str, pd.DataFrame] = field(default_factory=dict)
    spy_df: Optional[pd.DataFrame] = None
    iwm_df: Optional[pd.DataFrame] = None
    metadata: dict[str, dict] = field(default_factory=dict)
    eps_catalyst_set: set[str] = field(default_factory=set)
    premarket_prices: dict[str, float] = field(default_factory=dict)
    premarket_volumes: dict[str, float] = field(default_factory=dict)
    fetch_errors: dict[str, str] = field(default_factory=dict)
    fetched_at: str = ""
    symbols_fetched: int = 0

    def is_valid(self) -> bool:
        return self.spy_df is not None and len(self.ohlcv_map) > 0


class DataPipeline:
    def __init__(
        self,
        cache_dir: str = "data/cache",
        universe: str = "combined",
        custom_symbols: Optional[list[str]] = None,
        max_workers: int = 8,
        cache_ttl_hours: float = 4.0,
        period: str = "1y",
        data_root: Optional[str] = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.universe = universe
        self.custom_symbols = custom_symbols or []
        self.max_workers = max_workers
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self.period = period
        self.data_root = Path(data_root or os.getenv("DATA_ROOT", "data/us_equities"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def build_universe(self) -> list[str]:
        syms: set[str] = set()
        if self.universe in ("sp500", "combined"):
            syms.update(SP500_TICKERS)
        if self.universe in ("growth", "combined"):
            syms.update(NASDAQ_GROWTH)
        syms.update(self.custom_symbols)
        syms.update(["SPY", "IWM", "QQQ", "SMH"])
        return sorted(syms)

    def _cache_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol.replace('-', '_')}.parquet"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return datetime.now() - mtime < self.cache_ttl

    def _load_cache(self, symbol: str) -> Optional[pd.DataFrame]:
        p = self._cache_path(symbol)
        if self._is_fresh(p):
            try:
                return pd.read_parquet(p)
            except Exception:
                return None
        return None

    def _save_cache(self, symbol: str, df: pd.DataFrame) -> None:
        try:
            df.to_parquet(self._cache_path(symbol))
        except Exception as e:
            log.debug("Cache save failed %s: %s", symbol, e)

    def _fetch_one(self, symbol: str, force: bool = False) -> tuple[str, Optional[pd.DataFrame]]:
        if not force:
            cached = self._load_cache(symbol)
            if cached is not None:
                return symbol, cached

        try:
            import yfinance as yf
            df = yf.download(symbol, period=self.period, progress=False, auto_adjust=True)
            if df is not None and len(df) >= 20:
                df.columns = [str(c).lower() for c in df.columns]
                if "date" in df.columns and "timestamp" not in df.columns:
                    df = df.rename(columns={"date": "timestamp"})
                if "timestamp" not in df.columns:
                    df = df.reset_index().rename(columns={df.index.name or "Date": "timestamp", "Date": "timestamp"})
                self._save_cache(symbol, df)
                return symbol, df
        except Exception as e:
            log.debug("yfinance failed %s: %s", symbol, e)

        return symbol, None

    def _fetch_metadata(self, symbols: list[str]) -> dict[str, dict]:
        meta: dict[str, dict] = {}
        metadata_map = self._load_metadata_map()
        if metadata_map:
            for sym in symbols:
                meta[sym] = metadata_map.get(sym.upper(), {"sector": "", "market_cap": 0, "theme": "", "eps_date": ""})
            return meta

        try:
            import yfinance as yf
            for sym in symbols:
                try:
                    info = yf.Ticker(sym).fast_info
                    meta[sym] = {
                        "sector": getattr(info, "sector", ""),
                        "market_cap": getattr(info, "market_cap", 0),
                        "theme": "",
                        "eps_date": "",
                    }
                except Exception:
                    meta[sym] = {"sector": "", "market_cap": 0, "theme": "", "eps_date": ""}
        except ImportError:
            log.warning("yfinance not available for metadata fetch")
        return meta

    def _build_eps_set(self, symbols: list[str], lookahead_days: int = 3) -> set[str]:
        earnings_path = self.data_root / "earnings.parquet"
        if earnings_path.exists():
            try:
                df = pd.read_parquet(earnings_path)
                if not df.empty and "symbol" in df.columns and "earnings_date" in df.columns:
                    today = pd.Timestamp.utcnow().normalize()
                    future = today + pd.Timedelta(days=lookahead_days)
                    ed = pd.to_datetime(df["earnings_date"], utc=True, errors="coerce")
                    mask = ed.notna() & (ed >= today) & (ed <= future)
                    return set(df.loc[mask, "symbol"].astype(str).str.upper())
            except Exception:
                pass

        eps_set: set[str] = set()
        try:
            import yfinance as yf
            from datetime import date
            today = date.today()
            for sym in symbols:
                try:
                    cal = yf.Ticker(sym).calendar
                    if cal is not None and not cal.empty and "Earnings Date" in cal.index:
                        ed = cal.loc["Earnings Date"].iloc[0]
                        if hasattr(ed, "date"):
                            ed = ed.date()
                        if 0 <= (ed - today).days <= lookahead_days:
                            eps_set.add(sym)
                except Exception:
                    pass
        except ImportError:
            pass
        return eps_set

    def _read_local_frame(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        root = self.data_root / timeframe
        candidates = [
            root / f"{symbol.upper()}.parquet",
            root / f"{symbol.upper().replace('-', '_')}.parquet",
        ]
        for path in candidates:
            if not path.exists():
                continue
            try:
                df = pd.read_parquet(path)
                df = df.copy()
                df.columns = [str(c).lower() for c in df.columns]
                if "date" in df.columns and "timestamp" not in df.columns:
                    df = df.rename(columns={"date": "timestamp"})
                if "timestamp" not in df.columns and df.index.name:
                    df = df.reset_index().rename(columns={df.index.name: "timestamp"})
                if "timestamp" in df.columns:
                    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
                required = {"open", "high", "low", "close", "volume"}
                if not required.issubset(set(df.columns)):
                    return None
                return df.sort_values("timestamp" if "timestamp" in df.columns else df.columns[0]).reset_index(drop=True)
            except Exception as e:
                log.debug("Failed reading %s: %s", path, e)
        return None

    def _available_local_symbols(self) -> list[str]:
        root = self.data_root / "1d"
        if not root.exists():
            return []
        return sorted({p.stem.replace("_", "-").upper() for p in root.glob("*.parquet")})

    def _load_metadata_map(self) -> dict[str, dict]:
        for path in (self.data_root / "metadata.parquet", self.data_root / "metadata.csv"):
            if not path.exists():
                continue
            try:
                df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
                if df.empty or "symbol" not in df.columns:
                    continue
                df["symbol"] = df["symbol"].astype(str).str.upper()
                result: dict[str, dict] = {}
                for _, row in df.iterrows():
                    result[row["symbol"]] = {
                        "sector": row.get("sector", "") if hasattr(row, "get") else row["sector"] if "sector" in df.columns else "",
                        "market_cap": row.get("market_cap", 0) if hasattr(row, "get") else row["market_cap"] if "market_cap" in df.columns else 0,
                        "theme": row.get("theme", "") if hasattr(row, "get") else row["theme"] if "theme" in df.columns else "",
                        "industry": row.get("industry", "") if hasattr(row, "get") else row["industry"] if "industry" in df.columns else "",
                    }
                return result
            except Exception as e:
                log.debug("Failed loading metadata map from %s: %s", path, e)
        return {}

    def fetch(
        self,
        symbols: Optional[list[str]] = None,
        fetch_intraday: bool = True,
        intraday_interval: str = "15m",
    ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
        requested = [s.upper() for s in (symbols or self._available_local_symbols())]
        daily_map: dict[str, pd.DataFrame] = {}
        intraday_map: dict[str, pd.DataFrame] = {}

        for sym in requested:
            daily_df = self._read_local_frame(sym, "1d")
            if daily_df is not None and not daily_df.empty:
                daily_map[sym] = daily_df
            if fetch_intraday:
                tf = intraday_interval.lower()
                intraday_df = self._read_local_frame(sym, tf)
                if intraday_df is not None and not intraday_df.empty:
                    intraday_map[sym] = intraday_df

        return daily_map, intraday_map

    def get_sectors(self, symbols: list[str]) -> dict[str, str]:
        meta = self._load_metadata_map()
        sectors: dict[str, str] = {}
        for sym in symbols:
            entry = meta.get(sym.upper(), {})
            sector = str(entry.get("sector") or entry.get("industry") or DEFAULT_SECTOR_MAP.get(sym.upper(), ""))
            sectors[sym.upper()] = sector
        return sectors

    def get_metadata(self, symbols: list[str]) -> dict[str, dict]:
        meta = self._load_metadata_map()
        result: dict[str, dict] = {}
        for sym in symbols:
            entry = dict(meta.get(sym.upper(), {}))
            result[sym.upper()] = entry
        return result

    def run(
        self,
        force_refresh: bool = False,
        include_metadata: bool = True,
        include_earnings: bool = True,
        premarket_data: Optional[dict[str, float]] = None,
    ) -> PipelineData:
        symbols = self.build_universe()
        log.info("Pipeline: fetching %d symbols (universe=%s)", len(symbols), self.universe)

        t0 = time.time()
        ohlcv_map: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._fetch_one, sym, force_refresh): sym for sym in symbols}
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    _, df = future.result()
                    if df is not None:
                        ohlcv_map[sym] = df
                    else:
                        errors[sym] = "no_data"
                except Exception as e:
                    errors[sym] = str(e)

        elapsed = time.time() - t0
        log.info("Fetched %d / %d symbols in %.1fs (%d errors)", len(ohlcv_map), len(symbols), elapsed, len(errors))

        spy_df = ohlcv_map.get("SPY")
        iwm_df = ohlcv_map.get("IWM")
        scan_symbols = [s for s in ohlcv_map if s not in {"SPY", "IWM", "QQQ", "SMH", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY"}]
        scan_ohlcv = {s: ohlcv_map[s] for s in scan_symbols}

        meta: dict[str, dict] = {}
        if include_metadata:
            log.info("Fetching metadata...")
            meta = self._fetch_metadata(scan_symbols[:100])

        eps_set: set[str] = set()
        if include_earnings:
            log.info("Checking earnings calendar...")
            eps_set = self._build_eps_set(scan_symbols[:50])
            log.info("EPS catalysts this week: %s", eps_set)

        return PipelineData(
            ohlcv_map=scan_ohlcv,
            spy_df=spy_df,
            iwm_df=iwm_df,
            metadata=meta,
            eps_catalyst_set=eps_set,
            premarket_prices=premarket_data or {},
            fetch_errors=errors,
            fetched_at=datetime.now().isoformat(),
            symbols_fetched=len(ohlcv_map),
        )

    def run_scan_from_pipeline(self, equity: float = 100_000.0, force_refresh: bool = False) -> None:
        data = self.run(force_refresh=force_refresh)
        if not data.is_valid():
            log.error("Pipeline returned invalid data: SPY missing")
            return

        from martin_quant.daily_scan import DailyScanner, DailyScanConfig
        scanner = DailyScanner(config=DailyScanConfig(equity=equity))
        result = scanner.run(
            spy_df=data.spy_df,
            iwm_df=data.iwm_df,
            ohlcv_map=data.ohlcv_map,
            metadata=data.metadata,
            eps_catalyst_set=data.eps_catalyst_set,
            premarket_prices=data.premarket_prices,
        )
        print(result.summary())
        df = result.to_dataframe()
        if not df.empty:
            df.to_csv(f"scan_{result.date}.csv", index=False)
            log.info("Saved scan_%s.csv", result.date)



