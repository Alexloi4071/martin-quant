from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from martin_quant.core.datatypes import SetupSignal, TriggerSignal
from martin_quant.core.enums import SetupType, TriggerType
from martin_quant.features.ema import compute_ema


@dataclass(slots=True)
class OrbConfig:
    orb_minutes: int = 30
    max_entry_bars: int = 6
    min_trigger_rvol: float = 1.5
    max_stop_distance_pct: float = 5.0
    require_close_above_orh: bool = True
    prefer_close_above_prior_day_high: bool = False
    require_context_1h_ok: bool = True
    ema_context_span: int = 9


class OpeningRangeTrigger:
    """
    Opening Range Breakout (ORB) Trigger.

    Logic:
    - Opening range = high/low of the first orb_minutes bars of the session.
    - Trigger fires when a subsequent bar closes above the ORH with volume >= min_trigger_rvol.
    - Stop: low of the breakout bar or ORB low, whichever is higher.
    - Target: 3R from entry.

    Assumes df_15m timestamps are UTC and a session starts at 14:30 UTC (09:30 ET).
    """

    SESSION_OPEN_UTC_HOUR = 14
    SESSION_OPEN_UTC_MINUTE = 30

    def __init__(self, config: OrbConfig | None = None) -> None:
        self.config = config or OrbConfig()

    def detect(
        self,
        symbol: str,
        df_15m: pd.DataFrame,
        df_1h: pd.DataFrame | None = None,
        linked_setup: SetupSignal | None = None,
    ) -> TriggerSignal | None:
        cfg = self.config

        df = df_15m.copy().sort_values("timestamp").reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

        last_date = df["timestamp"].iloc[-1].date()
        today_bars = df[df["timestamp"].dt.date == last_date].copy()
        if today_bars.empty:
            return None

        session_open = pd.Timestamp(
            year=last_date.year, month=last_date.month, day=last_date.day,
            hour=self.SESSION_OPEN_UTC_HOUR, minute=self.SESSION_OPEN_UTC_MINUTE,
            tz="UTC",
        )
        orb_end = session_open + pd.Timedelta(minutes=cfg.orb_minutes)

        orb_bars = today_bars[today_bars["timestamp"] < orb_end]
        if orb_bars.empty or len(orb_bars) < 1:
            return None

        orb_high = float(orb_bars["high"].max())
        orb_low  = float(orb_bars["low"].min())

        post_orb = today_bars[today_bars["timestamp"] >= orb_end].head(cfg.max_entry_bars)
        if post_orb.empty:
            return None

        volume_20bar = float(df["volume"].iloc[-21:-1].mean()) if len(df) > 21 else float(df["volume"].mean())

        breakout_row = None
        for _, row in post_orb.iterrows():
            curr_close = float(row["close"])
            curr_vol   = float(row["volume"])
            rvol = curr_vol / volume_20bar if volume_20bar > 0 else 0.0
            if curr_close > orb_high and rvol >= cfg.min_trigger_rvol:
                breakout_row = row
                break

        if breakout_row is None:
            return None

        entry_price = float(breakout_row["close"])
        stop_price  = max(float(breakout_row["low"]), orb_low)
        stop_dist_pct = (entry_price - stop_price) / entry_price * 100.0
        if stop_dist_pct > cfg.max_stop_distance_pct:
            return None

        if cfg.require_context_1h_ok and df_1h is not None and len(df_1h) >= 20:
            df1h = df_1h.copy().sort_values("timestamp").reset_index(drop=True)
            ema1h = compute_ema(df1h["close"], cfg.ema_context_span)
            slope = float(ema1h.iloc[-1]) - float(ema1h.iloc[-4])
            if slope <= 0:
                return None

        rvol_val = float(breakout_row["volume"]) / volume_20bar if volume_20bar > 0 else 0.0
        risk   = entry_price - stop_price
        target = entry_price + risk * 3.0

        context: dict[str, Any] = {
            "orb_high": round(orb_high, 4),
            "orb_low": round(orb_low, 4),
            "breakout_timestamp": str(breakout_row["timestamp"]),
            "rvol": round(rvol_val, 2),
            "stop_dist_pct": round(stop_dist_pct, 2),
        }
        if linked_setup:
            context["linked_setup_type"] = linked_setup.setup_type.value
            context["linked_setup_score"] = linked_setup.score

        return TriggerSignal(
            symbol=symbol,
            timestamp=breakout_row["timestamp"],
            trigger_type=TriggerType.OPENING_RANGE_BREAKOUT,
            timeframe="15m",
            direction="long",
            score=round(min(rvol_val / 3.0, 1.0), 3),
            entry_price=round(entry_price, 4),
            stop_price=round(stop_price, 4),
            target_price=round(target, 4),
            linked_setup_type=linked_setup.setup_type if linked_setup else None,
            context=context,
            notes=[f"ORB breakout above {orb_high:.2f}, RVOL {rvol_val:.1f}x"],
        )

    def scan_universe(
        self,
        symbols: list[str],
        ohlcv_15m_map: dict[str, pd.DataFrame],
        ohlcv_1h_map: dict[str, pd.DataFrame] | None = None,
        setup_map: dict[str, SetupSignal] | None = None,
    ) -> list[TriggerSignal]:
        results = []
        for symbol in symbols:
            df_15m = ohlcv_15m_map.get(symbol.upper())
            if df_15m is None or df_15m.empty:
                continue
            df_1h = (ohlcv_1h_map or {}).get(symbol.upper())
            linked = (setup_map or {}).get(symbol.upper())
            sig = self.detect(symbol=symbol, df_15m=df_15m, df_1h=df_1h, linked_setup=linked)
            if sig is not None:
                results.append(sig)
        return sorted(results, key=lambda s: s.score, reverse=True)
