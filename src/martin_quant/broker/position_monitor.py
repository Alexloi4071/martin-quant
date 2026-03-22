"""position_monitor.py

Real-time position monitor that continuously checks open positions against
ExitManager rules.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import pandas as pd

from martin_quant.broker.ibkr_bridge import IBKRBridge, PortfolioPosition
from martin_quant.broker.order_manager import OrderManager
from martin_quant.risk import ExitManager, Position

log = logging.getLogger(__name__)


class PositionMonitor:
    def __init__(
        self,
        bridge: IBKRBridge,
        order_manager: OrderManager,
        ohlcv_getter: Callable[[str], Optional[pd.DataFrame]],
        entry_prices: Optional[dict[str, float]] = None,
        stop_prices: Optional[dict[str, float]] = None,
        interval: float = 60.0,
        regime: str = "BULL",
    ) -> None:
        self.bridge = bridge
        self.order_mgr = order_manager
        self.ohlcv_getter = ohlcv_getter
        self.entry_prices = entry_prices or {}
        self.stop_prices = stop_prices or {}
        self.interval = interval
        self.regime = regime
        self.exit_mgr = ExitManager()

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._check_count = 0
        self._action_log: list[dict] = []
        self._positions_meta: dict[str, Position] = {}

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="position-monitor"
        )
        self._thread.start()
        log.info(
            "PositionMonitor started (interval=%.0fs, regime=%s)",
            self.interval, self.regime,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        log.info("PositionMonitor stopped after %d checks", self._check_count)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def update_regime(self, regime: str) -> None:
        self.regime = regime
        log.info("PositionMonitor regime updated: %s", regime)

    def add_position(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        target_price: float = 0.0,
        direction: str = "long",
        shares: int = 0,
        entry_confirmation: dict[str, object] | None = None,
    ) -> None:
        self.entry_prices[symbol] = entry_price
        self.stop_prices[symbol] = stop_price
        self._positions_meta[symbol] = Position(
            symbol=symbol,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price if target_price > 0 else entry_price,
            shares=max(1, shares) if shares else 1,
            entry_date=_today_str(),
            direction=direction,
            current_shares=max(1, shares) if shares else 1,
            entry_confirmation=entry_confirmation,
        )
        log.info(
            "Monitoring %s: entry=%.2f stop=%.2f target=%.2f direction=%s",
            symbol, entry_price, stop_price, target_price if target_price > 0 else entry_price, direction,
        )

    def register_execution_plan(self, plan) -> None:
        self.add_position(
            symbol=str(getattr(plan, "symbol", "")),
            entry_price=float(getattr(plan, "entry_price", 0.0) or 0.0),
            stop_price=float(getattr(plan, "stop_price", 0.0) or 0.0),
            target_price=float(getattr(plan, "target_price", 0.0) or 0.0),
            direction=str(getattr(plan, "direction", "long")),
            shares=int(getattr(plan, "shares", 0) or 0),
            entry_confirmation=getattr(plan, "entry_confirmation", None),
        )

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._check_positions()
            except Exception as e:
                log.error("PositionMonitor check error: %s", e)
            self._stop_event.wait(timeout=self.interval)

    def _check_positions(self) -> None:
        self._check_count += 1
        positions = self.bridge.positions
        if not positions:
            log.debug("No positions to monitor")
            return

        log.debug("Checking %d positions (check #%d)", len(positions), self._check_count)
        for symbol, pos in positions.items():
            if abs(pos.quantity) < 1:
                continue
            self._check_one(symbol, pos)

    def _get_or_create_position(self, symbol: str, pos: PortfolioPosition) -> Position:
        tracked = self._positions_meta.get(symbol)
        if tracked is not None:
            tracked.current_shares = int(abs(pos.quantity))
            return tracked

        entry = self.entry_prices.get(symbol, pos.avg_cost)
        stop = self.stop_prices.get(symbol, pos.avg_cost)
        direction = "short" if pos.quantity < 0 else "long"
        tracked = Position(
            symbol=symbol,
            entry_price=entry,
            stop_price=stop,
            target_price=entry,
            shares=max(1, int(abs(pos.quantity))),
            entry_date=_today_str(),
            direction=direction,
            current_shares=max(1, int(abs(pos.quantity))),
        )
        self._positions_meta[symbol] = tracked
        return tracked

    def _check_one(self, symbol: str, pos: PortfolioPosition) -> None:
        try:
            df = self.ohlcv_getter(symbol)
            if df is None or len(df) < 10:
                log.debug("%s: no fresh data", symbol)
                return

            tracked = self._get_or_create_position(symbol, pos)
            current_price = float(df["close"].iloc[-1])
            exit_signal = self.exit_mgr.evaluate(
                position=tracked,
                df=df,
                current_price=current_price,
                regime=self.regime,
            )
            if not exit_signal.should_exit:
                log.debug("%s: hold (R=%.2f)", symbol, exit_signal.r_current)
                return

            shares_to_exit = max(1, int(round(abs(pos.quantity) * exit_signal.exit_pct)))
            log.info(
                "EXIT SIGNAL: %s sell=%d type=%s urgency=%s reason=%s",
                symbol, shares_to_exit, exit_signal.exit_type,
                exit_signal.urgency, exit_signal.reason,
            )
            use_market = exit_signal.urgency in {"immediate", "next_open"}
            exec_result = self.order_mgr.execute_exit(
                symbol=symbol,
                shares_to_sell=shares_to_exit,
                exit_type=exit_signal.exit_type,
                use_market=use_market,
            )
            self._action_log.append({
                "time": _now_str(),
                "symbol": symbol,
                "action": f"exit_{exit_signal.exit_type}",
                "shares": shares_to_exit,
                "price": current_price,
                "reason": exit_signal.reason,
                "order_id": exec_result.order_id,
                "entry_confirmation": tracked.entry_confirmation,
                "exit_confirmation": exit_signal.confirmation,
            })

            if exec_result.status == "submitted":
                self._apply_exit_state(symbol, tracked, shares_to_exit, exit_signal)

        except Exception as e:
            log.error("_check_one %s error: %s", symbol, e)

    def _apply_exit_state(self, symbol: str, tracked: Position, shares_to_exit: int, exit_signal) -> None:
        tracked.current_shares = max(0, tracked.current_shares - shares_to_exit)
        tracked.exit_confirmation = exit_signal.confirmation
        exit_type = exit_signal.exit_type
        if exit_type == "partial_3r":
            tracked.partial_taken = True
        elif exit_type == "partial_5r":
            tracked.partial_taken = True
            tracked.partial_taken_5r = True
        elif exit_type in {"hard_stop", "ema9_close_confirm", "time_stop", "regime_change_bear", "manual"}:
            tracked.current_shares = 0

        if tracked.current_shares <= 0 or exit_type in {"hard_stop", "ema9_close_confirm", "time_stop", "manual"}:
            self._positions_meta.pop(symbol, None)
            self.entry_prices.pop(symbol, None)
            self.stop_prices.pop(symbol, None)

    @property
    def action_log(self) -> list[dict]:
        return list(self._action_log)



def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



def _today_str() -> str:
    from datetime import date
    return str(date.today())
