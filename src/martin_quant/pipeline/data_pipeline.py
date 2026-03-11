"""data_pipeline.py

Data Pipeline — yfinance Auto Data Fetcher
==========================================
Martin Quant 每日自動拉取美股數據。

功能:
  - 一鍵拉取 SPY/IWM + watchlist 的 OHLCV 日線
  - 本地磁碟缓存（防止重複拉取）
  - 自動值測 Pre-market 價格 (yfinance 1m)
  - 返回標準 DataBundle 給 DailyScanner

Usage:
    from martin_quant.pipeline.data_pipeline import DataPipeline
    pipeline = DataPipeline()
    data = pipeline.fetch_all()
    # data.spy_df, data.iwm_df, data.ohlcv_map, data.metadata
"""
from __future__ import annotations

import logging
import os
import pickle
import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class DataBundle:
    """DataPipeline 的輸出結構"""
    spy_df: pd.DataFrame
    iwm_df: pd.DataFrame
    ohlcv_map: dict[str, pd.DataFrame] = field(default_factory=dict)
    metadata: dict[str, dict] = field(default_factory=dict)
    premarket_prices: dict[str, float] = field(default_factory=dict)
    eps_catalyst_set: set[str] = field(default_factory=set)
    fetch_date: str = ""


class DataPipeline:
    """
    yfinance 自動數據流水線。

    Parameters
    ----------
    cache_dir : str
        本地缓存目錄，預設 .cache/martin_quant
    lookback_days : int
        拉取多少天歷史數據，預設 200（足夠算 RS + EMA200）
    use_cache : bool
        是否使用本地缓存
    """

    # 預設 watchlist — Martin 常用股票
    DEFAULT_SYMBOLS = [
        # Market ETFs
        "SPY", "QQQ", "IWM", "XLK", "XLV", "XLE", "XLF", "XLY",
        # AI / Semis
        "NVDA", "AMD", "SMCI", "AVGO", "TSM", "AMAT", "LRCX", "MRVL",
        "ARM", "ASML", "INTC", "MU", "KLAC",
        # Cloud / Software
        "MSFT", "AMZN", "GOOGL", "META", "CRM", "NOW", "SNOW", "DDOG",
        "NET", "CRWD", "ZS", "PANW",
        # Biotech
        "LLY", "NVO", "MRNA", "REGN", "VRTX", "ABBV",
        # Energy
        "XOM", "CVX", "EOG", "SLB",
        # Financials
        "JPM", "GS", "MS", "V", "MA",
        # Consumer
        "TSLA", "AAPL", "NFLX", "SPOT",
    ]

    def __init__(
        self,
        cache_dir: str = ".cache/martin_quant",
        lookback_days: int = 200,
        use_cache: bool = True,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.lookback_days = lookback_days
        self.use_cache = use_cache
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_all(
        self,
        symbols: Optional[list[str]] = None,
        force_refresh: bool = False,
    ) -> DataBundle:
        """
        一鍵拉取所有所需數據。

        Parameters
        ----------
        symbols : list[str] 自定義 watchlist，若為 None 使用 DEFAULT_SYMBOLS
        force_refresh : bool  強制重拉，忽略缓存
        """
        symbols = symbols or self._load_watchlist_symbols()
        today_str = str(datetime.date.today())

        # Check cache
        cache_key = self.cache_dir / f"bundle_{today_str}.pkl"
        if self.use_cache and not force_refresh and cache_key.exists():
            try:
                with open(cache_key, "rb") as f:
                    bundle = pickle.load(f)
                log.info("DataPipeline: loaded from cache (%s)", today_str)
                return bundle
            except Exception:
                log.warning("Cache corrupt, re-fetching...")

        try:
            import yfinance as yf
        except ImportError:
            raise ImportError("yfinance not installed. Run: pip install yfinance")

        end_date   = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=self.lookback_days + 30)

        all_symbols = list(set(["SPY", "IWM"] + symbols))
        log.info("Fetching %d symbols from yfinance...", len(all_symbols))

        # Batch download
        raw = yf.download(
            all_symbols,
            start=str(start_date),
            end=str(end_date + datetime.timedelta(days=1)),
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        ohlcv_map: dict[str, pd.DataFrame] = {}

        if isinstance(raw.columns, pd.MultiIndex):
            # Multi-ticker download
            for sym in all_symbols:
                try:
                    df = raw.xs(sym, axis=1, level=1).dropna(how="all")
                    df.columns = [c.lower() for c in df.columns]
                    if len(df) >= 20:
                        ohlcv_map[sym] = df.tail(self.lookback_days)
                except Exception:
                    pass
        else:
            # Single ticker (rare case)
            raw.columns = [c.lower() for c in raw.columns]
            if len(all_symbols) == 1:
                ohlcv_map[all_symbols[0]] = raw.tail(self.lookback_days)

        spy_df = ohlcv_map.pop("SPY", pd.DataFrame())
        iwm_df = ohlcv_map.pop("IWM", pd.DataFrame())

        # Build metadata
        metadata = self._build_metadata(symbols)

        # Pre-market prices (latest 1-min bar if available)
        premarket_prices = self._fetch_premarket(symbols[:20], yf)  # limit to 20

        bundle = DataBundle(
            spy_df=spy_df,
            iwm_df=iwm_df,
            ohlcv_map=ohlcv_map,
            metadata=metadata,
            premarket_prices=premarket_prices,
            eps_catalyst_set=set(),
            fetch_date=today_str,
        )

        # Save cache
        if self.use_cache:
            try:
                with open(cache_key, "wb") as f:
                    pickle.dump(bundle, f)
                log.info("DataPipeline: saved cache (%s)", today_str)
            except Exception as e:
                log.warning("Could not save cache: %s", e)

        log.info(
            "DataPipeline: fetched %d symbols | SPY=%d bars | IWM=%d bars",
            len(ohlcv_map), len(spy_df), len(iwm_df),
        )
        return bundle

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_watchlist_symbols(self) -> list[str]:
        """Load from watchlist.txt if exists, else use defaults"""
        wl_file = Path("watchlist.txt")
        if wl_file.exists():
            with open(wl_file) as f:
                syms = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
            if syms:
                log.info("Loaded %d symbols from watchlist.txt", len(syms))
                return syms
        return self.DEFAULT_SYMBOLS

    @staticmethod
    def _build_metadata(symbols: list[str]) -> dict[str, dict]:
        """Simple sector/theme metadata map"""
        sector_map = {
            "NVDA": {"sector": "Technology", "theme": "AI_semis"},
            "AMD":  {"sector": "Technology", "theme": "AI_semis"},
            "SMCI": {"sector": "Technology", "theme": "AI_semis"},
            "AVGO": {"sector": "Technology", "theme": "AI_semis"},
            "ARM":  {"sector": "Technology", "theme": "AI_semis"},
            "MSFT": {"sector": "Technology", "theme": "cloud"},
            "AMZN": {"sector": "Technology", "theme": "cloud"},
            "GOOGL":{"sector": "Technology", "theme": "cloud"},
            "META": {"sector": "Technology", "theme": "cloud"},
            "CRM":  {"sector": "Technology", "theme": "cloud"},
            "NOW":  {"sector": "Technology", "theme": "cloud"},
            "SNOW": {"sector": "Technology", "theme": "cloud"},
            "CRWD": {"sector": "Technology", "theme": "cybersecurity"},
            "PANW": {"sector": "Technology", "theme": "cybersecurity"},
            "ZS":   {"sector": "Technology", "theme": "cybersecurity"},
            "LLY":  {"sector": "Healthcare",  "theme": "biotech"},
            "NVO":  {"sector": "Healthcare",  "theme": "biotech"},
            "MRNA": {"sector": "Healthcare",  "theme": "biotech"},
            "XOM":  {"sector": "Energy",      "theme": "energy"},
            "CVX":  {"sector": "Energy",      "theme": "energy"},
            "JPM":  {"sector": "Financials",  "theme": "banks"},
            "GS":   {"sector": "Financials",  "theme": "banks"},
            "TSLA": {"sector": "Consumer",    "theme": "EV"},
            "AAPL": {"sector": "Technology",  "theme": "consumer_tech"},
        }
        return {
            sym: sector_map.get(sym, {"sector": "Unknown", "theme": "misc"})
            for sym in symbols
        }

    @staticmethod
    def _fetch_premarket(
        symbols: list[str],
        yf_module,
    ) -> dict[str, float]:
        """Fetch latest pre-market prices via yfinance 1m"""
        prices: dict[str, float] = {}
        try:
            raw = yf_module.download(
                symbols,
                period="1d",
                interval="1m",
                auto_adjust=True,
                progress=False,
                threads=False,
                prepost=True,
            )
            if raw.empty:
                return prices
            if isinstance(raw.columns, pd.MultiIndex):
                for sym in symbols:
                    try:
                        close = raw["Close"][sym].dropna()
                        if not close.empty:
                            prices[sym] = float(close.iloc[-1])
                    except Exception:
                        pass
            else:
                if "Close" in raw.columns and symbols:
                    prices[symbols[0]] = float(raw["Close"].dropna().iloc[-1])
        except Exception as e:
            log.debug("Pre-market fetch skipped: %s", e)
        return prices
