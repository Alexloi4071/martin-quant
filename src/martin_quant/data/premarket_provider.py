"""premarket_provider.py

開市前報價提供者 — Gap Scanner 需要的開市前報價 + 成交量

資料來源 (按優先級):
  1. yfinance  — 免費，有 pre-market last price
  2. Finviz    — 免費，可得 pre-market price + %change
  3. Polygon   — 會員制，最準確的 pre-market OHLCV

輸出:
  - premarket_prices  : {symbol: last_premarket_price}
  - premarket_volumes : {symbol: premarket_cumulative_volume}
  - gap_pct           : {symbol: gap_pct_from_prev_close}
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)


class PremarketProvider:
    """
    Fetches pre-market price and volume data for gap scanning.

    Parameters
    ----------
    polygon_api_key : str, optional  Polygon.io API key for premium data
    rate_limit_sleep : float         seconds between requests (default 0.3)
    """

    def __init__(
        self,
        polygon_api_key: Optional[str] = None,
        rate_limit_sleep: float = 0.3,
    ) -> None:
        import os
        self.polygon_key    = polygon_api_key or os.getenv("POLYGON_API_KEY", "")
        self.rate_limit_sleep = rate_limit_sleep

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_premarket_prices(
        self,
        symbols: list[str],
        prev_closes: Optional[dict[str, float]] = None,
    ) -> dict[str, dict]:
        """
        Fetch pre-market data for a list of symbols.

        Parameters
        ----------
        symbols      : list[str]
        prev_closes  : dict {symbol: prev_close_price}  optional
                       If provided, computes gap_pct.

        Returns
        -------
        dict {symbol: {
            "premarket_price":  float,
            "premarket_volume": float,  # estimated (may be partial)
            "gap_pct":          float,  # 0 if prev_close not provided
            "source":           str,
        }}
        """
        results: dict[str, dict] = {}
        prev = prev_closes or {}

        for sym in symbols:
            try:
                data = self._fetch_yfinance_premarket(sym)
                if data and data.get("premarket_price"):
                    pm_px = data["premarket_price"]
                    pc    = prev.get(sym.upper(), 0)
                    gap   = (pm_px - pc) / pc * 100 if pc > 0 else 0.0
                    results[sym.upper()] = {
                        "premarket_price":  pm_px,
                        "premarket_volume": data.get("premarket_volume", 0),
                        "gap_pct":          round(gap, 2),
                        "source":           "yfinance",
                    }
                    time.sleep(self.rate_limit_sleep)
            except Exception as e:
                log.warning("Premarket fetch failed for %s: %s", sym, e)

        # Polygon upgrade (if key available)
        if self.polygon_key:
            polygon_results = self._fetch_polygon_premarket(symbols)
            for sym, pdata in polygon_results.items():
                if sym in results:
                    results[sym].update(pdata)
                    results[sym]["source"] = "polygon"

        return results

    def get_premarket_prices_dict(self, symbols: list[str], **kwargs) -> dict[str, float]:
        """Simplified: returns {symbol: premarket_price}."""
        full = self.get_premarket_prices(symbols, **kwargs)
        return {
            sym: d["premarket_price"]
            for sym, d in full.items()
            if d.get("premarket_price")
        }

    def get_premarket_volumes_dict(self, symbols: list[str], **kwargs) -> dict[str, float]:
        """Simplified: returns {symbol: premarket_volume}."""
        full = self.get_premarket_prices(symbols, **kwargs)
        return {
            sym: d.get("premarket_volume", 0)
            for sym, d in full.items()
        }

    def get_gap_table(
        self,
        symbols: list[str],
        prev_closes: dict[str, float],
        min_gap_pct: float = 3.0,
    ) -> pd.DataFrame:
        """
        Returns DataFrame of gap-up candidates.
        Columns: symbol, premarket_price, prev_close, gap_pct, premarket_volume, source
        Filtered to gap_pct >= min_gap_pct, sorted desc.
        """
        full = self.get_premarket_prices(symbols, prev_closes)
        rows = []
        for sym, d in full.items():
            if d.get("gap_pct", 0) >= min_gap_pct:
                rows.append({
                    "symbol":           sym,
                    "premarket_price":  d.get("premarket_price"),
                    "prev_close":       prev_closes.get(sym, None),
                    "gap_pct":          d.get("gap_pct"),
                    "premarket_volume": d.get("premarket_volume", 0),
                    "source":           d.get("source"),
                })
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values("gap_pct", ascending=False).reset_index(drop=True)
        return df

    # ------------------------------------------------------------------
    # Source implementations
    # ------------------------------------------------------------------

    def _fetch_yfinance_premarket(self, symbol: str) -> dict:
        """
        yfinance provides pre_market_price and pre_market_change
        via Ticker.info (during pre-market hours).
        """
        try:
            import yfinance as yf
            tkr  = yf.Ticker(symbol)
            info = tkr.fast_info

            pm_price  = getattr(info, "pre_market_price", None)
            if pm_price is None:
                # Fallback: last 2-day 1m bars, find pre-market candles
                raw = tkr.history(period="2d", interval="1m", prepost=True)
                if raw.empty:
                    return {}
                now       = pd.Timestamp.now(tz="America/New_York")
                pm_mask   = raw.index.tz_convert("America/New_York").hour < 9
                pm_bars   = raw[pm_mask]
                if pm_bars.empty:
                    return {}
                pm_price  = float(pm_bars["Close"].iloc[-1])
                pm_volume = float(pm_bars["Volume"].sum())
            else:
                pm_price  = float(pm_price)
                pm_volume = 0.0

            return {
                "premarket_price":  pm_price,
                "premarket_volume": pm_volume,
            }
        except Exception as e:
            log.debug("yfinance premarket failed for %s: %s", symbol, e)
            return {}

    def _fetch_polygon_premarket(
        self, symbols: list[str]
    ) -> dict[str, dict]:
        """
        Polygon.io /v2/aggs/grouped/locale/us/market/stocks endpoint
        provides pre-market aggregates.
        """
        try:
            import requests
            from datetime import date
            today = date.today().isoformat()
            url   = (
                f"https://api.polygon.io/v2/aggs/grouped/locale/us"
                f"/market/stocks/{today}"
                f"?adjusted=true&include_otc=false"
                f"&apiKey={self.polygon_key}"
            )
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return {}
            data = resp.json().get("results", [])
            sym_set = {s.upper() for s in symbols}
            result  = {}
            for bar in data:
                if bar.get("T") in sym_set:
                    result[bar["T"]] = {
                        "premarket_price":  bar.get("o"),  # open of the day
                        "premarket_volume": bar.get("v", 0),
                    }
            return result
        except Exception as e:
            log.warning("Polygon premarket failed: %s", e)
            return {}
