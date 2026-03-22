from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

import numpy as np

from martin_quant.entry.close_confirmation import CloseConfirmation


@dataclass
class ORBConfig:
    or_bars: int = 1
    min_rvol: float = 1.5
    max_or_range_pct: float = 4.0
    min_or_range_pct: float = 0.3
    close_above_or_pct: float = 0.1
    per_trade_risk_pct: float = 0.5
    r_target_multiple: float = 2.0
    lookback_rvol: int = 20
    min_daily_score: float = 0.5


@dataclass
class ORBSignal:
    symbol: str
    timeframe: str = "15m"
    trigger_bar_time: str = ""
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    or_high: float = 0.0
    or_low: float = 0.0
    or_range_pct: float = 0.0
    stop_pct: float = 0.0
    shares: int = 0
    risk_dollars: float = 0.0
    r_potential: float = 0.0
    rvol: float = 0.0
    trigger_reason: str = "orb_15m_breakout"
    confirmation_mode: str = "bar_close"
    confirmation_bars: int = 1
    confirmation_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "trigger_time": self.trigger_bar_time,
            "entry": round(self.entry_price, 4),
            "stop": round(self.stop_price, 4),
            "target": round(self.target_price, 4),
            "or_high": round(self.or_high, 4),
            "or_low": round(self.or_low, 4),
            "or_range_pct": round(self.or_range_pct, 2),
            "stop_pct": round(self.stop_pct, 2),
            "shares": self.shares,
            "risk_$": round(self.risk_dollars, 2),
            "r_potential": round(self.r_potential, 1),
            "rvol": round(self.rvol, 2),
            "trigger": self.trigger_reason,
            "confirmation_mode": self.confirmation_mode,
            "confirmation_bars": self.confirmation_bars,
            "confirmation_reason": self.confirmation_reason,
        }


class ORBTrigger:
    """15-minute opening range breakout trigger."""

    def __init__(
        self,
        equity: float = 100_000.0,
        config: Optional[ORBConfig] = None,
        close_confirmation: Optional[CloseConfirmation] = None,
    ) -> None:
        self.equity = equity
        self.config = config or ORBConfig()
        self._close_confirmation = close_confirmation or CloseConfirmation()

    @staticmethod
    def _opening_range_levels(or_df: pd.DataFrame) -> tuple[float, float, float]:
        or_high = float(or_df["high"].max())
        or_low = float(or_df["low"].min())
        if or_low >= or_high:
            stacked = pd.concat([or_df["open"], or_df["high"], or_df["low"], or_df["close"]], ignore_index=True)
            or_high = float(stacked.max())
            or_low = float(stacked.min())
        or_range_pct = (or_high - or_low) / or_low * 100.0 if or_low > 0 else 0.0
        return or_high, or_low, or_range_pct

    def check(
        self,
        symbol: str,
        df_15m: pd.DataFrame,
        daily_setup_score: float = 0.0,
    ) -> Optional[ORBSignal]:
        cfg = self.config
        if daily_setup_score < cfg.min_daily_score:
            return None
        if df_15m is None or len(df_15m) < cfg.or_bars + 2:
            return None

        df = df_15m.copy()
        df.columns = [c.lower() for c in df.columns]
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(set(df.columns)):
            return None

        or_df = df.iloc[:cfg.or_bars]
        or_high, or_low, or_range_pct = self._opening_range_levels(or_df)
        if or_range_pct > cfg.max_or_range_pct or or_range_pct < cfg.min_or_range_pct:
            return None

        post_or = df.iloc[cfg.or_bars:].reset_index(drop=False)
        avg_vol = float(df["volume"].iloc[: cfg.lookback_rvol].mean())
        if avg_vol <= 0:
            return None

        breakout_threshold = or_high * (1 + cfg.close_above_or_pct / 100.0)
        for pos, row in post_or.iterrows():
            bar_close = float(row["close"])
            bar_vol = float(row["volume"])
            rvol = bar_vol / avg_vol
            if bar_close < breakout_threshold or rvol < cfg.min_rvol:
                continue

            confirmation_window = post_or.iloc[: pos + 1].copy()
            close_result = self._close_confirmation.confirm_entry_level(
                df=confirmation_window,
                trade_direction="long",
                level=breakout_threshold,
                reference_label="OR_high_breakout",
            )
            if not close_result.confirmed:
                continue

            entry = bar_close
            stop = or_low
            stop_pct = (entry - stop) / entry * 100.0
            if stop_pct <= 0:
                continue

            stop_dist = entry - stop
            target = entry + stop_dist * cfg.r_target_multiple
            risk_dollars = self.equity * cfg.per_trade_risk_pct / 100.0
            shares = max(1, int(risk_dollars / stop_dist))
            r_potential = (target - entry) / stop_dist if stop_dist > 0 else 0.0
            bar_time = str(row.get("index", ""))

            return ORBSignal(
                symbol=symbol,
                timeframe="15m",
                trigger_bar_time=bar_time,
                entry_price=round(entry, 4),
                stop_price=round(stop, 4),
                target_price=round(target, 4),
                or_high=round(or_high, 4),
                or_low=round(or_low, 4),
                or_range_pct=round(or_range_pct, 2),
                stop_pct=round(stop_pct, 2),
                shares=shares,
                risk_dollars=round(risk_dollars, 2),
                r_potential=round(r_potential, 1),
                rvol=round(rvol, 2),
                trigger_reason="orb_15m_breakout_close_confirm",
                confirmation_mode="bar_close",
                confirmation_bars=close_result.required_bars,
                confirmation_reason=close_result.reason,
            )

        return None

    def get_or_levels(self, df_15m: pd.DataFrame) -> dict:
        if df_15m is None or len(df_15m) < self.config.or_bars:
            return {}
        df = df_15m.copy()
        df.columns = [c.lower() for c in df.columns]
        or_high, or_low, or_pct = self._opening_range_levels(df.iloc[: self.config.or_bars])
        return {
            "or_high": round(or_high, 4),
            "or_low": round(or_low, 4),
            "or_range_pct": round(or_pct, 2),
            "or_target": round(or_high + (or_high - or_low) * 2, 4),
            "or_stop": round(or_low, 4),
        }
