from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from martin_quant.features.atr import compute_adr, compute_atr
from martin_quant.features.ema import compute_ema


@dataclass(slots=True)
class UniverseConfig:
    # Liquidity filters
    min_price: float = 5.0
    min_avg_volume_20d: int = 300_000
    min_avg_dollar_volume_20d: float = 10_000_000.0
    min_adr_pct: float = 3.0

    # RS / Relative Strength
    rs_lookback_days: int = 60
    min_rs_rank_pct: float = 60.0
    benchmark_symbol: str = "SPY"

    # Portfolio construction
    max_size: int = 30
    max_per_sector: int = 6
    min_days_to_earnings: int = 3

    # Earnings
    earnings_path: str = ""


@dataclass
class WatchlistEntry:
    symbol: str
    price: float
    avg_volume_20d: float
    avg_dollar_volume_20d: float
    adr_pct: float
    rs_raw: float
    rs_rank_pct: float
    sector: str = ""
    theme: str = ""
    days_to_earnings: int = 999
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "avg_volume_20d": self.avg_volume_20d,
            "avg_dollar_volume_20d": self.avg_dollar_volume_20d,
            "adr_pct": self.adr_pct,
            "rs_raw": self.rs_raw,
            "rs_rank_pct": self.rs_rank_pct,
            "sector": self.sector,
            "theme": self.theme,
            "days_to_earnings": self.days_to_earnings,
            **self.metadata,
        }


class WatchlistBuilder:
    """
    Second-pass universe filter.

    Input:
      - candidate_symbols: list from Finviz screens
      - ohlcv_map: dict[symbol -> daily DataFrame with open/high/low/close/volume]
      - metadata_df: DataFrame with symbol, sector, theme columns
      - earnings_df: DataFrame with symbol, earnings_date columns (optional)

    Output:
      - list[WatchlistEntry] sorted by rs_rank_pct descending
      - DataFrame version for easy CSV/parquet export
    """

    def __init__(self, config: UniverseConfig | None = None) -> None:
        self.config = config or UniverseConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        candidate_symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        metadata_df: pd.DataFrame | None = None,
        earnings_df: pd.DataFrame | None = None,
    ) -> list[WatchlistEntry]:
        cfg = self.config
        metadata_df = self._prep_metadata(metadata_df)
        earnings_map = self._build_earnings_map(earnings_df)
        benchmark_rs = self._compute_benchmark_return(ohlcv_map)

        entries: list[WatchlistEntry] = []

        for symbol in candidate_symbols:
            symbol = symbol.upper()
            if symbol == cfg.benchmark_symbol.upper():
                continue

            df = ohlcv_map.get(symbol)
            if df is None or df.empty or len(df) < max(cfg.rs_lookback_days, 20):
                continue

            df = df.copy().sort_values("timestamp").reset_index(drop=True)
            price = float(df["close"].iloc[-1])
            if price < cfg.min_price:
                continue

            avg_vol = float(df["volume"].tail(20).mean())
            if avg_vol < cfg.min_avg_volume_20d:
                continue

            avg_dollar_vol = float((df["close"] * df["volume"]).tail(20).mean())
            if avg_dollar_vol < cfg.min_avg_dollar_volume_20d:
                continue

            adr = compute_adr(df, period=20)
            adr_pct_val = float(adr.iloc[-1] / price * 100) if not adr.isna().all() else 0.0
            if adr_pct_val < cfg.min_adr_pct:
                continue

            rs_raw = self._compute_rs(df, lookback=cfg.rs_lookback_days, benchmark_return=benchmark_rs)

            days_to_earn = earnings_map.get(symbol, 999)
            if days_to_earn < cfg.min_days_to_earnings:
                continue

            meta_row = metadata_df[metadata_df["symbol"] == symbol]
            sector = str(meta_row["sector"].iloc[0]) if not meta_row.empty and "sector" in meta_row.columns else ""
            theme  = str(meta_row["theme"].iloc[0])  if not meta_row.empty and "theme"  in meta_row.columns else ""

            entries.append(WatchlistEntry(
                symbol=symbol,
                price=price,
                avg_volume_20d=avg_vol,
                avg_dollar_volume_20d=avg_dollar_vol,
                adr_pct=round(adr_pct_val, 2),
                rs_raw=round(rs_raw, 4),
                rs_rank_pct=0.0,
                sector=sector,
                theme=theme,
                days_to_earnings=days_to_earn,
            ))

        entries = self._apply_rs_ranks(entries)
        entries = [e for e in entries if e.rs_rank_pct >= cfg.min_rs_rank_pct]
        entries = self._cap_per_sector(entries)
        entries = entries[: cfg.max_size]

        return entries

    def to_dataframe(self, entries: list[WatchlistEntry]) -> pd.DataFrame:
        if not entries:
            return pd.DataFrame()
        return pd.DataFrame([e.to_dict() for e in entries])

    def save(
        self,
        entries: list[WatchlistEntry],
        output_dir: str | Path,
        universe_txt: str | Path | None = None,
    ) -> None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        df = self.to_dataframe(entries)
        if not df.empty:
            df.to_csv(output_dir / "watchlist.csv", index=False)
            df.to_parquet(output_dir / "watchlist.parquet", index=False)

        if universe_txt is not None:
            Path(universe_txt).parent.mkdir(parents=True, exist_ok=True)
            symbols = [e.symbol for e in entries]
            Path(universe_txt).write_text("\n".join(symbols) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _prep_metadata(metadata_df: pd.DataFrame | None) -> pd.DataFrame:
        if metadata_df is None or metadata_df.empty:
            return pd.DataFrame(columns=["symbol", "sector", "theme"])
        out = metadata_df.copy()
        if "symbol" in out.columns:
            out["symbol"] = out["symbol"].astype(str).str.upper()
        return out

    @staticmethod
    def _build_earnings_map(
        earnings_df: pd.DataFrame | None,
    ) -> dict[str, int]:
        if earnings_df is None or earnings_df.empty:
            return {}
        today = pd.Timestamp.utcnow().normalize()
        out: dict[str, int] = {}
        for _, row in earnings_df.iterrows():
            symbol = str(row.get("symbol", "")).upper()
            edate = pd.to_datetime(row.get("earnings_date"), utc=True, errors="coerce")
            if pd.isna(edate) or not symbol:
                continue
            days = (edate.normalize() - today).days
            if symbol not in out or days < out[symbol]:
                out[symbol] = max(0, int(days))
        return out

    def _compute_benchmark_return(self, ohlcv_map: dict[str, pd.DataFrame]) -> float:
        bm = self.config.benchmark_symbol.upper()
        df = ohlcv_map.get(bm)
        if df is None or df.empty or len(df) < self.config.rs_lookback_days:
            return 0.0
        df = df.sort_values("timestamp").reset_index(drop=True)
        start = float(df["close"].iloc[-self.config.rs_lookback_days])
        end = float(df["close"].iloc[-1])
        return (end - start) / start if start != 0 else 0.0

    def _compute_rs(
        self,
        df: pd.DataFrame,
        lookback: int,
        benchmark_return: float,
    ) -> float:
        if len(df) < lookback:
            return 0.0
        start = float(df["close"].iloc[-lookback])
        end = float(df["close"].iloc[-1])
        stock_return = (end - start) / start if start != 0 else 0.0
        return stock_return - benchmark_return

    @staticmethod
    def _apply_rs_ranks(entries: list[WatchlistEntry]) -> list[WatchlistEntry]:
        if not entries:
            return entries
        rs_values = [e.rs_raw for e in entries]
        rs_series = pd.Series(rs_values)
        ranks = rs_series.rank(pct=True) * 100.0
        for entry, rank in zip(entries, ranks):
            entry.rs_rank_pct = round(float(rank), 1)
        return sorted(entries, key=lambda e: e.rs_rank_pct, reverse=True)

    def _cap_per_sector(self, entries: list[WatchlistEntry]) -> list[WatchlistEntry]:
        cap = self.config.max_per_sector
        sector_counts: dict[str, int] = {}
        result: list[WatchlistEntry] = []
        for e in entries:
            sector = e.sector or "Unknown"
            count = sector_counts.get(sector, 0)
            if count < cap:
                result.append(e)
                sector_counts[sector] = count + 1
        return result
