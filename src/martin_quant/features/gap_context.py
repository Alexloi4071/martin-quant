from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from martin_quant.features.ema import compute_ema


@dataclass
class GapContextConfig:
    significant_gap_pct: float = 1.0
    near_level_tolerance_pct: float = 1.0
    ema_spans: tuple[int, ...] = (9, 21, 50, 150)


@dataclass
class GapContext:
    gap_pct: float
    direction: str
    label: str
    session_open: float
    prev_close: float
    current_price: float
    fill_pct: float
    filled_gap: bool
    nearest_support: Optional[str] = None
    nearest_support_value: Optional[float] = None
    nearest_support_distance_pct: Optional[float] = None
    nearest_resistance: Optional[str] = None
    nearest_resistance_value: Optional[float] = None
    nearest_resistance_distance_pct: Optional[float] = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "gap_pct": round(self.gap_pct, 3),
            "direction": self.direction,
            "label": self.label,
            "session_open": round(self.session_open, 4),
            "prev_close": round(self.prev_close, 4),
            "current_price": round(self.current_price, 4),
            "fill_pct": round(self.fill_pct, 3),
            "filled_gap": self.filled_gap,
            "nearest_support": self.nearest_support,
            "nearest_support_value": round(self.nearest_support_value, 4) if self.nearest_support_value is not None else None,
            "nearest_support_distance_pct": round(self.nearest_support_distance_pct, 3) if self.nearest_support_distance_pct is not None else None,
            "nearest_resistance": self.nearest_resistance,
            "nearest_resistance_value": round(self.nearest_resistance_value, 4) if self.nearest_resistance_value is not None else None,
            "nearest_resistance_distance_pct": round(self.nearest_resistance_distance_pct, 3) if self.nearest_resistance_distance_pct is not None else None,
            "notes": self.notes,
        }


class GapContextAnalyzer:
    def __init__(self, config: Optional[GapContextConfig] = None) -> None:
        self.config = config or GapContextConfig()

    def analyze(
        self,
        daily_df: Optional[pd.DataFrame] = None,
        intraday_df: Optional[pd.DataFrame] = None,
        current_price: Optional[float] = None,
    ) -> Optional[GapContext]:
        if intraday_df is not None and not intraday_df.empty:
            intraday = intraday_df.copy()
            if "timestamp" not in intraday.columns:
                intraday = intraday.reset_index().rename(columns={intraday.index.name or "index": "timestamp"})
            intraday = intraday.sort_values("timestamp").reset_index(drop=True)
            intraday["timestamp"] = pd.to_datetime(intraday["timestamp"], utc=True)
            trade_date = intraday["timestamp"].iloc[-1].date()
            today = intraday[intraday["timestamp"].dt.date == trade_date]
            prev = intraday[intraday["timestamp"].dt.date < trade_date]
            if today.empty or prev.empty:
                return None
            session_open = float(today.iloc[0]["open"])
            prev_close = float(prev.iloc[-1]["close"])
            live_price = float(current_price if current_price is not None else today.iloc[-1]["close"])
        elif daily_df is not None and len(daily_df) >= 2:
            daily = daily_df.copy().reset_index(drop=True)
            session_open = float(daily.iloc[-1]["open"])
            prev_close = float(daily.iloc[-2]["close"])
            live_price = float(current_price if current_price is not None else daily.iloc[-1]["close"])
        else:
            return None

        if prev_close == 0:
            return None

        gap_pct = (session_open / prev_close - 1.0) * 100.0
        direction = "gap_up" if gap_pct > 0 else "gap_down" if gap_pct < 0 else "flat"
        levels = self._reference_levels(daily_df)
        support = self._nearest_level(levels, session_open, side="support")
        resistance = self._nearest_level(levels, session_open, side="resistance")
        fill_pct = self._gap_fill_pct(direction, session_open, prev_close, live_price)
        filled_gap = fill_pct >= 1.0
        label, notes = self._classify_gap(gap_pct, direction, support, resistance, filled_gap)

        return GapContext(
            gap_pct=float(gap_pct),
            direction=direction,
            label=label,
            session_open=session_open,
            prev_close=prev_close,
            current_price=live_price,
            fill_pct=float(fill_pct),
            filled_gap=filled_gap,
            nearest_support=support[0] if support else None,
            nearest_support_value=support[1] if support else None,
            nearest_support_distance_pct=support[2] if support else None,
            nearest_resistance=resistance[0] if resistance else None,
            nearest_resistance_value=resistance[1] if resistance else None,
            nearest_resistance_distance_pct=resistance[2] if resistance else None,
            notes=notes,
        )

    def _reference_levels(self, daily_df: Optional[pd.DataFrame]) -> dict[str, float]:
        if daily_df is None or len(daily_df) < 2:
            return {}
        daily = daily_df.copy().reset_index(drop=True)
        levels: dict[str, float] = {
            "prev_close": float(daily.iloc[-2]["close"]),
            "prev_day_high": float(daily.iloc[-2]["high"]),
            "prev_day_low": float(daily.iloc[-2]["low"]),
        }
        closes = pd.to_numeric(daily["close"], errors="coerce")
        for span in self.config.ema_spans:
            if len(closes) >= span:
                ema = compute_ema(closes, span)
                value = ema.iloc[-2] if len(ema) >= 2 else ema.iloc[-1]
                if not pd.isna(value):
                    levels[f"ema_{span}"] = float(value)
        return levels

    def _nearest_level(
        self,
        levels: dict[str, float],
        price: float,
        side: str,
    ) -> Optional[tuple[str, float, float]]:
        candidates: list[tuple[str, float, float]] = []
        for name, value in levels.items():
            if side == "support" and value <= price:
                distance = abs(price - value) / value * 100.0 if value else 0.0
                candidates.append((name, value, distance))
            if side == "resistance" and value >= price:
                distance = abs(value - price) / value * 100.0 if value else 0.0
                candidates.append((name, value, distance))
        if not candidates:
            return None
        return min(candidates, key=lambda item: item[2])

    @staticmethod
    def _gap_fill_pct(direction: str, session_open: float, prev_close: float, current_price: float) -> float:
        gap_size = abs(session_open - prev_close)
        if gap_size == 0:
            return 1.0
        if direction == "gap_up":
            progress = (session_open - current_price) / gap_size
        elif direction == "gap_down":
            progress = (current_price - session_open) / gap_size
        else:
            return 1.0
        return max(0.0, min(float(progress), 1.0))

    def _classify_gap(
        self,
        gap_pct: float,
        direction: str,
        support: Optional[tuple[str, float, float]],
        resistance: Optional[tuple[str, float, float]],
        filled_gap: bool,
    ) -> tuple[str, list[str]]:
        notes: list[str] = []
        significant = abs(gap_pct) >= self.config.significant_gap_pct
        near_support = support is not None and support[2] <= self.config.near_level_tolerance_pct
        near_resistance = resistance is not None and resistance[2] <= self.config.near_level_tolerance_pct

        if not significant:
            label = "flat_open"
        elif direction == "gap_up" and near_resistance:
            label = "gap_up_into_resistance"
            notes.append(f"open near resistance {resistance[0]}")
        elif direction == "gap_down" and near_support:
            label = "gap_down_into_support"
            notes.append(f"open near support {support[0]}")
        elif direction == "gap_up":
            label = "gap_up_clear"
        elif direction == "gap_down":
            label = "gap_down_clear"
        else:
            label = "flat_open"

        if filled_gap:
            notes.append("gap fully filled")
        return label, notes


def analyze_gap_context(
    daily_df: Optional[pd.DataFrame] = None,
    intraday_df: Optional[pd.DataFrame] = None,
    current_price: Optional[float] = None,
    config: Optional[GapContextConfig] = None,
) -> Optional[GapContext]:
    return GapContextAnalyzer(config=config).analyze(
        daily_df=daily_df,
        intraday_df=intraday_df,
        current_price=current_price,
    )
