from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from martin_quant.core.datatypes import TriggerSignal
from martin_quant.features.ema import compute_ema


@dataclass(**({"slots": True} if sys.version_info >= (3, 10) else {}))
class TradeSimulatorConfig:
    max_holding_days: int = 20
    trail_mode: str = "ema9"   # ema9 | none
    slippage_pct: float = 0.05
    commission_per_share: float = 0.005
    r_multiple_target: float = 3.0


@dataclass
class SimulatedTrade:
    symbol: str
    entry_date: Any
    exit_date: Any
    entry_price: float
    exit_price: float
    stop_price: float
    shares: int
    pnl: float
    pnl_pct: float
    r_multiple: float
    exit_reason: str
    holding_days: int
    trigger_type: str = ""
    setup_type: str = ""
    partial_exits: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "entry_date": str(self.entry_date),
            "exit_date": str(self.exit_date),
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4),
            "stop_price": round(self.stop_price, 4),
            "shares": self.shares,
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "r_multiple": round(self.r_multiple, 3),
            "exit_reason": self.exit_reason,
            "holding_days": self.holding_days,
            "trigger_type": self.trigger_type,
            "setup_type": self.setup_type,
        }


class TradeSimulator:
    """
    Bar-by-bar daily OHLCV trade simulator.

    For each TriggerSignal:
    - Enters at entry_price (with slippage) on the next daily open.
    - Manages stop (trailing EMA9 if trail_mode == 'ema9').
    - Exits on:
        * Stop hit (low <= stop)
        * Target hit (high >= target)
        * Max holding days elapsed
        * EMA9 trail stop hit
    """

    def __init__(self, config: TradeSimulatorConfig | None = None) -> None:
        self.config = config or TradeSimulatorConfig()

    def simulate(
        self,
        signal: TriggerSignal,
        df_daily: pd.DataFrame,
        shares: int = 100,
    ) -> SimulatedTrade | None:
        cfg = self.config
        df = df_daily.copy().sort_values("timestamp").reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

        signal_ts = pd.to_datetime(signal.timestamp, utc=True)
        entry_rows = df[df["timestamp"] > signal_ts]
        if entry_rows.empty:
            return None

        entry_idx = int(entry_rows.index[0])
        entry_price_raw = float(df.loc[entry_idx, "open"])
        entry_price = entry_price_raw * (1 + cfg.slippage_pct / 100.0)

        stop_price  = signal.stop_price  if signal.stop_price  else entry_price * 0.95
        target_price = signal.target_price if signal.target_price else entry_price * (1 + (entry_price - stop_price) / entry_price * cfg.r_multiple_target)

        risk_per_share = entry_price - stop_price
        if risk_per_share <= 0:
            return None

        ema9 = compute_ema(df["close"], 9)
        current_stop = stop_price
        exit_price   = None
        exit_reason  = "max_holding"
        exit_idx     = min(entry_idx + cfg.max_holding_days, len(df) - 1)

        for i in range(entry_idx + 1, min(entry_idx + cfg.max_holding_days + 1, len(df))):
            bar_low  = float(df.loc[i, "low"])
            bar_high = float(df.loc[i, "high"])
            bar_close = float(df.loc[i, "close"])

            if cfg.trail_mode == "ema9" and not pd.isna(ema9.iloc[i]):
                ema_val = float(ema9.iloc[i])
                if ema_val > current_stop:
                    current_stop = ema_val

            if bar_low <= current_stop:
                exit_price  = current_stop
                exit_reason = "stop"
                exit_idx    = i
                break

            if bar_high >= target_price:
                exit_price  = target_price
                exit_reason = "target"
                exit_idx    = i
                break

        if exit_price is None:
            exit_price  = float(df.loc[exit_idx, "close"])
            exit_reason = "max_holding"

        exit_price_net = exit_price * (1 - cfg.slippage_pct / 100.0)
        commission = shares * cfg.commission_per_share * 2
        pnl = shares * (exit_price_net - entry_price) - commission
        pnl_pct = (exit_price_net - entry_price) / entry_price * 100.0
        r_multiple = (exit_price_net - entry_price) / risk_per_share if risk_per_share else 0.0
        holding_days = int(exit_idx - entry_idx)

        return SimulatedTrade(
            symbol=signal.symbol,
            entry_date=df.loc[entry_idx, "timestamp"],
            exit_date=df.loc[exit_idx, "timestamp"],
            entry_price=round(entry_price, 4),
            exit_price=round(exit_price_net, 4),
            stop_price=round(stop_price, 4),
            shares=shares,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            r_multiple=round(r_multiple, 3),
            exit_reason=exit_reason,
            holding_days=holding_days,
            trigger_type=signal.trigger_type.value,
            setup_type=signal.linked_setup_type.value if signal.linked_setup_type else "",
        )

    def run_backtest(
        self,
        signals: list[TriggerSignal],
        ohlcv_daily_map: dict[str, pd.DataFrame],
        shares_per_trade: int = 100,
    ) -> list[SimulatedTrade]:
        trades: list[SimulatedTrade] = []
        for sig in signals:
            df = ohlcv_daily_map.get(sig.symbol.upper())
            if df is None or df.empty:
                continue
            trade = self.simulate(signal=sig, df_daily=df, shares=shares_per_trade)
            if trade is not None:
                trades.append(trade)
        return trades
