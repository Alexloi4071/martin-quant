from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from martin_quant.core.datatypes import SetupSignal, TriggerSignal
from martin_quant.core.enums import SetupType, TriggerType
from martin_quant.features.ema import compute_ema


@dataclass
class AvwapReclaimConfig:
    max_entry_bars: int = 12
    min_trigger_rvol: float = 1.2
    max_stop_distance_pct: float = 5.0
    avwap_touch_tolerance_pct: float = 0.5
    max_avwap_extension_pct: float = 3.0
    require_context_1h_ok: bool = True
    ema_context_span: int = 9


class AvwapReclaimTrigger:
    """
    Anchored VWAP Reclaim Trigger.

    Logic:
    - Compute AVWAP anchored to the most recent significant swing low in df_15m.
    - Trigger fires when price pulls back to AVWAP (within avwap_touch_tolerance_pct)
      and closes above it with RVOL >= min_trigger_rvol.
    - Price must not be extended more than max_avwap_extension_pct above AVWAP at close.
    - Stop: low of the reclaim bar.
    - Target: 3R from entry.
    """

    def __init__(self, config: AvwapReclaimConfig | None = None) -> None:
        self.config = config or AvwapReclaimConfig()

    @staticmethod
    def _compute_avwap(
        df: pd.DataFrame,
        anchor_idx: int,
        high_col: str = "high",
        low_col: str = "low",
        close_col: str = "close",
        volume_col: str = "volume",
    ) -> pd.Series:
        sub = df.iloc[anchor_idx:].copy()
        typical = (sub[high_col] + sub[low_col] + sub[close_col]) / 3.0
        pv = typical * sub[volume_col]
        cum_pv = pv.cumsum()
        cum_vol = sub[volume_col].cumsum()
        avwap = cum_pv / cum_vol
        return avwap.reindex(df.index)

    @staticmethod
    def _find_anchor_idx(df: pd.DataFrame, lookback: int = 60) -> int:
        window = df.tail(lookback)
        min_loc = window["low"].idxmin()
        return int(df.index.get_loc(min_loc))

    def detect(
        self,
        symbol: str,
        df_15m: pd.DataFrame,
        df_1h: pd.DataFrame | None = None,
        linked_setup: SetupSignal | None = None,
    ) -> TriggerSignal | None:
        cfg = self.config
        min_bars = cfg.max_entry_bars + 65
        if len(df_15m) < min_bars:
            return None

        df = df_15m.copy().sort_values("timestamp").reset_index(drop=True)
        close  = df["close"]
        low    = df["low"]
        volume = df["volume"]

        anchor_idx = self._find_anchor_idx(df, lookback=60)
        avwap = self._compute_avwap(df, anchor_idx=anchor_idx)

        avg_vol_20 = float(volume.iloc[-21:-1].mean()) if len(volume) > 21 else float(volume.mean())

        reclaim_idx = None
        for i in range(len(df) - cfg.max_entry_bars - 1, len(df) - 1):
            if pd.isna(avwap.iloc[i]) or pd.isna(avwap.iloc[i + 1]):
                continue

            prev_close = float(close.iloc[i])
            curr_close = float(close.iloc[i + 1])
            curr_avwap = float(avwap.iloc[i + 1])
            curr_vol   = float(volume.iloc[i + 1])

            near_avwap = abs(prev_close - curr_avwap) / curr_avwap * 100.0 <= cfg.avwap_touch_tolerance_pct
            reclaim    = curr_close > curr_avwap
            extension  = (curr_close - curr_avwap) / curr_avwap * 100.0
            rvol       = curr_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0

            if near_avwap and reclaim and extension <= cfg.max_avwap_extension_pct and rvol >= cfg.min_trigger_rvol:
                reclaim_idx = i + 1
                break

        if reclaim_idx is None:
            return None

        entry_price  = float(close.iloc[reclaim_idx])
        stop_price   = float(low.iloc[reclaim_idx])
        avwap_at_bar = float(avwap.iloc[reclaim_idx])

        stop_dist_pct = (entry_price - stop_price) / entry_price * 100.0
        if stop_dist_pct > cfg.max_stop_distance_pct:
            return None

        if cfg.require_context_1h_ok and df_1h is not None and len(df_1h) >= 20:
            df1h = df_1h.copy().sort_values("timestamp").reset_index(drop=True)
            ema1h = compute_ema(df1h["close"], cfg.ema_context_span)
            slope = float(ema1h.iloc[-1]) - float(ema1h.iloc[-4])
            if slope <= 0:
                return None

        rvol_val = float(volume.iloc[reclaim_idx]) / avg_vol_20 if avg_vol_20 > 0 else 0.0
        risk   = entry_price - stop_price
        target = entry_price + risk * 3.0
        extension_pct = (entry_price - avwap_at_bar) / avwap_at_bar * 100.0

        context: dict[str, Any] = {
            "avwap_at_reclaim": round(avwap_at_bar, 4),
            "avwap_anchor_idx": int(anchor_idx),
            "avwap_anchor_timestamp": str(df["timestamp"].iloc[anchor_idx]),
            "extension_pct": round(extension_pct, 2),
            "rvol": round(rvol_val, 2),
            "stop_dist_pct": round(stop_dist_pct, 2),
        }
        if linked_setup:
            context["linked_setup_type"] = linked_setup.setup_type.value
            context["linked_setup_score"] = linked_setup.score

        return TriggerSignal(
            symbol=symbol,
            timestamp=df["timestamp"].iloc[reclaim_idx],
            trigger_type=TriggerType.AVWAP_RECLAIM,
            timeframe="15m",
            direction="long",
            score=round(min(rvol_val / 3.0, 1.0), 3),
            entry_price=round(entry_price, 4),
            stop_price=round(stop_price, 4),
            target_price=round(target, 4),
            linked_setup_type=linked_setup.setup_type if linked_setup else None,
            context=context,
            notes=[f"AVWAP reclaim at {avwap_at_bar:.2f}, extension {extension_pct:.1f}%, RVOL {rvol_val:.1f}x"],
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

