"""Transcript-driven breadth and leader participation overlay."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from martin_quant.scanners.leader_scanner import LeaderConfig, LeaderScanner


@dataclass
class BreadthParticipationConfig:
    min_bars: int = 80
    strong_pct_above_ema21: float = 0.65
    strong_pct_above_ema50: float = 0.55
    strong_pct_bull_stack: float = 0.40
    strong_leader_ratio: float = 0.08
    weak_pct_above_ema21: float = 0.35
    weak_pct_above_ema50: float = 0.25
    weak_pct_bull_stack: float = 0.15
    weak_leader_ratio: float = 0.03


@dataclass
class BreadthParticipationSnapshot:
    state: str
    universe_size: int
    pct_above_ema21: float
    pct_above_ema50: float
    pct_bull_stack: float
    leader_count: int
    leader_ratio: float
    exposure_factor: float
    top_sectors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def bonus_for(self, direction: str) -> float:
        state_map = {
            "EXPANDING": {"long": 0.08, "short": -0.03},
            "MIXED": {"long": 0.00, "short": 0.00},
            "SHRINKING": {"long": -0.05, "short": 0.04},
            "VERY_WEAK": {"long": -0.12, "short": 0.08},
        }
        direction_key = str(direction or "long").lower().strip()
        return state_map.get(self.state, state_map["MIXED"]).get(direction_key, 0.0)

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "universe_size": self.universe_size,
            "pct_above_ema21": round(self.pct_above_ema21, 3),
            "pct_above_ema50": round(self.pct_above_ema50, 3),
            "pct_bull_stack": round(self.pct_bull_stack, 3),
            "leader_count": self.leader_count,
            "leader_ratio": round(self.leader_ratio, 3),
            "exposure_factor": round(self.exposure_factor, 3),
            "top_sectors": self.top_sectors,
            "notes": self.notes,
        }


class BreadthParticipationAnalyzer:
    def __init__(self, config: Optional[BreadthParticipationConfig] = None) -> None:
        self.config = config or BreadthParticipationConfig()

    @staticmethod
    def _ema(close: pd.Series, span: int) -> float:
        return float(close.ewm(span=span, adjust=False, min_periods=min(span, max(5, span // 2))).mean().iloc[-1])

    def analyze(
        self,
        universe: dict[str, pd.DataFrame],
        sector_map: Optional[dict[str, str]] = None,
        spy_df: Optional[pd.DataFrame] = None,
    ) -> BreadthParticipationSnapshot:
        cfg = self.config
        sector_map = sector_map or {}
        eligible: dict[str, pd.DataFrame] = {}

        above_ema21 = 0
        above_ema50 = 0
        bull_stack = 0

        for symbol, df in universe.items():
            if df is None or len(df) < cfg.min_bars or "close" not in df.columns:
                continue
            close = df["close"].astype(float)
            ema9 = self._ema(close, 9)
            ema21 = self._ema(close, 21)
            ema50 = self._ema(close, 50)
            last_close = float(close.iloc[-1])
            eligible[symbol] = df
            if last_close > ema21:
                above_ema21 += 1
            if last_close > ema50:
                above_ema50 += 1
            if ema9 > ema21 > ema50:
                bull_stack += 1

        universe_size = len(eligible)
        if universe_size == 0:
            return BreadthParticipationSnapshot(
                state="MIXED",
                universe_size=0,
                pct_above_ema21=0.0,
                pct_above_ema50=0.0,
                pct_bull_stack=0.0,
                leader_count=0,
                leader_ratio=0.0,
                exposure_factor=0.75,
                notes=["no eligible symbols for breadth analysis"],
            )

        pct_above_ema21 = above_ema21 / universe_size
        pct_above_ema50 = above_ema50 / universe_size
        pct_bull_stack = bull_stack / universe_size

        leader_count = 0
        top_sectors: list[str] = []
        leader_universe = {symbol: df for symbol, df in eligible.items() if len(df) >= 252}
        if spy_df is not None and leader_universe:
            scanner = LeaderScanner(config=LeaderConfig(history_file='data/leader_history_scan_v2.json'), spy_df=spy_df)
            scanner._save_history = lambda health: None
            leaders, health = scanner.build_leader_list(leader_universe, sector_map=sector_map)
            leader_count = len(leaders)
            top_sectors = list(health.top_sectors)
        leader_ratio = leader_count / universe_size if universe_size else 0.0

        strong_hits = sum(
            [
                pct_above_ema21 >= cfg.strong_pct_above_ema21,
                pct_above_ema50 >= cfg.strong_pct_above_ema50,
                pct_bull_stack >= cfg.strong_pct_bull_stack,
                leader_ratio >= cfg.strong_leader_ratio,
            ]
        )
        weak_hits = sum(
            [
                pct_above_ema21 <= cfg.weak_pct_above_ema21,
                pct_above_ema50 <= cfg.weak_pct_above_ema50,
                pct_bull_stack <= cfg.weak_pct_bull_stack,
                leader_ratio <= cfg.weak_leader_ratio,
            ]
        )

        notes = [
            f"{pct_above_ema21:.0%} above EMA21",
            f"{pct_above_ema50:.0%} above EMA50",
            f"{pct_bull_stack:.0%} bull-stacked",
            f"leaders={leader_count}/{universe_size}",
        ]
        if strong_hits >= 3 and pct_above_ema21 >= 0.55 and pct_above_ema50 >= 0.45:
            state = "EXPANDING"
            exposure_factor = 1.0
            notes.append("broad participation and enough leaders to press longs")
        elif weak_hits >= 3:
            state = "VERY_WEAK"
            exposure_factor = 0.35
            notes.append("breadth collapsed, size down aggressively")
        elif pct_above_ema21 < 0.50 or pct_above_ema50 < 0.40 or leader_ratio < max(cfg.strong_leader_ratio * 0.75, 0.05):
            state = "SHRINKING"
            exposure_factor = 0.60
            notes.append("participation is shrinking, demand tighter confirmation")
        else:
            state = "MIXED"
            exposure_factor = 0.80
            notes.append("mixed participation, keep selectivity high")

        return BreadthParticipationSnapshot(
            state=state,
            universe_size=universe_size,
            pct_above_ema21=pct_above_ema21,
            pct_above_ema50=pct_above_ema50,
            pct_bull_stack=pct_bull_stack,
            leader_count=leader_count,
            leader_ratio=leader_ratio,
            exposure_factor=exposure_factor,
            top_sectors=top_sectors,
            notes=notes,
        )
