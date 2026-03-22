"""Dynamic sector relative strength overlay for scan-v2."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .sector_regime_filter import SECTOR_ETF_MAP, normalize_sector_name


@dataclass
class SectorRelativeStrengthConfig:
    min_bars: int = 40
    fast_lookback: int = 21
    slow_lookback: int = 63
    strong_relative_threshold: float = 0.03
    weak_relative_threshold: float = -0.03
    strong_member_ratio: float = 0.25
    weak_member_ratio: float = 0.15


@dataclass
class SectorStrengthSnapshot:
    sector: str
    canonical_sector: str
    etf_symbol: str
    member_count: int
    sector_relative_score: float
    avg_member_return: float
    avg_member_relative_to_sector: float
    pct_members_above_ema21: float
    leading_member_ratio: float
    state: str
    notes: list[str] = field(default_factory=list)

    def bonus_for(self, direction: str) -> float:
        direction_key = str(direction or "long").lower().strip()
        matrix = {
            "STRONG": {"long": 0.10, "short": -0.06},
            "NEUTRAL": {"long": 0.00, "short": 0.00},
            "WEAK": {"long": -0.08, "short": 0.10},
        }
        return matrix.get(self.state, matrix["NEUTRAL"]).get(direction_key, 0.0)

    def to_dict(self) -> dict[str, object]:
        return {
            "sector": self.sector,
            "canonical_sector": self.canonical_sector,
            "etf_symbol": self.etf_symbol,
            "member_count": self.member_count,
            "sector_relative_score": round(self.sector_relative_score, 4),
            "avg_member_return": round(self.avg_member_return, 4),
            "avg_member_relative_to_sector": round(self.avg_member_relative_to_sector, 4),
            "pct_members_above_ema21": round(self.pct_members_above_ema21, 3),
            "leading_member_ratio": round(self.leading_member_ratio, 3),
            "state": self.state,
            "notes": self.notes,
        }


class DynamicSectorRelativeStrengthAnalyzer:
    def __init__(self, config: Optional[SectorRelativeStrengthConfig] = None) -> None:
        self.config = config or SectorRelativeStrengthConfig()

    @staticmethod
    def _return(close: pd.Series, lookback: int) -> float:
        if close.empty or len(close) < 2:
            return 0.0
        effective = min(lookback, len(close) - 1)
        if effective <= 0:
            return 0.0
        start = float(close.iloc[-effective - 1])
        end = float(close.iloc[-1])
        return (end / start - 1.0) if start else 0.0

    @staticmethod
    def _ema(close: pd.Series, span: int) -> float:
        return float(close.ewm(span=span, adjust=False, min_periods=min(span, max(5, span // 2))).mean().iloc[-1])

    def analyze_universe(
        self,
        universe: dict[str, pd.DataFrame],
        sector_map: dict[str, str],
        sector_etf_data: Optional[dict[str, pd.DataFrame]],
        benchmark_df: Optional[pd.DataFrame],
    ) -> dict[str, SectorStrengthSnapshot]:
        cfg = self.config
        if benchmark_df is None or sector_etf_data is None:
            return {}

        grouped: dict[str, list[tuple[str, pd.DataFrame, str]]] = {}
        for symbol, df in universe.items():
            raw_sector = sector_map.get(symbol, "")
            canonical_sector = normalize_sector_name(raw_sector)
            if not raw_sector or df is None or len(df) < cfg.min_bars:
                continue
            grouped.setdefault(canonical_sector, []).append((symbol, df, raw_sector))

        benchmark_close = benchmark_df["close"].astype(float) if "close" in benchmark_df.columns else pd.Series(dtype=float)
        benchmark_fast = self._return(benchmark_close, cfg.fast_lookback)
        benchmark_slow = self._return(benchmark_close, cfg.slow_lookback)
        results: dict[str, SectorStrengthSnapshot] = {}

        for canonical_sector, members in grouped.items():
            etf_df = sector_etf_data.get(canonical_sector)
            if etf_df is None or etf_df.empty or "close" not in etf_df.columns:
                continue

            sector_close = etf_df["close"].astype(float)
            sector_fast = self._return(sector_close, cfg.fast_lookback)
            sector_slow = self._return(sector_close, cfg.slow_lookback)
            sector_relative_score = ((sector_fast - benchmark_fast) * 0.6) + ((sector_slow - benchmark_slow) * 0.4)

            member_returns: list[float] = []
            member_relative_to_sector: list[float] = []
            above_ema21 = 0
            leaders = 0

            for _, df, _ in members:
                close = df["close"].astype(float)
                member_fast = self._return(close, cfg.fast_lookback)
                member_returns.append(member_fast)
                member_relative = member_fast - sector_fast
                member_relative_to_sector.append(member_relative)
                if float(close.iloc[-1]) > self._ema(close, 21):
                    above_ema21 += 1
                if member_relative > 0 and float(close.iloc[-1]) > self._ema(close, 21):
                    leaders += 1

            member_count = len(members)
            avg_member_return = sum(member_returns) / member_count if member_count else 0.0
            avg_member_relative_to_sector = sum(member_relative_to_sector) / member_count if member_count else 0.0
            pct_members_above_ema21 = above_ema21 / member_count if member_count else 0.0
            leading_member_ratio = leaders / member_count if member_count else 0.0

            notes = [
                f"sector_rel={sector_relative_score:.1%}",
                f"members_above_ema21={pct_members_above_ema21:.0%}",
                f"leader_members={leaders}/{member_count}",
            ]
            if (
                sector_relative_score >= cfg.strong_relative_threshold
                and pct_members_above_ema21 >= 0.55
                and leading_member_ratio >= cfg.strong_member_ratio
            ):
                state = "STRONG"
                notes.append("sector is outperforming and participation is broad")
            elif (
                sector_relative_score <= cfg.weak_relative_threshold
                and pct_members_above_ema21 <= 0.45
                and leading_member_ratio <= cfg.weak_member_ratio
            ):
                state = "WEAK"
                notes.append("sector is lagging and internal participation is poor")
            else:
                state = "NEUTRAL"
                notes.append("sector leadership is mixed")

            results[canonical_sector] = SectorStrengthSnapshot(
                sector=members[0][2],
                canonical_sector=canonical_sector,
                etf_symbol=SECTOR_ETF_MAP.get(canonical_sector, canonical_sector.upper()),
                member_count=member_count,
                sector_relative_score=sector_relative_score,
                avg_member_return=avg_member_return,
                avg_member_relative_to_sector=avg_member_relative_to_sector,
                pct_members_above_ema21=pct_members_above_ema21,
                leading_member_ratio=leading_member_ratio,
                state=state,
                notes=notes,
            )
        return results
