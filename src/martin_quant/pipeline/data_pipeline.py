"""data_pipeline.py

Automated Data Pipeline
========================
自動從 provider 拉取 watchlist 所有股票的日線 + 15分鐘資料，
回傳可直接給 DailyScannerV2 使用的 dict。

功能:
  - 並行下載（ThreadPoolExecutor）
  - 自動跳過下載失敗的股票
  - 可選: 快取到 data/cache/ 目錄（避免重複拉取）
  - 支援多個 data provider（yfinance / IBKR / custom）

Usage:
    from martin_quant.pipeline.data_pipeline import DataPipeline

    pipeline = DataPipeline()
    daily_data, intraday_data = pipeline.fetch(
        symbols=["NVDA", "AMD", "MSFT"],
        fetch_intraday=True,
        intraday_interval="15m",
    )
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
import logging
import time

import pandas as pd

log = logging.getLogger(__name__)

DEFAULT_SYMBOLS = [
    "SPY", "QQQ", "IWM",
    "NVDA", "AMD", "MSFT", "AAPL", "AMZN", "META", "GOOGL",
    "TSM", "AVGO", "QCOM", "AMAT",
    "JPM", "GS", "V",
    "XLK", "SOXX", "XLY", "XLF",
]

# Default sectors for common symbols
DEFAULT_SECTOR_MAP: dict[str, str] = {
    "NVDA": "semiconductors", "AMD": "semiconductors",
    "TSM": "semiconductors",  "AVGO": "semiconductors",
    "QCOM": "semiconductors", "AMAT": "semiconductors",
    "MSFT": "technology",     "AAPL": "technology",
    "GOOGL": "technology",    "META": "technology",
    "AMZN": "consumer_discretionary",
    "JPM": "financials",      "GS": "financials",   "V": "financials",
    "SPY": "index",           "QQQ": "index",       "IWM": "index",
    "XLK": "technology",      "SOXX": "semiconductors",
    "XLY": "consumer_discretionary", "XLF": "financials",
}


class DataPipeline:
    """
    並行下載多股票資料的 Pipeline。

    Parameters
    ----------
    provider_name : str
        'yfinance' | 'ibkr' | 'auto' (auto-detect)
    cache_dir : str, optional
        快取目錄路徑；None = 不快取
    max_workers : int
        並行下載執行緒數
    retry : int
        下載失敗重試次數
    """

    def __init__(
        self,
        provider_name: str = "auto",
        cache_dir: Optional[str] = None,
        max_workers: int = 8,
        retry: int = 2,
    ) -> None:
        self.provider_name = provider_name
        self.cache_dir     = Path(cache_dir) if cache_dir else None
        self.max_workers   = max_workers
        self.retry         = retry
        self._provider     = None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def fetch(
        self,
        symbols: Optional[list[str]] = None,
        fetch_intraday: bool = False,
        intraday_interval: str = "15m",
        daily_period: str = "1y",
    ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
        """
        並行下載所有股票的日線（+ 可選 15m）資料。

        Returns
        -------
        (daily_data, intraday_data)
          daily_data    : {symbol: daily_ohlcv_df}
          intraday_data : {symbol: intraday_ohlcv_df}  (若 fetch_intraday=False 則為空)
        """
        symbols   = symbols or DEFAULT_SYMBOLS
        provider  = self._get_provider()
        daily_out: dict[str, pd.DataFrame]    = {}
        intra_out: dict[str, pd.DataFrame]    = {}

        log.info("DataPipeline: fetching %d symbols (intraday=%s)", len(symbols), fetch_intraday)

        def _fetch_one(sym: str) -> tuple[str, Optional[pd.DataFrame], Optional[pd.DataFrame]]:
            d_df = i_df = None
            for attempt in range(self.retry + 1):
                try:
                    d_df = self._get_daily(provider, sym, period=daily_period)
                    if fetch_intraday:
                        i_df = self._get_intraday(provider, sym, interval=intraday_interval)
                    break
                except Exception as e:
                    if attempt < self.retry:
                        time.sleep(0.5 * (attempt + 1))
                    else:
                        log.warning("%s: download failed after %d retries: %s", sym, self.retry, e)
            return sym, d_df, i_df

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {ex.submit(_fetch_one, s): s for s in symbols}
            for fut in as_completed(futures):
                sym, d_df, i_df = fut.result()
                if d_df is not None and len(d_df) >= 20:
                    daily_out[sym] = d_df
                    if i_df is not None:
                        intra_out[sym] = i_df

        log.info("DataPipeline: success daily=%d  intraday=%d",
                 len(daily_out), len(intra_out))
        return daily_out, intra_out

    def get_sectors(
        self,
        symbols: Optional[list[str]] = None,
    ) -> dict[str, str]:
        """
        回傳 {symbol: sector} mapping。
        先查 DEFAULT_SECTOR_MAP，剩餘嘗試從 provider 取得。
        """
        symbols = symbols or list(DEFAULT_SECTOR_MAP.keys())
        result  = {s: DEFAULT_SECTOR_MAP.get(s, "") for s in symbols}
        provider = self._get_provider()
        for sym in symbols:
            if not result[sym] and hasattr(provider, "get_sector"):
                try:
                    result[sym] = provider.get_sector(sym) or ""
                except Exception:
                    pass
        return result

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _get_provider(self):
        if self._provider is not None:
            return self._provider
        try:
            from martin_quant.data import get_provider
            self._provider = get_provider(self.provider_name)
        except Exception:
            self._provider = self._yfinance_fallback()
        return self._provider

    def _get_daily(
        self,
        provider,
        symbol: str,
        period: str = "1y",
    ) -> Optional[pd.DataFrame]:
        # Try provider method first
        if hasattr(provider, "get_daily"):
            return provider.get_daily(symbol, period=period)
        # yfinance fallback
        return self._yf_daily(symbol, period)

    def _get_intraday(
        self,
        provider,
        symbol: str,
        interval: str = "15m",
    ) -> Optional[pd.DataFrame]:
        if hasattr(provider, "get_intraday"):
            return provider.get_intraday(symbol, interval=interval)
        return self._yf_intraday(symbol, interval)

    @staticmethod
    def _yfinance_fallback():
        class _YFProvider:
            def get_daily(self, sym, period="1y"):
                import yfinance as yf
                df = yf.download(sym, period=period, progress=False, auto_adjust=True)
                df.columns = [c.lower() for c in df.columns]
                return df

            def get_intraday(self, sym, interval="15m"):
                import yfinance as yf
                df = yf.download(sym, period="1d", interval=interval,
                                 progress=False, auto_adjust=True)
                df.columns = [c.lower() for c in df.columns]
                return df
        return _YFProvider()

    @staticmethod
    def _yf_daily(symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf
            df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
            df.columns = [c.lower() for c in df.columns]
            return df if not df.empty else None
        except Exception as e:
            log.warning("%s yf daily failed: %s", symbol, e)
            return None

    @staticmethod
    def _yf_intraday(symbol: str, interval: str = "15m") -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf
            df = yf.download(symbol, period="1d", interval=interval,
                             progress=False, auto_adjust=True)
            df.columns = [c.lower() for c in df.columns]
            return df if not df.empty else None
        except Exception as e:
            log.warning("%s yf intraday failed: %s", symbol, e)
            return None
