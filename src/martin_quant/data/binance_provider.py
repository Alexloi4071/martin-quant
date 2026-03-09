"""binance_provider.py

Binance 密碼貨徱5源 — 主要用於加密貨日線/K線資料

支援:
  - 多時間架: 1d / 4h / 1h / 15m / 5m
  - 自動稏存 (./cache/binance/)
  - Rate limit 處理
  - CCXT 標準化 API

需要: pip install ccxt
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

# Timeframe label → Binance interval string + duration in seconds
_TF_MAP = {
    "1d":  ("1d",   86400),
    "4h":  ("4h",   14400),
    "1h":  ("1h",   3600),
    "15m": ("15m",  900),
    "5m":  ("5m",   300),
    "1m":  ("1m",   60),
}


class BinanceProvider:
    """
    Wraps CCXT Binance for OHLCV retrieval.
    Falls back to cached parquet if network unavailable.

    Parameters
    ----------
    api_key    : str, optional  (read-only key for market data is fine)
    api_secret : str, optional
    cache_dir  : str, optional  default = "./cache/binance"
    use_futures: bool  default False (spot)
    """

    MAX_CANDLES_PER_REQUEST = 1000

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        cache_dir: str = "./cache/binance",
        use_futures: bool = False,
    ) -> None:
        self.cache_dir   = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.use_futures = use_futures
        self._exchange   = None

        # Lazy init CCXT
        try:
            import ccxt
            exchange_cls = ccxt.binanceusdm if use_futures else ccxt.binance
            self._exchange = exchange_cls({
                "apiKey":    api_key    or os.getenv("BINANCE_API_KEY", ""),
                "secret":    api_secret or os.getenv("BINANCE_API_SECRET", ""),
                "enableRateLimit": True,
                "options": {"defaultType": "future" if use_futures else "spot"},
            })
            log.info("Binance provider initialised (futures=%s)", use_futures)
        except ImportError:
            log.warning("ccxt not installed. Install with: pip install ccxt")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        limit: int = 500,
        since: Optional[datetime] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV for a symbol.

        Parameters
        ----------
        symbol    : str  e.g. "BTCUSDT"
        timeframe : str  "1d" | "4h" | "1h" | "15m" | "5m"
        limit     : int  number of candles
        since     : datetime, optional  start time
        use_cache : bool  try local cache first

        Returns
        -------
        pd.DataFrame with columns: timestamp(index), open, high, low, close, volume
        """
        if timeframe not in _TF_MAP:
            raise ValueError(f"timeframe must be one of {list(_TF_MAP)}")

        cache_file = self.cache_dir / f"{symbol}_{timeframe}.parquet"

        # Try cache
        cached = None
        if use_cache and cache_file.exists():
            try:
                cached = pd.read_parquet(cache_file)
                # If cache is recent enough, return it
                if self._cache_is_fresh(cached, timeframe):
                    log.debug("Cache hit: %s %s", symbol, timeframe)
                    return cached.tail(limit).copy()
            except Exception as e:
                log.warning("Cache read failed: %s", e)

        # Fetch from API
        if self._exchange is None:
            if cached is not None:
                log.warning("No API connection, returning stale cache.")
                return cached.tail(limit).copy()
            raise RuntimeError("ccxt not installed and no cache available.")

        df = self._fetch_all(symbol, timeframe, limit, since)

        # Merge with cache
        if cached is not None:
            df = pd.concat([cached, df]).drop_duplicates().sort_index()

        if not df.empty:
            df.to_parquet(cache_file)

        return df.tail(limit).copy()

    def get_daily(self, symbol: str, limit: int = 500, **kwargs) -> pd.DataFrame:
        return self.get_ohlcv(symbol, "1d", limit, **kwargs)

    def get_5m(self, symbol: str, limit: int = 500, **kwargs) -> pd.DataFrame:
        return self.get_ohlcv(symbol, "5m", limit, **kwargs)

    def get_multi_tf(
        self,
        symbols: list[str],
        timeframes: list[str] = ("1d", "4h", "1h"),
        limit: int = 500,
    ) -> dict[str, dict[str, pd.DataFrame]]:
        """
        Returns {symbol: {timeframe: df}}.
        """
        result: dict[str, dict[str, pd.DataFrame]] = {}
        for sym in symbols:
            result[sym] = {}
            for tf in timeframes:
                try:
                    result[sym][tf] = self.get_ohlcv(sym, tf, limit)
                except Exception as e:
                    log.warning("Failed %s %s: %s", sym, tf, e)
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_all(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        since: Optional[datetime],
    ) -> pd.DataFrame:
        """Fetch candles, paginating if needed."""
        _, tf_seconds = _TF_MAP[timeframe]
        binance_tf    = _TF_MAP[timeframe][0]

        if since is None:
            since = datetime.now(timezone.utc) - timedelta(
                seconds=tf_seconds * limit
            )

        since_ms = int(since.timestamp() * 1000)
        all_bars: list[list] = []
        remaining = limit

        while remaining > 0:
            fetch_n = min(remaining, self.MAX_CANDLES_PER_REQUEST)
            try:
                bars = self._exchange.fetch_ohlcv(
                    symbol, binance_tf, since=since_ms, limit=fetch_n
                )
            except Exception as e:
                log.error("Binance fetch error %s %s: %s", symbol, timeframe, e)
                break

            if not bars:
                break

            all_bars.extend(bars)
            since_ms   = bars[-1][0] + 1  # next ms after last bar
            remaining -= len(bars)

            if len(bars) < fetch_n:
                break  # reached the end

            time.sleep(0.05)  # respect rate limit

        if not all_bars:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            all_bars, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp").sort_index()
        df = df.astype(float)
        return df

    def _cache_is_fresh(self, df: pd.DataFrame, timeframe: str) -> bool:
        """True if the last bar is recent enough to be useful."""
        if df.empty:
            return False
        _, tf_seconds = _TF_MAP[timeframe]
        last_ts  = df.index[-1]
        now      = pd.Timestamp.now(tz="UTC")
        age_secs = (now - last_ts).total_seconds()
        # Fresh if last bar is within 2 periods
        return age_secs < tf_seconds * 2
