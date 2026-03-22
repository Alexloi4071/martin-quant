from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


@dataclass
class FinnhubProviderConfig:
    api_key: str = ""
    api_key_env: str = "FINNHUB_API_KEY"
    base_url: str = "https://finnhub.io/api/v1"
    timeout: int = 20
    pause_seconds: float = 0.35

    def resolved_api_key(self) -> str:
        key = self.api_key or os.getenv(self.api_key_env, "")
        if not key:
            raise ValueError(
                f"Finnhub API key not found. Set {self.api_key_env} or pass api_key explicitly."
            )
        return key


class FinnhubProvider:
    """
    Finnhub provider for:
    - company profile2
    - quote
    - earnings calendar
    - OHLCV candles (1d / 1h / 15m)
    """

    def __init__(self, config: FinnhubProviderConfig | None = None) -> None:
        self.config = config or FinnhubProviderConfig()
        self.api_key = self.config.resolved_api_key()

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        params["token"] = self.api_key
        url = f"{self.config.base_url}{path}?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urlopen(req, timeout=self.config.timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def get_quote(self, symbol: str) -> dict:
        time.sleep(self.config.pause_seconds)
        return self._get_json("/quote", {"symbol": symbol.upper()})

    def get_company_profile2(self, symbol: str) -> dict:
        time.sleep(self.config.pause_seconds)
        return self._get_json("/stock/profile2", {"symbol": symbol.upper()})

    def get_earnings_calendar(self, from_date: date, to_date: date) -> list[dict]:
        time.sleep(self.config.pause_seconds)
        payload = self._get_json(
            "/calendar/earnings",
            {"from": from_date.isoformat(), "to": to_date.isoformat()},
        )
        if isinstance(payload, dict):
            for key in ("earningsCalendar", "earnings_calendar", "data"):
                if key in payload and isinstance(payload[key], list):
                    return payload[key]
        return []

    def get_stock_candles(
        self,
        symbol: str,
        resolution: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pd.DataFrame:
        start_ts = int(start_dt.replace(tzinfo=timezone.utc).timestamp())
        end_ts = int(end_dt.replace(tzinfo=timezone.utc).timestamp())

        time.sleep(self.config.pause_seconds)
        payload = self._get_json(
            "/stock/candle",
            {"symbol": symbol.upper(), "resolution": resolution, "from": start_ts, "to": end_ts},
        )

        if not isinstance(payload, dict) or payload.get("s") not in ("ok", None):
            return pd.DataFrame()
        if "t" not in payload or not payload.get("t"):
            return pd.DataFrame()

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(payload["t"], unit="s", utc=True),
            "open":   payload.get("o", []),
            "high":   payload.get("h", []),
            "low":    payload.get("l", []),
            "close":  payload.get("c", []),
            "volume": payload.get("v", []),
        })
        if df.empty:
            return df
        return df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    def build_metadata_frame(self, symbols: list[str]) -> pd.DataFrame:
        rows: list[dict] = []
        for symbol in symbols:
            profile = self.get_company_profile2(symbol)
            quote   = self.get_quote(symbol)
            rows.append({
                "symbol":        symbol.upper(),
                "company_name":  profile.get("name"),
                "exchange":      profile.get("exchange"),
                "currency":      profile.get("currency"),
                "country":       profile.get("country"),
                "ipo":           profile.get("ipo"),
                "market_cap":    profile.get("marketCapitalization"),
                "industry":      profile.get("finnhubIndustry"),
                "sector":        profile.get("finnhubIndustry"),
                "theme":         "",
                "price":         quote.get("c"),
                "prev_close":    quote.get("pc"),
                "day_change":    quote.get("d"),
                "day_change_pct":quote.get("dp"),
            })
        out = pd.DataFrame(rows)
        if out.empty:
            return out
        out["symbol"] = out["symbol"].astype(str).str.upper()
        return out.drop_duplicates(subset=["symbol"]).reset_index(drop=True)

    def build_earnings_frame(
        self,
        symbols: list[str],
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> pd.DataFrame:
        from_date = from_date or date.today()
        to_date   = to_date   or (from_date + timedelta(days=45))

        rows = self.get_earnings_calendar(from_date, to_date)
        if not rows:
            return pd.DataFrame(columns=["symbol", "earnings_date", "eps_estimate", "revenue_estimate"])

        df = pd.DataFrame(rows)
        if "symbol" not in df.columns:
            return pd.DataFrame(columns=["symbol", "earnings_date", "eps_estimate", "revenue_estimate"])

        df["symbol"] = df["symbol"].astype(str).str.upper()
        df = df[df["symbol"].isin([s.upper() for s in symbols])].copy()
        df = df.rename(columns={"date": "earnings_date", "epsEstimate": "eps_estimate", "revenueEstimate": "revenue_estimate"})
        keep = [c for c in ["symbol", "earnings_date", "eps_estimate", "revenue_estimate"] if c in df.columns]
        df = df[keep].copy()
        if "earnings_date" in df.columns:
            df["earnings_date"] = pd.to_datetime(df["earnings_date"], utc=True, errors="coerce")
        return df.sort_values(["symbol", "earnings_date"]).reset_index(drop=True)

    def fetch_ohlcv_frames(
        self,
        symbol: str,
        daily_days: int = 450,
        hourly_days: int = 120,
        m15_days: int = 45,
    ) -> dict[str, pd.DataFrame]:
        now = datetime.now(timezone.utc)
        return {
            "1d":  self.get_stock_candles(symbol, "D",  now - timedelta(days=daily_days),  now),
            "1h":  self.get_stock_candles(symbol, "60", now - timedelta(days=hourly_days), now),
            "15m": self.get_stock_candles(symbol, "15", now - timedelta(days=m15_days),    now),
        }

    @staticmethod
    def write_symbol_parquets(
        data_root: str | Path,
        symbol: str,
        frames: dict[str, pd.DataFrame],
    ) -> None:
        root = Path(data_root)
        for tf in ("1d", "1h", "15m"):
            (root / tf).mkdir(parents=True, exist_ok=True)
        for tf, df in frames.items():
            if df is not None and not df.empty:
                final_path = root / tf / f"{symbol.upper()}.parquet"
                temp_path = final_path.with_name(f".{final_path.name}.{os.getpid()}.tmp")
                try:
                    df.to_parquet(temp_path, index=False)
                    os.replace(temp_path, final_path)
                finally:
                    if temp_path.exists():
                        temp_path.unlink(missing_ok=True)


