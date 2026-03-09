from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from martin_quant.backtest.trade_simulator import SimulatedTrade


@dataclass
class PerformanceMetrics:
    total_trades: int
    win_rate: float
    avg_r: float
    avg_win_r: float
    avg_loss_r: float
    profit_factor: float
    total_pnl: float
    max_drawdown_pct: float
    avg_holding_days: float
    expectancy_r: float
    sharpe_ratio: float
    trades_by_exit: dict[str, int]
    trades_by_setup: dict[str, int]
    trades_by_trigger: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "avg_win_r": round(self.avg_win_r, 3),
            "avg_loss_r": round(self.avg_loss_r, 3),
            "profit_factor": round(self.profit_factor, 3),
            "total_pnl": round(self.total_pnl, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "avg_holding_days": round(self.avg_holding_days, 1),
            "expectancy_r": round(self.expectancy_r, 3),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "trades_by_exit": self.trades_by_exit,
            "trades_by_setup": self.trades_by_setup,
            "trades_by_trigger": self.trades_by_trigger,
        }


def compute_performance(trades: list[SimulatedTrade]) -> PerformanceMetrics:
    if not trades:
        return PerformanceMetrics(
            total_trades=0, win_rate=0.0, avg_r=0.0, avg_win_r=0.0,
            avg_loss_r=0.0, profit_factor=0.0, total_pnl=0.0,
            max_drawdown_pct=0.0, avg_holding_days=0.0, expectancy_r=0.0,
            sharpe_ratio=0.0, trades_by_exit={}, trades_by_setup={},
            trades_by_trigger={},
        )

    df = pd.DataFrame([t.to_dict() for t in trades])

    r_values   = df["r_multiple"].tolist()
    pnl_values = df["pnl"].tolist()

    wins   = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r <= 0]

    win_rate    = len(wins) / len(r_values) if r_values else 0.0
    avg_r       = float(pd.Series(r_values).mean()) if r_values else 0.0
    avg_win_r   = float(pd.Series(wins).mean())   if wins   else 0.0
    avg_loss_r  = float(pd.Series(losses).mean()) if losses else 0.0

    gross_profit = sum(r for r in r_values if r > 0)
    gross_loss   = abs(sum(r for r in r_values if r < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    total_pnl = sum(pnl_values)

    cum_pnl   = pd.Series(pnl_values).cumsum()
    roll_max  = cum_pnl.cummax()
    drawdown  = (cum_pnl - roll_max)
    max_dd_pct = float(drawdown.min() / (roll_max.max() + 1e-9) * 100.0) if not roll_max.empty else 0.0

    avg_holding = float(df["holding_days"].mean()) if "holding_days" in df.columns else 0.0
    expectancy  = win_rate * avg_win_r + (1 - win_rate) * avg_loss_r

    r_series  = pd.Series(r_values)
    sharpe    = float(r_series.mean() / r_series.std()) if r_series.std() > 0 else 0.0

    def counts(col: str) -> dict[str, int]:
        if col not in df.columns:
            return {}
        return df[col].value_counts().to_dict()

    return PerformanceMetrics(
        total_trades=len(trades),
        win_rate=win_rate,
        avg_r=avg_r,
        avg_win_r=avg_win_r,
        avg_loss_r=avg_loss_r,
        profit_factor=profit_factor,
        total_pnl=total_pnl,
        max_drawdown_pct=max_dd_pct,
        avg_holding_days=avg_holding,
        expectancy_r=expectancy,
        sharpe_ratio=sharpe,
        trades_by_exit=counts("exit_reason"),
        trades_by_setup=counts("setup_type"),
        trades_by_trigger=counts("trigger_type"),
    )
