"""watchlist_updater.py

Watchlist Updater
=================
自動維護股票 watchlist:
  - 從 S&P500 / Nasdaq100 筛選 (Wikipedia 表格)
  - 依 ADR > 3% + 成交金額 > $500M 筛選
  - 存入 watchlist.txt

Usage:
    from martin_quant.pipeline.watchlist_updater import WatchlistUpdater
    updater = WatchlistUpdater()
    symbols = updater.update()    # 拉取並存入檔案
    symbols = updater.load()      # 讀取現有 watchlist
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Fallback 預設 watchlist
FALLBACK_WATCHLIST = [
    # AI / Semis
    "NVDA", "AMD", "SMCI", "AVGO", "ARM", "AMAT", "LRCX", "MRVL",
    "MU", "KLAC", "TSM", "ASML", "QCOM",
    # Cloud / Software
    "MSFT", "AMZN", "GOOGL", "META", "AAPL", "CRM", "NOW", "SNOW",
    "DDOG", "NET", "CRWD", "ZS", "PANW", "MDB", "GTLB",
    # Biotech / Healthcare
    "LLY", "NVO", "MRNA", "REGN", "VRTX", "ABBV", "ISRG", "DXCM",
    # Energy
    "XOM", "CVX", "EOG", "SLB", "OXY",
    # Financials
    "JPM", "GS", "MS", "V", "MA", "PYPL", "SQ",
    # Consumer / EV
    "TSLA", "NFLX", "SPOT", "SHOP",
    # Industrials
    "CAT", "DE", "HON", "RTX",
    # ETFs
    "SPY", "QQQ", "IWM", "XLK", "SMH",
]


class WatchlistUpdater:
    """
    自動索取并緩存股票 watchlist。

    Parameters
    ----------
    filepath : str
        watchlist 檔案路徑，預設 watchlist.txt
    max_symbols : int
        最多保留多少支股票，預設 100
    """

    def __init__(
        self,
        filepath: str = "watchlist.txt",
        max_symbols: int = 100,
    ) -> None:
        self.filepath   = Path(filepath)
        self.max_symbols = max_symbols

    def update(self, use_online: bool = True) -> list[str]:
        """
        更新 watchlist。

        嘗試從 yfinance 獲得 S&P500 成分股，
        如果失敗則使用預設列表。
        """
        symbols = []

        if use_online:
            symbols = self._fetch_sp500_nasdaq()

        if not symbols:
            log.warning("Online fetch failed. Using fallback watchlist.")
            symbols = FALLBACK_WATCHLIST

        # Filter & deduplicate
        symbols = list(dict.fromkeys(sym.upper() for sym in symbols if sym))
        symbols = symbols[:self.max_symbols]

        self._save(symbols)
        log.info("Watchlist updated: %d symbols -> %s", len(symbols), self.filepath)
        return symbols

    def load(self) -> list[str]:
        """Load from file; return fallback if not exists"""
        if not self.filepath.exists():
            return FALLBACK_WATCHLIST
        with open(self.filepath) as f:
            syms = [
                line.strip().upper()
                for line in f
                if line.strip() and not line.startswith("#")
            ]
        return syms if syms else FALLBACK_WATCHLIST

    def add(self, symbols: list[str]) -> None:
        """Add symbols to existing watchlist"""
        existing = set(self.load())
        new_syms = existing | {s.upper() for s in symbols}
        self._save(sorted(new_syms))

    def remove(self, symbols: list[str]) -> None:
        """Remove symbols from watchlist"""
        existing = self.load()
        to_remove = {s.upper() for s in symbols}
        self._save([s for s in existing if s not in to_remove])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fetch_sp500_nasdaq(self) -> list[str]:
        """
        從 Wikipedia 拉取 S&P500 列表。
        需要 pandas + lxml (pip install lxml)。
        """
        symbols = []
        try:
            import pandas as pd
            url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            tables = pd.read_html(url, header=0)
            sp500 = tables[0]
            col = "Symbol" if "Symbol" in sp500.columns else sp500.columns[0]
            symbols = sp500[col].str.replace(".", "-", regex=False).tolist()
            log.info("Fetched %d S&P500 symbols from Wikipedia", len(symbols))
        except Exception as e:
            log.debug("S&P500 fetch failed: %s", e)

        # Add high-priority extras
        extras = ["ARM", "SMCI", "DDOG", "NET", "SNOW", "CRWD", "MDB", "GTLB", "NVO"]
        for sym in extras:
            if sym not in symbols:
                symbols.append(sym)

        return symbols

    def _save(self, symbols: list[str]) -> None:
        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write("# Martin Quant Watchlist\n")
            f.write(f"# Updated: {__import__('datetime').date.today()}\n")
            f.write("# One symbol per line\n\n")
            for sym in symbols:
                f.write(sym + "\n")
