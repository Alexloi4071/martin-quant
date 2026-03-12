"""data_pipeline.py

Automated data pipeline — fetches all OHLCV, metadata, and earnings data
needed for the daily scan.

Features:
  - Auto-builds watchlist from S&P500 + NASDAQ100 + custom universe
  - Concurrent OHLCV fetch (ThreadPoolExecutor)
  - Cached to local Parquet (no re-fetch if data fresh < 4h)
  - Earnings calendar integration (from yfinance or local CSV)
  - Pre-market price snapshot
  - Error handling + partial result recovery

Usage:
    pipeline = DataPipeline(cache_dir="data/cache", universe="sp500")
    data = pipeline.run()
    # data.ohlcv_map, data.spy_df, data.iwm_df, data.metadata,
    # data.eps_catalyst_set, data.premarket_prices
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

# Default universe lists
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


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class DataPipeline:
    """
    Automated daily data fetcher.

    Parameters
    ----------
    cache_dir : str
        Directory to cache Parquet files
    universe : str
        "sp500" | "growth" | "combined" | "custom"
    custom_symbols : list[str]
        Extra symbols to add to universe
    max_workers : int
        Concurrent fetch threads
    cache_ttl_hours : float
        Hours before cached data is considered stale
    """

    def __init__(
        self,
        cache_dir: str = "data/cache",
        universe: str = "combined",
        custom_symbols: Optional[list[str]] = None,
        max_workers: int = 8,
        cache_ttl_hours: float = 4.0,
        period: str = "1y",
    ) -> None:
        self.cache_dir       = Path(cache_dir)
        self.universe        = universe
        self.custom_symbols  = custom_symbols or []
        self.max_workers     = max_workers
        self.cache_ttl       = timedelta(hours=cache_ttl_hours)
        self.period          = period
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Universe builder
    # ------------------------------------------------------------------

    def build_universe(self) -> list[str]:
        syms: set[str] = set()
        if self.universe in ("sp500", "combined"):
            syms.update(SP500_TICKERS)
        if self.universe in ("growth", "combined"):
            syms.update(NASDAQ_GROWTH)
        syms.update(self.custom_symbols)
        # Always include index ETFs
        syms.update(["SPY", "IWM", "QQQ", "SMH"])
        return sorted(syms)

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Fetch single symbol
    # ------------------------------------------------------------------

    def _fetch_one(
        self,
        symbol: str,
        force: bool = False,
    ) -> tuple[str, Optional[pd.DataFrame]]:
        if not force:
            cached = self._load_cache(symbol)
            if cached is not None:
                return symbol, cached

        # Try provider
        try:
            from martin_quant.data.providers import get_provider
            provider = get_provider()
            df = provider.get_ohlcv(symbol, period=self.period)
            if df is not None and len(df) >= 20:
                self._save_cache(symbol, df)
                return symbol, df
        except Exception as e:
            log.debug("Provider failed %s: %s", symbol, e)

        # Fallback: yfinance
        try:
            import yfinance as yf
            df = yf.download(symbol, period=self.period, progress=False, auto_adjust=True)
            if df is not None and len(df) >= 20:
                df.columns = [c.lower() for c in df.columns]
                self._save_cache(symbol, df)
                return symbol, df
        except Exception as e:
            log.debug("yfinance failed %s: %s", symbol, e)

        return symbol, None

    # ------------------------------------------------------------------
    # Metadata fetch
    # ------------------------------------------------------------------

    def _fetch_metadata(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch sector, market cap, theme metadata."""
        meta: dict[str, dict] = {}
        try:
            import yfinance as yf
            for sym in symbols:
                try:
                    info = yf.Ticker(sym).fast_info
                    meta[sym] = {
                        "sector":     getattr(info, "sector", ""),
                        "market_cap": getattr(info, "market_cap", 0),
                        "theme":      "",  # populated by ThemeMomentumCalc
                        "eps_date":   "",
                    }
                except Exception:
                    meta[sym] = {"sector": "", "market_cap": 0, "theme": "", "eps_date": ""}
        except ImportError:
            log.warning("yfinance not available for metadata fetch")
        return meta

    # ------------------------------------------------------------------
    # Earnings detection
    # ------------------------------------------------------------------

    def _build_eps_set(
        self,
        symbols: list[str],
        lookahead_days: int = 3,
    ) -> set[str]:
        """Return symbols reporting earnings within lookahead_days."""
        eps_set: set[str] = set()
        try:
            import yfinance as yf
            from datetime import date
            today = date.today()
            for sym in symbols:
                try:
                    cal = yf.Ticker(sym).calendar
                    if cal is not None and not cal.empty:
                        if "Earnings Date" in cal.index:
                            ed = cal.loc["Earnings Date"].iloc[0]
                            if hasattr(ed, "date"):
                                ed = ed.date()
                            if 0 <= (ed - today).days <= lookahead_days:
                                eps_set.add(sym)
                                log.debug("%s earnings in %d days", sym, (ed - today).days)
                except Exception:
                    pass
        except ImportError:
            pass
        return eps_set

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(
        self,
        force_refresh: bool = False,
        include_metadata: bool = True,
        include_earnings: bool = True,
        premarket_data: Optional[dict[str, float]] = None,
    ) -> PipelineData:
        """
        Run the full data pipeline.

        Returns PipelineData with all fields populated.
        """
        symbols = self.build_universe()
        log.info("Pipeline: fetching %d symbols (universe=%s)", len(symbols), self.universe)

        t0 = time.time()
        ohlcv_map: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}

        # Concurrent fetch
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self._fetch_one, sym, force_refresh): sym
                for sym in symbols
            }
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
        log.info(
            "Fetched %d / %d symbols in %.1fs (%d errors)",
            len(ohlcv_map), len(symbols), elapsed, len(errors),
        )

        # SPY / IWM references
        spy_df = ohlcv_map.get("SPY")
        iwm_df = ohlcv_map.get("IWM")

        # Remove index ETFs from scan universe
        scan_symbols = [
            s for s in ohlcv_map
            if s not in {"SPY", "IWM", "QQQ", "SMH", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY"}
        ]
        scan_ohlcv = {s: ohlcv_map[s] for s in scan_symbols}

        # Metadata
        meta: dict[str, dict] = {}
        if include_metadata:
            log.info("Fetching metadata...")
            meta = self._fetch_metadata(scan_symbols[:100])  # limit to avoid rate limits

        # Earnings
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

    def run_scan_from_pipeline(
        self,
        equity: float = 100_000.0,
        force_refresh: bool = False,
    ) -> None:
        """Convenience: run pipeline then daily scan, print results."""
        data = self.run(force_refresh=force_refresh)
        if not data.is_valid():
            log.error("Pipeline returned invalid data — SPY missing")
            return

        from martin_quant.daily_scan import DailyScanner, DailyScanConfig
        scanner = DailyScanner(config=DailyScanConfig(equity=equity))
        result  = scanner.run(
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
