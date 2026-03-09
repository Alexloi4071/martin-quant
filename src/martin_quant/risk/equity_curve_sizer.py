"""equity_curve_sizer.py

動態倉位管理 — Equity Curve Feedback

Martin 影片 1:55:03 核心規則:
  - 連續虧損 N 次 → 自動縮小 per_trade_risk_pct
  - Equity curve 低於 N 日均線 → 防禦模式
  - 恢復盈利 → 逐步恢復正常倉位
  - 大盤弱 + 自己虧損 → 立即縮倉 (double penalty)

設計:
  EquityCurveSizer 包裝 PositionSizer，
  根據 trade history 自動調整 per_trade_risk_pct。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class EquityCurveSizerConfig:
    # Base risk
    base_risk_pct: float = 0.5         # 正常狀態每筆風險佔總資產 %
    min_risk_pct: float = 0.1          # 最低風險下限 (防禦模式)
    max_risk_pct: float = 1.0          # 最高風險上限 (順風加倉)

    # Drawdown triggers
    consecutive_loss_reduce: int = 3   # 連虧 N 筆觸發縮倉
    consecutive_loss_halt:   int = 6   # 連虧 N 筆觸發暫停新倉
    reduce_factor: float = 0.5         # 縮倉倍數 (e.g. 0.5% → 0.25%)

    # Equity curve MA
    equity_ma_window: int = 10         # 過去 N 筆交易 equity 的均線
    below_ma_reduce: bool = True       # equity 低於均線時縮倉
    below_ma_factor: float = 0.7       # 低於均線時倍數

    # Recovery
    recovery_consecutive_wins: int = 3 # 連贏 N 筆才恢復正常倉位

    # Market regime penalty
    market_bear_extra_reduce: float = 0.7  # 大盤熊市時再乘以此因子


# ---------------------------------------------------------------------------
# State tracker
# ---------------------------------------------------------------------------

@dataclass
class EquityCurveState:
    equity_history: list[float] = field(default_factory=list)
    pnl_history:    list[float] = field(default_factory=list)   # per-trade PnL
    r_history:      list[float] = field(default_factory=list)   # per-trade R multiple
    consecutive_losses: int = 0
    consecutive_wins:   int = 0
    halted: bool = False
    current_risk_pct: float = 0.5

    def update(self, trade_pnl: float, trade_r: float, equity_after: float) -> None:
        self.equity_history.append(equity_after)
        self.pnl_history.append(trade_pnl)
        self.r_history.append(trade_r)

        if trade_r >= 0:
            self.consecutive_wins  += 1
            self.consecutive_losses = 0
            self.halted = False
        else:
            self.consecutive_losses += 1
            self.consecutive_wins   = 0

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


# ---------------------------------------------------------------------------
# Main sizer
# ---------------------------------------------------------------------------

class EquityCurveSizer:
    """
    Wraps a base risk_pct and dynamically adjusts it based on recent
    trade history and equity curve performance.

    Usage:
        sizer = EquityCurveSizer()
        risk_pct = sizer.get_risk_pct(equity=100_000, market_regime="bull")
        # ... after trade closes:
        sizer.record_trade(pnl=500, r_multiple=2.5, equity_after=100_500)
    """

    def __init__(self, config: Optional[EquityCurveSizerConfig] = None) -> None:
        self.config = config or EquityCurveSizerConfig()
        self.state  = EquityCurveState(
            current_risk_pct=self.config.base_risk_pct
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_risk_pct(
        self,
        equity: float,
        market_regime: str = "bull",  # "bull" | "neutral" | "bear"
    ) -> float:
        """
        Return the current per-trade risk % to use for position sizing.

        Parameters
        ----------
        equity : float
            Current portfolio equity.
        market_regime : str
            Market condition from MarketRegimeFilter.

        Returns
        -------
        float
            Adjusted risk_pct, clamped to [min_risk_pct, max_risk_pct].
        """
        cfg = self.config
        st  = self.state

        if st.halted:
            return 0.0

        risk = cfg.base_risk_pct

        # 1. Consecutive loss penalty
        if st.consecutive_losses >= cfg.consecutive_loss_halt:
            st.halted = True
            return 0.0
        elif st.consecutive_losses >= cfg.consecutive_loss_reduce:
            risk *= cfg.reduce_factor

        # 2. Equity below MA penalty
        if cfg.below_ma_reduce and st.is_below_equity_ma():
            risk *= cfg.below_ma_factor

        # 3. Market regime penalty
        if market_regime == "bear":
            risk *= cfg.market_bear_extra_reduce

        # 4. Recovery bonus (restore after winning streak)
        if st.consecutive_wins >= cfg.recovery_consecutive_wins:
            risk = cfg.base_risk_pct  # fully restored

        # Clamp
        risk = max(cfg.min_risk_pct, min(cfg.max_risk_pct, risk))
        st.current_risk_pct = risk
        return risk

    def record_trade(
        self,
        pnl: float,
        r_multiple: float,
        equity_after: float,
    ) -> None:
        """
        Call after each trade closes to update the internal state.

        Parameters
        ----------
        pnl : float
            Realised PnL in dollar terms.
        r_multiple : float
            Trade result in R (positive = win, negative = loss).
        equity_after : float
            Portfolio equity after the trade.
        """
        self.state.update(
            trade_pnl=pnl,
            trade_r=r_multiple,
            equity_after=equity_after,
        )

    def summary(self) -> dict:
        st  = self.state
        cfg = self.config
        return {
            "current_risk_pct":      st.current_risk_pct,
            "consecutive_losses":    st.consecutive_losses,
            "consecutive_wins":      st.consecutive_wins,
            "halted":                st.halted,
            "equity_ma":             round(st.equity_ma, 2) if st.equity_ma else None,
            "current_equity":        st.current_equity,
            "below_equity_ma":       st.is_below_equity_ma(),
            "total_trades_recorded": len(st.r_history),
        }

    def reset(self) -> None:
        """Full reset — use at start of new trading period."""
        self.state = EquityCurveState(
            current_risk_pct=self.config.base_risk_pct
        )
