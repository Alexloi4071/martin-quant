from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from martin_quant.core.datatypes import SetupSignal, TriggerSignal
from martin_quant.core.enums import SetupType, TriggerType
from martin_quant.features.ema import compute_ema


@dataclass
class ReclaimConfig:
    max_entry_bars: int = 6
    min_trigger_rvol: float = 1.5
    max_stop_distance_pct: float = 5.0
    require_close_above_ema9_15m: bool = True
    require_context_1h_ok: bool = True
    ema_reclaim_span: int = 9


class ReclaimTrigger:
    """
    Reclaim Trigger: fires when price reclaims a key EMA (default EMA9) on the
    15-minute timeframe, with relative volume confirmation.

    Logic:
    - In the last max_entry_bars bars, price dipped below the EMA then closed back above it.
    - The reclaim bar's volume >= min_trigger_rvol * 20-bar avg volume on that timeframe.
    - Optionally: 1h context bar's EMA9 is sloping up.
    - Stop: low of the reclaim bar.
    - Target: 3R from entry.
    """

    def __init__(self, config: ReclaimConfig | None = None) -> None:
        self.config = config or ReclaimConfig()

    def detect(
        self,
        symbol: str,
        df_15m: pd.DataFrame,
        df_1h: pd.DataFrame | None = None,
        linked_setup: SetupSignal | None = None,
    ) -> TriggerSignal | None:
        cfg = self.config
        min_bars = max(cfg.max_entry_bars + 1, 30)
        if len(df_15m) < min_bars:
            return None

        df = df_15m.copy().sort_values("timestamp").reset_index(drop=True)
        close  = df["close"]
        low    = df["low"]
        volume = df["volume"]

        ema = compute_ema(close, cfg.ema_reclaim_span)
        avg_vol_20 = float(volume.iloc[-21:-1].mean()) if len(volume) > 21 else float(volume.mean())

        reclaim_idx = None
        for i in range(len(df) - cfg.max_entry_bars - 1, len(df) - 1):
            prev_close = float(close.iloc[i])
            curr_close = float(close.iloc[i + 1])
            prev_ema   = float(ema.iloc[i])
            curr_ema   = float(ema.iloc[i + 1])
            curr_vol   = float(volume.iloc[i + 1])

            dipped  = prev_close < prev_ema
            reclaim = curr_close > curr_ema
            rvol    = curr_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0

            if dipped and reclaim and rvol >= cfg.min_trigger_rvol:
                reclaim_idx = i + 1
                break

        if reclaim_idx is None:
            return None

        entry_price = float(close.iloc[reclaim_idx])
        stop_price  = float(low.iloc[reclaim_idx])
        stop_dist_pct = (entry_price - stop_price) / entry_price * 100.0
        if stop_dist_pct > cfg.max_stop_distance_pct:
            return None

        if cfg.require_context_1h_ok and df_1h is not None and len(df_1h) >= 20:
            df1h = df_1h.copy().sort_values("timestamp").reset_index(drop=True)
            ema1h = compute_ema(df1h["close"], cfg.ema_reclaim_span)
            slope = float(ema1h.iloc[-1]) - float(ema1h.iloc[-4])
            if slope <= 0:
                return None

        risk   = entry_price - stop_price
        target = entry_price + risk * 3.0
        rvol_val = float(volume.iloc[reclaim_idx]) / avg_vol_20 if avg_vol_20 > 0 else 0.0

        context: dict[str, Any] = {
            "reclaim_bar_idx": int(reclaim_idx),
            "reclaim_timestamp": str(df["timestamp"].iloc[reclaim_idx]),
            "rvol": round(rvol_val, 2),
            "stop_dist_pct": round(stop_dist_pct, 2),
            f"ema{cfg.ema_reclaim_span}_at_reclaim": round(float(ema.iloc[reclaim_idx]), 4),
        }
        if linked_setup:
            context["linked_setup_type"] = linked_setup.setup_type.value
            context["linked_setup_score"] = linked_setup.score

        return TriggerSignal(
            symbol=symbol,
            timestamp=df["timestamp"].iloc[reclaim_idx],
            trigger_type=TriggerType.RECLAIM,
            timeframe="15m",
            direction="long",
            score=round(min(rvol_val / 3.0, 1.0), 3),
            entry_price=round(entry_price, 4),
            stop_price=round(stop_price, 4),
            target_price=round(target, 4),
            linked_setup_type=linked_setup.setup_type if linked_setup else None,
            context=context,
            notes=[f"EMA{cfg.ema_reclaim_span} reclaim on 15m, RVOL {rvol_val:.1f}x"],
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

