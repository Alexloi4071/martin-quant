from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


@dataclass
class FinvizScreenDefinition:
    name: str
    filters: list[str]
    order: str = "-marketcap"
    signal: str = ""
    limit: int = 120


@dataclass
class FinvizProviderConfig:
    base_url: str = "https://finviz.com/screener.ashx"
    view: str = "111"
    page_size: int = 20
    timeout: int = 20
    pause_seconds: float = 0.8
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0 Safari/537.36"
    )


class FinvizProvider:
    """
    Programmatic Finviz screener reader.

    Design:
    - Accepts raw Finviz filter tags from config.
    - Supports multiple screens and unions them.
    - Returns a candidate universe; Martin logic still does final ranking/filtering.
    """

    def __init__(self, config: FinvizProviderConfig | None = None) -> None:
        self.config = config or FinvizProviderConfig()

    @staticmethod
    def _filter_symbol_rows(df: pd.DataFrame) -> pd.DataFrame:
        """
        Keep only rows that look like real ticker symbols.

        Finviz layout changes can cause pd.read_html() to parse filter controls,
        nav rows, or menu headers instead of the screener results.
        """
        if "symbol" not in df.columns:
            return pd.DataFrame(columns=["symbol"])

        out = df.copy()
        out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()

        invalid_symbols = {
            "", "NAN", "TICKER", "SYMBOL", "NO.", "OVERVIEW", "VALUATION",
            "FINANCIAL", "OWNERSHIP", "PERFORMANCE", "TECHNICAL", "ETF",
            "NEWS", "SNAPSHOT", "MAPS", "STATS",
        }
        valid_mask = out["symbol"].str.fullmatch(r"[A-Z][A-Z\.\-]{0,9}", na=False)
        valid_mask &= ~out["symbol"].isin(invalid_symbols)

        if "company_name" in out.columns:
            company = out["company_name"].astype(str).str.strip()
            valid_mask &= company.ne("")
            valid_mask &= ~company.str.contains("Overview|Valuation|Technical|Snapshot", case=False, na=False)

        filtered = out.loc[valid_mask].drop_duplicates(subset=["symbol"]).reset_index(drop=True)
        if filtered.empty:
            return pd.DataFrame(columns=out.columns)
        return filtered

    def _build_url(
        self,
        filters: Iterable[str],
        order: str,
        signal: str = "",
        start_row: int = 1,
    ) -> str:
        params = {
            "v": self.config.view,
            "f": ",".join(filters),
            "o": order,
        }
        if signal:
            params["s"] = signal
        if start_row > 1:
            params["r"] = str(start_row)
        return f"{self.config.base_url}?{urlencode(params)}"

    def _fetch_html(self, url: str) -> str:
        req = Request(
            url,
            headers={
                "User-Agent": self.config.user_agent,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://finviz.com/",
            },
        )
        with urlopen(req, timeout=self.config.timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    @staticmethod
    def _snake_case_columns(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out.columns = [
            str(c).strip().lower().replace(" ", "_").replace("/", "_").replace("%", "pct")
            for c in out.columns
        ]
        return out

    @staticmethod
    def _coerce_common_columns(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()

        rename_map = {}
        if "ticker" in out.columns:
            rename_map["ticker"] = "symbol"
        if "company" in out.columns:
            rename_map["company"] = "company_name"
        out = out.rename(columns=rename_map)

        for col in out.columns:
            if out[col].dtype == "object":
                out[col] = out[col].astype(str).str.strip()

        numeric_like = [
            "price", "change", "volume", "market_cap", "p_e", "forward_p_e",
            "peg", "eps_growth_this_year", "eps_growth_next_year",
            "sales_growth_qtr_over_qtr", "roe", "roa", "perf_week",
            "perf_month", "perf_quarter", "perf_half_y", "perf_year",
            "atr", "rsi_(14)", "avg_volume", "rel_volume",
        ]
        for col in numeric_like:
            if col in out.columns:
                out[col] = (
                    out[col]
                    .astype(str)
                    .str.replace("%", "", regex=False)
                    .str.replace(",", "", regex=False)
                    .str.replace("B", "e9", regex=False)
                    .str.replace("M", "e6", regex=False)
                    .str.replace("K", "e3", regex=False)
                    .replace("-", pd.NA)
                )
                out[col] = pd.to_numeric(out[col], errors="coerce")

        if "symbol" in out.columns:
            out["symbol"] = out["symbol"].astype(str).str.upper()

        return out

    def _extract_table(self, html: str) -> pd.DataFrame:
        try:
            tables = pd.read_html(io.StringIO(html))
            for table in tables:
                cols = [str(c).strip().lower() for c in table.columns]
                if "ticker" in cols or "company" in cols:
                    cleaned = self._coerce_common_columns(self._snake_case_columns(table))
                    cleaned = self._filter_symbol_rows(cleaned)
                    if not cleaned.empty:
                        return cleaned
        except Exception:
            pass

        tickers = sorted(set(re.findall(r"/quote\.ashx\?t=([A-Z\.\-]+)", html)))
        if not tickers:
            return pd.DataFrame(columns=["symbol"])
        tickers = [ticker for ticker in tickers if ticker not in {"NAN", "TICKER", "SYMBOL"}]
        return pd.DataFrame({"symbol": tickers})

    def screen(self, definition: FinvizScreenDefinition) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        rows_left = max(1, int(definition.limit))
        start_row = 1

        while rows_left > 0:
            url = self._build_url(
                filters=definition.filters,
                order=definition.order,
                signal=definition.signal,
                start_row=start_row,
            )
            html = self._fetch_html(url)
            df = self._extract_table(html)

            if df.empty or "symbol" not in df.columns:
                break

            df["screen_name"] = definition.name
            df["screen_order"] = definition.order
            df["screen_signal"] = definition.signal
            frames.append(df)

            if len(df) < self.config.page_size:
                break

            rows_left -= len(df)
            start_row += self.config.page_size
            time.sleep(self.config.pause_seconds)

        if not frames:
            return pd.DataFrame(columns=["symbol", "screen_name"])

        out = pd.concat(frames, ignore_index=True)
        out = out.drop_duplicates(subset=["symbol", "screen_name"])
        return out.head(definition.limit).reset_index(drop=True)

    def screen_many(
        self,
        definitions: list[FinvizScreenDefinition],
        dedupe: bool = True,
    ) -> pd.DataFrame:
        frames = []
        for definition in definitions:
            df = self.screen(definition)
            if not df.empty:
                frames.append(df)
                time.sleep(self.config.pause_seconds)

        if not frames:
            return pd.DataFrame(columns=["symbol", "screen_name"])

        out = pd.concat(frames, ignore_index=True)

        if dedupe and "symbol" in out.columns:
            agg_cols = [col for col in out.columns if col != "screen_name"]
            out = (
                out.sort_values(["symbol", "screen_name"])
                .groupby("symbol", as_index=False)
                .first()[agg_cols]
            )

        return out.reset_index(drop=True)
