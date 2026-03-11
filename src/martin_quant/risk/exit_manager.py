"""exit_manager.py

Martin Luk Exit Manager
=======================
完整的出場管理系統 — 對應 Martin 的出場規則:

  1. EMA9 兩根收盤破出   → 全出 (ema9_two_bar_confirm)
  2. EMA9 止損追蹤       → 部分出 or 全出
  3. 3R / 5R 部分止盈    → 50% 倉位
  4. 硬止損（跌破 stop） → 立即全出
  5. 時間止損（N 天無效） → 全出
  6. 市場制度轉 Bear     → 減倉 50%

Usage:
    from martin_quant.risk.exit_manager import ExitManager, Position, ExitSignal

    mgr = ExitManager()
    signal = mgr.evaluate(
        position=pos,
        df=ohlcv_df,
        current_price=125.5,
        regime="bull",
    )
    if signal.should_exit:
        print(signal.exit_type, signal.exit_pct)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Literal
import logging

import pandas as pd
import numpy as np

log = logging.getLogger(__name__)

# Exit types
ExitType = Literal[
    "hard_stop",
    "ema9_two_bar_confirm",
    "ema9_trail",
    "partial_3r",
    "partial_5r",
    "time_stop",
    "regime_change_bear",
    "manual",
    "none",
]


@dataclass
class Position:
    """持倉資訊"""
    symbol: str
    entry_price: float
    stop_price: float
    target_price: float          # 1st target (3R)
    shares: int
    entry_date: str              # 'YYYY-MM-DD'
    direction: str = "long"      # "long" | "short"
    partial_taken: bool = False  # 是否已做過部分止盈
    partial_taken_5r: bool = False
    current_shares: int = 0      # 追蹤剩餘份額（0 = 用 shares）

    def __post_init__(self):
        if self.current_shares == 0:
            self.current_shares = self.shares

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def r_multiple_target_3r(self) -> float:
        return self.entry_price + 3 * self.risk_per_share

    @property
    def r_multiple_target_5r(self) -> float:
        return self.entry_price + 5 * self.risk_per_share

    @property
    def days_held(self) -> int:
        try:
            from datetime import date
            entry = date.fromisoformat(self.entry_date)
            return (date.today() - entry).days
        except Exception:
            return 0


@dataclass
class ExitSignal:
    """出場信號結果"""
    symbol: str
    should_exit: bool
    exit_type: ExitType
    exit_pct: float              # 出場比例 0.5 = 50%，1.0 = 全出
    exit_price: float            # 建議出場價
    reason: str
    r_current: float             # 當前 R 倍數
    urgency: str                 # "immediate" | "next_open" | "eod"
    notes: str = ""

    @property
    def is_full_exit(self) -> bool:
        return self.exit_pct >= 1.0

    @property
    def is_partial_exit(self) -> bool:
        return 0 < self.exit_pct < 1.0


@dataclass
class ExitManagerConfig:
    """出場管理設定"""
    # EMA9 trail
    ema9_period: int = 9
    ema9_two_bar_bars: int = 2          # 連續幾根收盤破 EMA9 才全出

    # Partial take profit
    enable_partial_3r: bool = True
    partial_3r_pct: float = 0.5         # 到 3R 賣掉 50%
    enable_partial_5r: bool = True
    partial_5r_pct: float = 0.5         # 到 5R 再賣 50%（剩 25%）

    # Time stop
    enable_time_stop: bool = True
    time_stop_days: int = 15            # 15 天沒有達 1R → 出場
    time_stop_r_threshold: float = 1.0  # 低於這個 R 才觸發

    # Hard stop buffer
    hard_stop_buffer_pct: float = 0.1   # 0.1% 緩衝防止 whipsaw

    # Bear regime
    bear_regime_reduce_pct: float = 0.5


class ExitManager:
    """
    Martin Luk 出場管理器。

    每天收盤後對每個持倉調用 evaluate()，
    返回 ExitSignal 決定是否出場及出場比例。
    """

    def __init__(self, config: Optional[ExitManagerConfig] = None) -> None:
        self.cfg = config or ExitManagerConfig()

    def evaluate(
        self,
        position: Position,
        df: pd.DataFrame,           # 該股票 OHLCV，至少 20 根 K 線
        current_price: float,
        regime: str = "bull",       # "bull" | "caution" | "bear"
    ) -> ExitSignal:
        """
        評估持倉是否應出場。

        Returns ExitSignal — 若 should_exit=False 則繼續持有。
        """
        cfg = self.cfg
        sym = position.symbol
        direction = position.direction

        # 計算當前 R 倍數
        r_current = self._calc_r(position, current_price)

        # 確保有足夠數據
        if df is None or len(df) < 10:
            return self._no_exit(sym, current_price, r_current)

        df = self._normalize(df)
        ema9 = df["close"].ewm(span=cfg.ema9_period, adjust=False).mean()
        current_ema9 = float(ema9.iloc[-1])

        # ----------------------------------------------------------------
        # 1. Hard Stop
        # ----------------------------------------------------------------
        hard_stop_level = (
            position.stop_price * (1 - cfg.hard_stop_buffer_pct / 100)
            if direction == "long"
            else position.stop_price * (1 + cfg.hard_stop_buffer_pct / 100)
        )
        if direction == "long" and current_price <= hard_stop_level:
            return ExitSignal(
                symbol=sym, should_exit=True,
                exit_type="hard_stop", exit_pct=1.0,
                exit_price=current_price, urgency="immediate",
                reason=f"Price {current_price:.2f} <= hard stop {hard_stop_level:.2f}",
                r_current=round(r_current, 2),
            )
        if direction == "short" and current_price >= hard_stop_level:
            return ExitSignal(
                symbol=sym, should_exit=True,
                exit_type="hard_stop", exit_pct=1.0,
                exit_price=current_price, urgency="immediate",
                reason=f"Price {current_price:.2f} >= hard stop {hard_stop_level:.2f}",
                r_current=round(r_current, 2),
            )

        # ----------------------------------------------------------------
        # 2. EMA9 Two-Bar Confirm (Martin 最常用出場)
        # ----------------------------------------------------------------
        if len(df) >= cfg.ema9_two_bar_bars + 1:
            closes_below_ema9 = 0
            for i in range(1, cfg.ema9_two_bar_bars + 1):
                close_i = float(df["close"].iloc[-i])
                ema9_i  = float(ema9.iloc[-i])
                if direction == "long" and close_i < ema9_i:
                    closes_below_ema9 += 1
                elif direction == "short" and close_i > ema9_i:
                    closes_below_ema9 += 1

            if closes_below_ema9 >= cfg.ema9_two_bar_bars:
                return ExitSignal(
                    symbol=sym, should_exit=True,
                    exit_type="ema9_two_bar_confirm", exit_pct=1.0,
                    exit_price=current_price, urgency="next_open",
                    reason=f"{cfg.ema9_two_bar_bars} bars closed below EMA9({current_ema9:.2f})",
                    r_current=round(r_current, 2),
                )

        # ----------------------------------------------------------------
        # 3. Partial Take Profit — 3R
        # ----------------------------------------------------------------
        if cfg.enable_partial_3r and not position.partial_taken:
            if direction == "long" and current_price >= position.r_multiple_target_3r:
                return ExitSignal(
                    symbol=sym, should_exit=True,
                    exit_type="partial_3r", exit_pct=cfg.partial_3r_pct,
                    exit_price=current_price, urgency="eod",
                    reason=f"Reached 3R target {position.r_multiple_target_3r:.2f}",
                    r_current=round(r_current, 2),
                    notes="Sell 50%, move stop to breakeven",
                )

        # ----------------------------------------------------------------
        # 4. Partial Take Profit — 5R
        # ----------------------------------------------------------------
        if cfg.enable_partial_5r and position.partial_taken and not position.partial_taken_5r:
            if direction == "long" and current_price >= position.r_multiple_target_5r:
                return ExitSignal(
                    symbol=sym, should_exit=True,
                    exit_type="partial_5r", exit_pct=cfg.partial_5r_pct,
                    exit_price=current_price, urgency="eod",
                    reason=f"Reached 5R target {position.r_multiple_target_5r:.2f}",
                    r_current=round(r_current, 2),
                    notes="Sell another 50% of remaining, let runner go",
                )

        # ----------------------------------------------------------------
        # 5. Time Stop
        # ----------------------------------------------------------------
        if cfg.enable_time_stop:
            if (
                position.days_held >= cfg.time_stop_days
                and r_current < cfg.time_stop_r_threshold
            ):
                return ExitSignal(
                    symbol=sym, should_exit=True,
                    exit_type="time_stop", exit_pct=1.0,
                    exit_price=current_price, urgency="next_open",
                    reason=(
                        f"{position.days_held}d held, only {r_current:.1f}R "
                        f"(threshold: {cfg.time_stop_r_threshold}R)"
                    ),
                    r_current=round(r_current, 2),
                )

        # ----------------------------------------------------------------
        # 6. Bear Regime — Reduce
        # ----------------------------------------------------------------
        if regime == "bear" and direction == "long" and not position.partial_taken:
            return ExitSignal(
                symbol=sym, should_exit=True,
                exit_type="regime_change_bear", exit_pct=cfg.bear_regime_reduce_pct,
                exit_price=current_price, urgency="next_open",
                reason="Market regime changed to Bear — reducing long exposure",
                r_current=round(r_current, 2),
                notes=f"Reduce {int(cfg.bear_regime_reduce_pct*100)}% of position",
            )

        return self._no_exit(sym, current_price, r_current)

    def evaluate_portfolio(
        self,
        positions: list[Position],
        ohlcv_map: dict[str, pd.DataFrame],
        price_map: dict[str, float],
        regime: str = "bull",
    ) -> list[ExitSignal]:
        """對整個持倉組合批量評估出場信號"""
        signals = []
        for pos in positions:
            df = ohlcv_map.get(pos.symbol)
            price = price_map.get(pos.symbol, pos.entry_price)
            sig = self.evaluate(pos, df, price, regime)
            if sig.should_exit:
                signals.append(sig)
        return sorted(signals, key=lambda s: s.exit_pct, reverse=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_r(position: Position, current_price: float) -> float:
        rps = position.risk_per_share
        if rps <= 0:
            return 0.0
        if position.direction == "long":
            return (current_price - position.entry_price) / rps
        else:
            return (position.entry_price - current_price) / rps

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        return df

    @staticmethod
    def _no_exit(symbol: str, price: float, r: float) -> ExitSignal:
        return ExitSignal(
            symbol=symbol, should_exit=False,
            exit_type="none", exit_pct=0.0,
            exit_price=price, urgency="none",
            reason="Hold — no exit condition triggered",
            r_current=round(r, 2),
        )
