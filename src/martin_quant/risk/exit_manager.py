from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from martin_quant.entry.close_confirmation import CloseConfirmation, CloseConfirmationConfig


ExitType = Literal[
    "hard_stop",
    "ema9_close_confirm",
    "partial_3r",
    "partial_5r",
    "time_stop",
    "regime_change_bear",
    "manual",
    "none",
]


@dataclass
class Position:
    symbol: str
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    entry_date: str
    direction: str = "long"
    partial_taken: bool = False
    partial_taken_5r: bool = False
    current_shares: int = 0
    entry_confirmation: dict[str, object] | None = None
    exit_confirmation: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.current_shares == 0:
            self.current_shares = self.shares

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def r_multiple_target_3r(self) -> float:
        if self.direction == "short":
            return self.entry_price - 3 * self.risk_per_share
        return self.entry_price + 3 * self.risk_per_share

    @property
    def r_multiple_target_5r(self) -> float:
        if self.direction == "short":
            return self.entry_price - 5 * self.risk_per_share
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
    symbol: str
    should_exit: bool
    exit_type: ExitType
    exit_pct: float
    exit_price: float
    reason: str
    r_current: float
    urgency: str
    notes: str = ""
    confirmation: dict[str, object] | None = None

    @property
    def is_full_exit(self) -> bool:
        return self.exit_pct >= 1.0

    @property
    def is_partial_exit(self) -> bool:
        return 0 < self.exit_pct < 1.0


@dataclass
class ExitManagerConfig:
    ema9_period: int = 9
    ema9_confirm_bars_long: int = 2
    ema9_confirm_bars_short: int = 1
    enable_partial_3r: bool = True
    partial_3r_pct: float = 0.5
    enable_partial_5r: bool = True
    partial_5r_pct: float = 0.5
    enable_time_stop: bool = True
    time_stop_days: int = 15
    time_stop_r_threshold: float = 1.0
    hard_stop_buffer_pct: float = 0.1
    bear_regime_reduce_pct: float = 0.5


class ExitManager:
    def __init__(self, config: Optional[ExitManagerConfig] = None) -> None:
        self.cfg = config or ExitManagerConfig()
        self._close_confirmation = CloseConfirmation(
            CloseConfirmationConfig(
                ema_span=self.cfg.ema9_period,
                required_bars_long=self.cfg.ema9_confirm_bars_long,
                required_bars_short=self.cfg.ema9_confirm_bars_short,
            )
        )

    def evaluate(
        self,
        position: Position,
        df: pd.DataFrame,
        current_price: float,
        regime: str = "bull",
    ) -> ExitSignal:
        symbol = position.symbol
        direction = position.direction.lower().strip()
        r_current = self._calc_r(position, current_price)

        if df is None or len(df) < max(10, self.cfg.ema9_period):
            return self._no_exit(symbol, current_price, r_current)

        df = self._normalize(df)

        hard_stop_level = (
            position.stop_price * (1 - self.cfg.hard_stop_buffer_pct / 100.0)
            if direction == "long"
            else position.stop_price * (1 + self.cfg.hard_stop_buffer_pct / 100.0)
        )
        if direction == "long" and current_price <= hard_stop_level:
            return ExitSignal(
                symbol=symbol,
                should_exit=True,
                exit_type="hard_stop",
                exit_pct=1.0,
                exit_price=current_price,
                reason=f"Price {current_price:.2f} <= hard stop {hard_stop_level:.2f}",
                r_current=round(r_current, 2),
                urgency="immediate",
            )
        if direction == "short" and current_price >= hard_stop_level:
            return ExitSignal(
                symbol=symbol,
                should_exit=True,
                exit_type="hard_stop",
                exit_pct=1.0,
                exit_price=current_price,
                reason=f"Price {current_price:.2f} >= hard stop {hard_stop_level:.2f}",
                r_current=round(r_current, 2),
                urgency="immediate",
            )

        close_result = self._close_confirmation.confirm_trade_failure(
            df=df,
            trade_direction="short" if direction == "short" else "long",
            span=self.cfg.ema9_period,
        )
        if close_result.confirmed:
            relation_text = "above" if direction == "short" else "below"
            return ExitSignal(
                symbol=symbol,
                should_exit=True,
                exit_type="ema9_close_confirm",
                exit_pct=1.0,
                exit_price=current_price,
                reason=(
                    f"{close_result.confirmed_bars} close(s) {relation_text} "
                    f"EMA{self.cfg.ema9_period} confirmed exit"
                ),
                r_current=round(r_current, 2),
                urgency="next_open",
                notes=close_result.reason,
                confirmation=close_result.to_dict(),
            )

        if self.cfg.enable_partial_3r and not position.partial_taken:
            reached_3r = (
                current_price >= position.r_multiple_target_3r
                if direction == "long"
                else current_price <= position.r_multiple_target_3r
            )
            if reached_3r:
                return ExitSignal(
                    symbol=symbol,
                    should_exit=True,
                    exit_type="partial_3r",
                    exit_pct=self.cfg.partial_3r_pct,
                    exit_price=current_price,
                    reason=f"Reached 3R target {position.r_multiple_target_3r:.2f}",
                    r_current=round(r_current, 2),
                    urgency="eod",
                    notes="Scale out and tighten stop",
                )

        if self.cfg.enable_partial_5r and position.partial_taken and not position.partial_taken_5r:
            reached_5r = (
                current_price >= position.r_multiple_target_5r
                if direction == "long"
                else current_price <= position.r_multiple_target_5r
            )
            if reached_5r:
                return ExitSignal(
                    symbol=symbol,
                    should_exit=True,
                    exit_type="partial_5r",
                    exit_pct=self.cfg.partial_5r_pct,
                    exit_price=current_price,
                    reason=f"Reached 5R target {position.r_multiple_target_5r:.2f}",
                    r_current=round(r_current, 2),
                    urgency="eod",
                    notes="Scale out more and let runner work",
                )

        if self.cfg.enable_time_stop and position.days_held >= self.cfg.time_stop_days and r_current < self.cfg.time_stop_r_threshold:
            return ExitSignal(
                symbol=symbol,
                should_exit=True,
                exit_type="time_stop",
                exit_pct=1.0,
                exit_price=current_price,
                reason=(
                    f"{position.days_held}d held, only {r_current:.1f}R "
                    f"vs threshold {self.cfg.time_stop_r_threshold:.1f}R"
                ),
                r_current=round(r_current, 2),
                urgency="next_open",
            )

        if regime.lower() == "bear" and direction == "long" and not position.partial_taken:
            return ExitSignal(
                symbol=symbol,
                should_exit=True,
                exit_type="regime_change_bear",
                exit_pct=self.cfg.bear_regime_reduce_pct,
                exit_price=current_price,
                reason="Market regime changed to Bear, reducing long exposure",
                r_current=round(r_current, 2),
                urgency="next_open",
                notes=f"Reduce {int(self.cfg.bear_regime_reduce_pct * 100)}% of position",
            )

        return self._no_exit(symbol, current_price, r_current)

    def evaluate_portfolio(
        self,
        positions: list[Position],
        ohlcv_map: dict[str, pd.DataFrame],
        price_map: dict[str, float],
        regime: str = "bull",
    ) -> list[ExitSignal]:
        signals: list[ExitSignal] = []
        for position in positions:
            df = ohlcv_map.get(position.symbol)
            price = price_map.get(position.symbol, position.entry_price)
            signal = self.evaluate(position=position, df=df, current_price=price, regime=regime)
            if signal.should_exit:
                signals.append(signal)
        return sorted(signals, key=lambda item: item.exit_pct, reverse=True)

    @staticmethod
    def _calc_r(position: Position, current_price: float) -> float:
        risk_per_share = position.risk_per_share
        if risk_per_share <= 0:
            return 0.0
        if position.direction == "short":
            return (position.entry_price - current_price) / risk_per_share
        return (current_price - position.entry_price) / risk_per_share

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out.columns = [str(col).lower() for col in out.columns]
        return out

    @staticmethod
    def _no_exit(symbol: str, price: float, r_current: float) -> ExitSignal:
        return ExitSignal(
            symbol=symbol,
            should_exit=False,
            exit_type="none",
            exit_pct=0.0,
            exit_price=price,
            reason="Hold: no exit condition triggered",
            r_current=round(r_current, 2),
            urgency="none",
        )
