"""Watchlist construction helpers for Martin-style universe building."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class WatchlistConfig:
    min_price: float = 10.0
    max_price: float = 2000.0
    min_adr_pct: float = 5.0
    adr_lookback: int = 20
    min_dollar_volume_20d: float = 10_000_000.0
    dollar_vol_lookback: int = 20
    min_rs_rank_pct: float = 70.0
    rs_lookback_days: int = 252
    leading_rs_min: float = 80.0
    mediocre_rs_min: float = 50.0
    pillar_mcap_min: float = 100_000_000_000.0
    require_above_ema150: bool = True
    min_market_cap: float = 500_000_000.0


UniverseConfig = WatchlistConfig


@dataclass
class WatchlistEntry:
    symbol: str
    price: float
    adr_pct: float
    avg_dollar_volume_20d: float
    rs_rank_pct: float
    rs_1yr_pct: float
    above_ema150: bool
    market_cap: Optional[float]
    sector: str
    theme: str
    category: str
    volume_rank: float
    above_ema50: bool = False
    trend_bucket: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": round(self.price, 2),
            "adr_pct": round(self.adr_pct, 2),
            "avg_dollar_volume_20d": round(self.avg_dollar_volume_20d, 0),
            "rs_rank_pct": round(self.rs_rank_pct, 1),
            "rs_1yr_pct": round(self.rs_1yr_pct, 2),
            "above_ema50": self.above_ema50,
            "above_ema150": self.above_ema150,
            "market_cap": self.market_cap,
            "sector": self.sector,
            "theme": self.theme,
            "category": self.category,
            "trend_bucket": self.trend_bucket,
            "volume_rank": round(self.volume_rank, 1),
        }


def compute_adr_pct(df: pd.DataFrame, lookback: int = 20) -> float:
    recent = df.tail(lookback)
    adr = ((recent["high"] - recent["low"]) / recent["low"] * 100).mean()
    return float(adr)


def compute_dollar_volume(df: pd.DataFrame, lookback: int = 20) -> float:
    recent = df.tail(lookback)
    dollar_vol = (recent["close"] * recent["volume"]).mean()
    return float(dollar_vol)


def compute_rs_vs_benchmark(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    lookback_days: int = 252,
) -> float:
    stock_start_idx = -min(lookback_days, len(df))
    bench_start_idx = -min(lookback_days, len(benchmark_df))
    stock_ret = df["close"].iloc[-1] / df["close"].iloc[stock_start_idx] - 1
    bench_ret = benchmark_df["close"].iloc[-1] / benchmark_df["close"].iloc[bench_start_idx] - 1
    if bench_ret == 0:
        return 0.0
    return float(stock_ret / abs(bench_ret))


def compute_ema_value(df: pd.DataFrame, span: int) -> Optional[float]:
    if len(df) < span:
        return None
    ema = df["close"].ewm(span=span, adjust=False, min_periods=span).mean()
    value = ema.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def compute_ema150(df: pd.DataFrame) -> Optional[float]:
    return compute_ema_value(df, 150)


def classify_category(
    rs_rank_pct: float,
    market_cap: Optional[float],
    cfg: WatchlistConfig,
) -> str:
    if market_cap and market_cap >= cfg.pillar_mcap_min:
        return "pillar"
    if rs_rank_pct >= cfg.leading_rs_min:
        return "leading"
    if rs_rank_pct >= cfg.mediocre_rs_min:
        return "mediocre"
    return "lagging"


def classify_trend_bucket(
    close: float,
    ema50: Optional[float],
    ema150: Optional[float],
) -> str:
    if ema50 is None or ema150 is None:
        return "unclassified"
    if close > ema50 and ema50 > ema150:
        return "leading"
    if close < ema50 and ema50 < ema150:
        return "lagging"
    return "mediocre"


def _percentile_rank_map(value_map: dict[str, float]) -> dict[str, float]:
    if not value_map:
        return {}
    values = np.array(list(value_map.values()), dtype=float)
    return {sym: float(np.mean(values <= value) * 100) for sym, value in value_map.items()}


class WatchlistBuilder:
    """Build Martin-style watchlists from daily OHLCV data."""

    def __init__(self, config: Optional[WatchlistConfig] = None) -> None:
        self.config = config or WatchlistConfig()

    def _compute_metrics(self, df: pd.DataFrame) -> dict[str, float | None]:
        last_close = float(df["close"].iloc[-1])
        return {
            "last_close": last_close,
            "adr": compute_adr_pct(df, self.config.adr_lookback),
            "dvol": compute_dollar_volume(df, self.config.dollar_vol_lookback),
            "ema50": compute_ema_value(df, 50),
            "ema150": compute_ema_value(df, 150),
        }

    def _passes_common_filters(self, df: pd.DataFrame, metrics: dict[str, float | None]) -> bool:
        cfg = self.config
        if len(df) < 30:
            return False
        last_close = float(metrics["last_close"] or 0.0)
        if not (cfg.min_price <= last_close <= cfg.max_price):
            return False
        if float(metrics["adr"] or 0.0) < cfg.min_adr_pct:
            return False
        if float(metrics["dvol"] or 0.0) < cfg.min_dollar_volume_20d:
            return False
        return True

    def _entry_from_metrics(
        self,
        symbol: str,
        metrics: dict[str, float | None],
        rs_rank: float,
        rs_1yr_pct: float,
        volume_rank: float,
        metadata: Optional[dict[str, dict]],
    ) -> WatchlistEntry:
        sym_meta = (metadata or {}).get(symbol.upper(), {})
        market_cap = sym_meta.get("market_cap")
        ema50 = metrics.get("ema50")
        ema150 = metrics.get("ema150")
        last_close = float(metrics["last_close"] or 0.0)

        return WatchlistEntry(
            symbol=symbol.upper(),
            price=last_close,
            adr_pct=float(metrics["adr"] or 0.0),
            avg_dollar_volume_20d=float(metrics["dvol"] or 0.0),
            rs_rank_pct=rs_rank,
            rs_1yr_pct=rs_1yr_pct,
            above_ema50=bool(ema50 is not None and last_close > ema50),
            above_ema150=bool(ema150 is not None and last_close > ema150),
            market_cap=market_cap,
            sector=sym_meta.get("sector", "Unknown"),
            theme=sym_meta.get("theme", ""),
            category=classify_category(rs_rank, market_cap, self.config),
            trend_bucket=classify_trend_bucket(last_close, ema50, ema150),
            volume_rank=volume_rank,
        )

    def _build_rs_rank_maps(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        spy_df: Optional[pd.DataFrame],
    ) -> tuple[dict[str, float], dict[str, float]]:
        cfg = self.config
        rs_map: dict[str, float] = {}
        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None or len(df) < 20:
                continue
            if spy_df is not None:
                rs_map[sym] = compute_rs_vs_benchmark(df, spy_df, cfg.rs_lookback_days)
            else:
                lookback = min(cfg.rs_lookback_days, len(df))
                rs_map[sym] = float(df["close"].iloc[-1] / df["close"].iloc[-lookback] - 1)
        return rs_map, _percentile_rank_map(rs_map)

    def _build_volume_rank_map(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
    ) -> dict[str, float]:
        dvol_map: dict[str, float] = {}
        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None or len(df) < 20:
                continue
            dvol_map[sym] = compute_dollar_volume(df, self.config.dollar_vol_lookback)
        return _percentile_rank_map(dvol_map)

    def build(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        spy_df: Optional[pd.DataFrame] = None,
        metadata: Optional[dict[str, dict]] = None,
    ) -> list[WatchlistEntry]:
        cfg = self.config
        rs_map, rs_ranks = self._build_rs_rank_maps(symbols, ohlcv_map, spy_df)
        vol_ranks = self._build_volume_rank_map(symbols, ohlcv_map)

        entries: list[WatchlistEntry] = []
        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None:
                continue

            rs_rank = rs_ranks.get(sym, 0.0)
            if rs_rank < cfg.min_rs_rank_pct:
                continue

            metrics = self._compute_metrics(df)
            if not self._passes_common_filters(df, metrics):
                continue

            ema150 = metrics.get("ema150")
            if cfg.require_above_ema150 and ema150 is not None and float(metrics["last_close"]) < ema150:
                continue

            entries.append(
                self._entry_from_metrics(
                    symbol=sym,
                    metrics=metrics,
                    rs_rank=rs_rank,
                    rs_1yr_pct=rs_map.get(sym, 0.0) * 100,
                    volume_rank=vol_ranks.get(sym, 0.0),
                    metadata=metadata,
                )
            )

        return sorted(entries, key=lambda entry: entry.rs_rank_pct, reverse=True)

    def build_transcript_buckets(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        spy_df: Optional[pd.DataFrame] = None,
        metadata: Optional[dict[str, dict]] = None,
        include_unclassified: bool = False,
    ) -> dict[str, list[WatchlistEntry]]:
        """Bucket names follow the 2026-03-12 livestream rules.

        leading:
            price > EMA50 and EMA50 > EMA150
        lagging:
            price < EMA50 and EMA50 < EMA150
        mediocre:
            everything in between
        """
        rs_map, rs_ranks = self._build_rs_rank_maps(symbols, ohlcv_map, spy_df)
        vol_ranks = self._build_volume_rank_map(symbols, ohlcv_map)
        buckets: dict[str, list[WatchlistEntry]] = {
            "leading": [],
            "mediocre": [],
            "lagging": [],
        }
        if include_unclassified:
            buckets["unclassified"] = []

        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None:
                continue

            metrics = self._compute_metrics(df)
            if not self._passes_common_filters(df, metrics):
                continue

            entry = self._entry_from_metrics(
                symbol=sym,
                metrics=metrics,
                rs_rank=rs_ranks.get(sym, 0.0),
                rs_1yr_pct=rs_map.get(sym, 0.0) * 100,
                volume_rank=vol_ranks.get(sym, 0.0),
                metadata=metadata,
            )
            bucket_name = entry.trend_bucket or "unclassified"
            if bucket_name == "unclassified" and not include_unclassified:
                continue
            buckets.setdefault(bucket_name, []).append(entry)

        for bucket_name, items in buckets.items():
            reverse = bucket_name != "lagging"
            items.sort(key=lambda entry: entry.rs_rank_pct, reverse=reverse)
        return buckets

    def build_transcript_watchlists(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        spy_df: Optional[pd.DataFrame] = None,
        metadata: Optional[dict[str, dict]] = None,
        include_unclassified: bool = False,
    ) -> dict[str, list[WatchlistEntry]]:
        return self.build_transcript_buckets(
            symbols=symbols,
            ohlcv_map=ohlcv_map,
            spy_df=spy_df,
            metadata=metadata,
            include_unclassified=include_unclassified,
        )
