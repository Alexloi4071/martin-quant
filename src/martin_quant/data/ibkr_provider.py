"""ibkr_provider.py

Interactive Brokers 數據提供者 — 美股日線 + 期權数據
yfinance 為自動備用

支援:
  - 日線 OHLCV (US equities)
  - 期權鏈 (calls + puts, greeks, IV)
  - 重要指數: SPY, QQQ, IWM 日線

需要: pip install ib_insync yfinance
  需要 TWS 或 IB Gateway 在 127.0.0.1:7497 執行
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)


class IBKRProvider:
    """
    IBKR data provider with yfinance fallback.

    Parameters
    ----------
    host       : str  TWS host, default "127.0.0.1"
    port       : int  TWS port, default 7497 (paper: 7496)
    client_id  : int  default 10
    cache_dir  : str  default "./cache/ibkr"
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 10,
        cache_dir: str = "./cache/ibkr",
    ) -> None:
        self.host      = host
        self.port      = port
        self.client_id = client_id
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ib       = None
        self._connected = False

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, timeout: int = 10) -> bool:
        """Try to connect to TWS/IB Gateway. Returns True if successful."""
        try:
            from ib_insync import IB
            self._ib = IB()
            self._ib.connect(
                self.host, self.port, clientId=self.client_id,
                timeout=timeout, readonly=True
            )
            self._connected = True
            log.info("IBKR connected: %s:%d", self.host, self.port)
            return True
        except Exception as e:
            log.warning("IBKR connection failed: %s. Falling back to yfinance.", e)
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._ib and self._connected:
            self._ib.disconnect()
            self._connected = False

    # ------------------------------------------------------------------
    # Daily OHLCV
    # ------------------------------------------------------------------

    def get_daily(
        self,
        symbol: str,
        days: int = 365,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch daily OHLCV for a US equity.
        Uses IBKR if connected, yfinance otherwise.

        Returns
        -------
        pd.DataFrame  index=date, columns=[open, high, low, close, volume]
        """
        cache_file = self.cache_dir / f"{symbol}_1d.parquet"

        if use_cache and cache_file.exists():
            try:
                cached = pd.read_parquet(cache_file)
                if self._daily_cache_fresh(cached):
                    return cached.tail(days).copy()
            except Exception:
                pass

        if self._connected and self._ib is not None:
            df = self._ibkr_get_daily(symbol, days)
        else:
            df = self._yfinance_get_daily(symbol, days)

        if not df.empty:
            df.to_parquet(cache_file)
        return df

    def get_multi_daily(
        self,
        symbols: list[str],
        days: int = 365,
    ) -> dict[str, pd.DataFrame]:
        """Batch daily OHLCV: {symbol: daily_df}"""
        result: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                df = self.get_daily(sym, days)
                if not df.empty:
                    result[sym.upper()] = df
            except Exception as e:
                log.warning("Failed to fetch %s: %s", sym, e)
        return result

    # ------------------------------------------------------------------
    # Options chain
    # ------------------------------------------------------------------

    def get_options_chain(
        self,
        symbol: str,
        expiry: Optional[str] = None,  # "YYYYMMDD" or None for nearest
    ) -> pd.DataFrame:
        """
        Fetch options chain for a symbol.
        Returns DataFrame with: strike, right(C/P), expiry, bid, ask,
        IV, delta, gamma, theta, vega, OI, volume.

        Note: IBKR required for live Greeks/IV. yfinance for basic chain.
        """
        if self._connected and self._ib is not None:
            return self._ibkr_options_chain(symbol, expiry)
        return self._yfinance_options_chain(symbol, expiry)

    # ------------------------------------------------------------------
    # IBKR implementations
    # ------------------------------------------------------------------

    def _ibkr_get_daily(self, symbol: str, days: int) -> pd.DataFrame:
        try:
            from ib_insync import Stock
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            duration = f"{min(days, 365) + 10} D"
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            if not bars:
                return pd.DataFrame()
            df = pd.DataFrame([
                {"date": b.date, "open": b.open, "high": b.high,
                 "low": b.low, "close": b.close, "volume": b.volume}
                for b in bars
            ])
            df["date"] = pd.to_datetime(df["date"])
            return df.set_index("date").sort_index().tail(days)
        except Exception as e:
            log.error("IBKR daily fetch failed for %s: %s", symbol, e)
            return self._yfinance_get_daily(symbol, days)

    def _ibkr_options_chain(self, symbol: str, expiry: Optional[str]) -> pd.DataFrame:
        try:
            from ib_insync import Stock, Option
            contract = Stock(symbol, "SMART", "USD")
            chains   = self._ib.reqSecDefOptParams(
                contract.symbol, "", contract.secType, contract.conId
            )
            if not chains:
                return pd.DataFrame()
            chain = next(c for c in chains if c.exchange == "SMART")
            expiries = sorted(chain.expirations)
            target_exp = expiry or (expiries[0] if expiries else None)
            if not target_exp:
                return pd.DataFrame()

            rows = []
            for right in ("C", "P"):
                for strike in sorted(chain.strikes):
                    opt = Option(symbol, target_exp, strike, right, "SMART")
                    self._ib.qualifyContracts(opt)
                    ticker = self._ib.reqMktData(opt, "", False, False)
                    self._ib.sleep(0.05)
                    rows.append({
                        "symbol":  symbol,
                        "expiry":  target_exp,
                        "strike":  strike,
                        "right":   right,
                        "bid":     ticker.bid,
                        "ask":     ticker.ask,
                        "IV":      ticker.impliedVolatility,
                        "delta":   ticker.modelGreeks.delta if ticker.modelGreeks else None,
                        "gamma":   ticker.modelGreeks.gamma if ticker.modelGreeks else None,
                        "theta":   ticker.modelGreeks.theta if ticker.modelGreeks else None,
                        "vega":    ticker.modelGreeks.vega  if ticker.modelGreeks else None,
                        "OI":      ticker.callOpenInterest if right == "C" else ticker.putOpenInterest,
                        "volume":  ticker.volume,
                    })
            return pd.DataFrame(rows)
        except Exception as e:
            log.error("IBKR options failed for %s: %s", symbol, e)
            return self._yfinance_options_chain(symbol, expiry)

    # ------------------------------------------------------------------
    # yfinance fallback
    # ------------------------------------------------------------------

    def _yfinance_get_daily(self, symbol: str, days: int) -> pd.DataFrame:
        try:
            import yfinance as yf
            period = f"{min(days + 30, 730)}d"
            tkr    = yf.Ticker(symbol)
            raw    = tkr.history(period=period, auto_adjust=True)
            if raw.empty:
                return pd.DataFrame()
            df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.columns = ["open", "high", "low", "close", "volume"]
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.index.name = "date"
            return df.tail(days)
        except Exception as e:
            log.error("yfinance failed for %s: %s", symbol, e)
            return pd.DataFrame()

    def _yfinance_options_chain(
        self, symbol: str, expiry: Optional[str]
    ) -> pd.DataFrame:
        try:
            import yfinance as yf
            tkr     = yf.Ticker(symbol)
            expiries = tkr.options
            if not expiries:
                return pd.DataFrame()
            target = expiry or expiries[0]
            chain  = tkr.option_chain(target)
            calls  = chain.calls.copy()
            puts   = chain.puts.copy()
            calls["right"] = "C"
            puts["right"]  = "P"
            df = pd.concat([calls, puts], ignore_index=True)
            # Standardise columns
            col_map = {
                "strike":           "strike",
                "lastPrice":        "last",
                "bid":              "bid",
                "ask":              "ask",
                "impliedVolatility":"IV",
                "openInterest":     "OI",
                "volume":           "volume",
            }
            df = df.rename(columns=col_map)
            df["symbol"] = symbol
            df["expiry"] = target
            return df
        except Exception as e:
            log.error("yfinance options failed for %s: %s", symbol, e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _daily_cache_fresh(self, df: pd.DataFrame) -> bool:
        if df.empty:
            return False
        last_date = pd.to_datetime(df.index[-1])
        today     = pd.Timestamp.today().normalize()
        # Fresh if last bar is within 1 trading day
        return (today - last_date).days <= 3

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
