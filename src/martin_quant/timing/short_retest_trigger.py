from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from martin_quant.core.datatypes import SetupSignal, TriggerSignal
from martin_quant.core.enums import TriggerType
from martin_quant.entry.close_confirmation import CloseConfirmation
from martin_quant.features.ema import compute_ema
from martin_quant.features.gap_context import GapContext, analyze_gap_context


@dataclass
class ShortRetestBreakdownConfig:
    wait_for_minutes: int = 15
    max_entry_bars: int = 8
    ema_fast_span: int = 9
    ema_mid_span: int = 21
    min_trigger_rvol: float = 1.0
    max_stop_distance_pct: float = 5.0
    retest_tolerance_pct: float = 0.75
    gap_reference_tolerance_pct: float = 0.75
    stop_buffer_pct: float = 0.2
    r_target_multiple: float = 3.0
    hard_gap_down_pct: float = -2.0


class ShortRetestBreakdownTrigger:
    """Martin-style short trigger: wait, retest declining resistance, then break down."""

    SESSION_OPEN_UTC_HOUR = 14
    SESSION_OPEN_UTC_MINUTE = 30

    def __init__(
        self,
        config: ShortRetestBreakdownConfig | None = None,
        close_confirmation: Optional[CloseConfirmation] = None,
    ) -> None:
        self.config = config or ShortRetestBreakdownConfig()
        self._close_confirmation = close_confirmation or CloseConfirmation()

    def _benchmark_gap_pct(self, benchmark_df_15m: pd.DataFrame | None, trade_date) -> float | None:
        if benchmark_df_15m is None or benchmark_df_15m.empty:
            return None
        bench = benchmark_df_15m.copy().sort_values("timestamp").reset_index(drop=True)
        bench["timestamp"] = pd.to_datetime(bench["timestamp"], utc=True)
        today_bars = bench[bench["timestamp"].dt.date == trade_date]
        prev_bars = bench[bench["timestamp"].dt.date < trade_date]
        if today_bars.empty or prev_bars.empty:
            return None
        today_open = float(today_bars.iloc[0]["open"])
        prev_close = float(prev_bars.iloc[-1]["close"])
        if prev_close == 0:
            return None
        return (today_open / prev_close - 1.0) * 100.0

    def detect(
        self,
        symbol: str,
        df_15m: pd.DataFrame,
        benchmark_df_15m: pd.DataFrame | None = None,
        linked_setup: SetupSignal | None = None,
        resistance_avwap: float | None = None,
        daily_df: Optional[pd.DataFrame] = None,
    ) -> TriggerSignal | None:
        cfg = self.config
        min_bars = max(cfg.ema_mid_span + 2, 24)
        if df_15m is None or len(df_15m) < min_bars:
            return None

        df = df_15m.copy().sort_values("timestamp").reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df["ema_9"] = compute_ema(df["close"], cfg.ema_fast_span)
        df["ema_21"] = compute_ema(df["close"], cfg.ema_mid_span)

        last_date = df["timestamp"].iloc[-1].date()
        today_bars = df[df["timestamp"].dt.date == last_date].copy()
        if len(today_bars) < 3:
            return None

        session_open = pd.Timestamp(
            year=last_date.year,
            month=last_date.month,
            day=last_date.day,
            hour=self.SESSION_OPEN_UTC_HOUR,
            minute=self.SESSION_OPEN_UTC_MINUTE,
            tz="UTC",
        )
        wait_until = session_open + pd.Timedelta(minutes=cfg.wait_for_minutes)
        eligible = today_bars[today_bars["timestamp"] >= wait_until].head(cfg.max_entry_bars + 1)
        if len(eligible) < 2:
            return None

        volume_20bar = float(df["volume"].iloc[-21:-1].mean()) if len(df) > 21 else float(df["volume"].mean())
        benchmark_gap_pct = self._benchmark_gap_pct(benchmark_df_15m, last_date)
        gap_context: Optional[GapContext] = analyze_gap_context(daily_df=daily_df, intraday_df=df)

        prev_close = gap_context.prev_close if gap_context is not None else None
        if prev_close is None:
            prev_day_bars = df[df["timestamp"].dt.date < last_date]
            if not prev_day_bars.empty:
                prev_close = float(prev_day_bars.iloc[-1]["close"])

        for pos in range(len(eligible) - 1):
            retest = eligible.iloc[pos]
            retest_idx = int(retest.name)
            if retest_idx <= 0:
                continue

            ema9 = retest.get("ema_9")
            ema21 = retest.get("ema_21")
            prev_ema9 = df.iloc[retest_idx - 1].get("ema_9")
            prev_ema21 = df.iloc[retest_idx - 1].get("ema_21")
            if any(pd.isna([ema9, ema21, prev_ema9, prev_ema21])):
                continue
            if not (float(ema9) < float(prev_ema9) and float(ema21) <= float(prev_ema21)):
                continue

            retest_refs: list[str] = []
            high = float(retest["high"])
            if abs(high - float(ema9)) / float(ema9) * 100.0 <= cfg.retest_tolerance_pct:
                retest_refs.append("ema9")
            if abs(high - float(ema21)) / float(ema21) * 100.0 <= cfg.retest_tolerance_pct:
                retest_refs.append("ema21")
            if resistance_avwap is not None and resistance_avwap > 0:
                if abs(high - resistance_avwap) / resistance_avwap * 100.0 <= cfg.retest_tolerance_pct:
                    retest_refs.append("avwap")
            if prev_close is not None and prev_close > 0:
                if abs(high - prev_close) / prev_close * 100.0 <= cfg.gap_reference_tolerance_pct:
                    retest_refs.append("gap_fill")
            if gap_context is not None and gap_context.nearest_resistance_value is not None:
                resistance_value = float(gap_context.nearest_resistance_value)
                if abs(high - resistance_value) / resistance_value * 100.0 <= cfg.retest_tolerance_pct:
                    retest_refs.append(gap_context.nearest_resistance or "daily_resistance")
            if not retest_refs:
                continue

            retest_low = float(retest["low"])
            for trigger_pos in range(pos + 1, len(eligible)):
                trigger = eligible.iloc[trigger_pos]
                trigger_close = float(trigger["close"])
                trigger_open = float(trigger["open"])
                trigger_ema9 = trigger.get("ema_9")
                if pd.isna(trigger_ema9):
                    continue

                confirmation_window = eligible.iloc[pos + 1:trigger_pos + 1].copy()
                close_result = self._close_confirmation.confirm_entry_level(
                    df=confirmation_window,
                    trade_direction="short",
                    level=retest_low,
                    reference_label="retest_low_break",
                )
                if not close_result.confirmed:
                    continue

                rvol = float(trigger["volume"]) / volume_20bar if volume_20bar > 0 else 0.0
                if trigger_close >= retest_low:
                    continue
                if trigger_close >= float(trigger_ema9):
                    continue
                if trigger_close >= trigger_open:
                    continue
                if rvol < cfg.min_trigger_rvol:
                    continue

                stop_reference = max(float(retest["high"]), float(ema9), float(ema21))
                stop_price = stop_reference * (1 + cfg.stop_buffer_pct / 100.0)
                stop_dist_pct = (stop_price - trigger_close) / trigger_close * 100.0
                if stop_dist_pct <= 0 or stop_dist_pct > cfg.max_stop_distance_pct:
                    continue

                risk = stop_price - trigger_close
                target = trigger_close - risk * cfg.r_target_multiple
                score = min(1.0, 0.4 + min(rvol / 3.0, 0.4) + 0.1 * min(len(set(retest_refs)), 2))

                context: dict[str, Any] = {
                    "retest_timestamp": str(retest["timestamp"]),
                    "trigger_timestamp": str(trigger["timestamp"]),
                    "retest_references": sorted(set(retest_refs)),
                    "ema9_at_retest": round(float(ema9), 4),
                    "ema21_at_retest": round(float(ema21), 4),
                    "benchmark_gap_pct": round(float(benchmark_gap_pct), 3) if benchmark_gap_pct is not None else None,
                    "cover_if_close_above_ema9": True,
                    "stop_dist_pct": round(stop_dist_pct, 2),
                    "rvol": round(rvol, 2),
                    "entry_confirmation": close_result.to_dict(),
                }
                if gap_context is not None:
                    context["gap_context"] = gap_context.to_dict()
                if linked_setup:
                    context["linked_setup_type"] = linked_setup.setup_type.value
                    context["linked_setup_score"] = linked_setup.score

                notes = [
                    f"Waited past first {cfg.wait_for_minutes} minutes before shorting",
                    f"Retest into declining {'/'.join(sorted(set(retest_refs)))} then 15m breakdown",
                    f"Entry confirmation: {close_result.reason}",
                    "Cover if price closes back above EMA9",
                ]
                if gap_context is not None and gap_context.label != "flat_open":
                    notes.append(f"Symbol gap context: {gap_context.label}")
                if benchmark_gap_pct is not None and benchmark_gap_pct <= cfg.hard_gap_down_pct:
                    notes.append("Benchmark gapped down hard, so short only after bounce/retest")

                return TriggerSignal(
                    symbol=symbol,
                    timestamp=trigger["timestamp"],
                    trigger_type=TriggerType.SHORT_RETEST_BREAKDOWN,
                    timeframe="15m",
                    direction="short",
                    score=round(score, 3),
                    entry_price=round(trigger_close, 4),
                    stop_price=round(stop_price, 4),
                    target_price=round(target, 4),
                    linked_setup_type=linked_setup.setup_type if linked_setup else None,
                    context=context,
                    notes=notes,
                )

        return None

    def scan_universe(
        self,
        symbols: list[str],
        ohlcv_15m_map: dict[str, pd.DataFrame],
        benchmark_15m_map: dict[str, pd.DataFrame] | None = None,
        setup_map: dict[str, SetupSignal] | None = None,
        resistance_avwap_map: dict[str, float] | None = None,
        daily_map: dict[str, pd.DataFrame] | None = None,
    ) -> list[TriggerSignal]:
        results: list[TriggerSignal] = []
        benchmark_df = None
        if benchmark_15m_map:
            benchmark_df = benchmark_15m_map.get("QQQ")
            if benchmark_df is None:
                benchmark_df = benchmark_15m_map.get("SPY")

        for symbol in symbols:
            df_15m = ohlcv_15m_map.get(symbol.upper())
            if df_15m is None or df_15m.empty:
                continue
            linked = (setup_map or {}).get(symbol.upper())
            avwap = (resistance_avwap_map or {}).get(symbol.upper())
            daily_df = (daily_map or {}).get(symbol.upper())
            sig = self.detect(
                symbol=symbol,
                df_15m=df_15m,
                benchmark_df_15m=benchmark_df,
                linked_setup=linked,
                resistance_avwap=avwap,
                daily_df=daily_df,
            )
            if sig is not None:
                results.append(sig)
        return sorted(results, key=lambda item: item.score, reverse=True)
