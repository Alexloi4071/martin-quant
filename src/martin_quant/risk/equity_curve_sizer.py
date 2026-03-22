from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class EquityCurveSizerConfig:
    base_risk_pct: float = 0.5
    min_risk_pct: float = 0.1
    max_risk_pct: float = 1.0

    consecutive_loss_reduce: int = 3
    consecutive_loss_halt: int = 6
    reduce_factor: float = 0.5

    equity_ma_window: int = 10
    below_ma_reduce: bool = True
    below_ma_factor: float = 0.7

    recovery_consecutive_wins: int = 3
    market_bear_extra_reduce: float = 0.7
    bull_boost_pct: float = 0.0

    # Backward-compatible aliases expected by older tests/callers.
    reduced_risk_pct: Optional[float] = None
    consecutive_loss_threshold: Optional[int] = None
    equity_ma_period: Optional[int] = None

    def __post_init__(self) -> None:
        if self.consecutive_loss_threshold is not None:
            self.consecutive_loss_reduce = int(self.consecutive_loss_threshold)
        if self.equity_ma_period is not None:
            self.equity_ma_window = int(self.equity_ma_period)
        if self.reduced_risk_pct is not None:
            if self.base_risk_pct <= 0:
                self.reduce_factor = 0.0
            else:
                self.reduce_factor = max(0.0, float(self.reduced_risk_pct) / float(self.base_risk_pct))


@dataclass
class EquityCurveState:
    equity_history: list[float] = field(default_factory=list)
    pnl_history: list[float] = field(default_factory=list)
    r_history: list[float] = field(default_factory=list)
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    halted: bool = False
    current_risk_pct: float = 0.5

    def update(self, trade_pnl: float, trade_r: float, equity_after: float) -> None:
        self.equity_history.append(float(equity_after))
        self.pnl_history.append(float(trade_pnl))
        self.r_history.append(float(trade_r))

        if trade_r >= 0:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.halted = False
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0

    @property
    def equity_ma(self) -> Optional[float]:
        if len(self.equity_history) < 2:
            return None
        return float(np.mean(self.equity_history[-10:]))

    @property
    def current_equity(self) -> Optional[float]:
        return self.equity_history[-1] if self.equity_history else None

    def is_below_equity_ma(self) -> bool:
        if self.equity_ma is None or self.current_equity is None:
            return False
        return self.current_equity < self.equity_ma


class EquityCurveSizer:
    def __init__(
        self,
        initial_equity: Optional[float] = None,
        config: Optional[EquityCurveSizerConfig] = None,
    ) -> None:
        self.config = config or EquityCurveSizerConfig()
        self.state = EquityCurveState(current_risk_pct=self.config.base_risk_pct)
        if initial_equity is not None:
            self.state.equity_history.append(float(initial_equity))

    def _equity_ma(self) -> Optional[float]:
        history = self.state.equity_history
        if len(history) < 2:
            return None
        window = max(1, int(self.config.equity_ma_window))
        return float(np.mean(history[-window:]))

    def _is_below_equity_ma(self, equity: float) -> bool:
        equity_ma = self._equity_ma()
        if equity_ma is None:
            return False
        return float(equity) < equity_ma

    def get_risk_pct(self, equity: float, market_regime: str = "bull") -> float:
        cfg = self.config
        st = self.state

        if st.halted:
            return 0.0

        risk = cfg.base_risk_pct
        if market_regime == "bull" and cfg.bull_boost_pct:
            risk += cfg.bull_boost_pct

        if st.consecutive_losses >= cfg.consecutive_loss_halt:
            st.halted = True
            st.current_risk_pct = 0.0
            return 0.0
        if st.consecutive_losses >= cfg.consecutive_loss_reduce:
            if cfg.reduced_risk_pct is not None:
                risk = float(cfg.reduced_risk_pct)
            else:
                risk *= cfg.reduce_factor

        if cfg.below_ma_reduce and self._is_below_equity_ma(equity):
            risk *= cfg.below_ma_factor

        if market_regime == "bear":
            risk *= cfg.market_bear_extra_reduce

        if st.consecutive_wins >= cfg.recovery_consecutive_wins:
            risk = cfg.base_risk_pct + (cfg.bull_boost_pct if market_regime == "bull" else 0.0)

        risk = max(cfg.min_risk_pct, min(cfg.max_risk_pct, risk))
        st.current_risk_pct = risk
        return risk

    def record_trade(self, *args, **kwargs) -> None:
        if len(args) == 3 and not kwargs:
            pnl, r_multiple, equity_after = args
        elif {"pnl", "r_multiple", "equity_after"}.issubset(kwargs):
            pnl = kwargs["pnl"]
            r_multiple = kwargs["r_multiple"]
            equity_after = kwargs["equity_after"]
        elif (len(args) == 1 and "won" in kwargs) or len(args) == 2:
            equity_after = args[0]
            won = args[1] if len(args) == 2 else kwargs["won"]
            previous_equity = self.state.current_equity
            pnl = float(equity_after) - float(previous_equity) if previous_equity is not None else 0.0
            r_multiple = kwargs.get("r_multiple", 1.0 if won else -1.0)
        else:
            raise TypeError(
                "record_trade expects either (pnl, r_multiple, equity_after), "
                "keyword args pnl=/r_multiple=/equity_after=, or legacy "
                "(equity_after, won=...) usage."
            )

        self.state.update(
            trade_pnl=float(pnl),
            trade_r=float(r_multiple),
            equity_after=float(equity_after),
        )

    def summary(self) -> dict:
        st = self.state
        equity_ma = self._equity_ma()
        current_equity = st.current_equity
        return {
            "current_risk_pct": st.current_risk_pct,
            "consecutive_losses": st.consecutive_losses,
            "consecutive_wins": st.consecutive_wins,
            "halted": st.halted,
            "equity_ma": round(equity_ma, 2) if equity_ma is not None else None,
            "current_equity": current_equity,
            "below_equity_ma": self._is_below_equity_ma(current_equity) if current_equity is not None else False,
            "total_trades_recorded": len(st.r_history),
        }

    def reset(self) -> None:
        initial_equity = self.state.equity_history[0] if self.state.equity_history else None
        self.state = EquityCurveState(current_risk_pct=self.config.base_risk_pct)
        if initial_equity is not None:
            self.state.equity_history.append(float(initial_equity))
