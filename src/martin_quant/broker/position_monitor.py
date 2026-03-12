"""position_monitor.py

Real-time position monitor — runs as a background thread, continuously
checks open positions against ExitManager rules.

Checks every `interval` seconds:
  1. Fetch current prices (via IBKR reqMktData or fallback)
  2. Run PartialExitManager for each position
  3. If exit signal: submit sell order via OrderManager
  4. Log and Telegram alert on any action taken

Usage:
    monitor = PositionMonitor(
        bridge=bridge,
        order_manager=mgr,
        ohlcv_getter=my_price_fn,   # fn(symbol) -> pd.DataFrame
        interval=60,
    )
    monitor.start()
    # ... runs in background ...
    monitor.stop()
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import pandas as pd

from martin_quant.broker.ibkr_bridge import IBKRBridge, PortfolioPosition
from martin_quant.broker.order_manager import OrderManager

log = logging.getLogger(__name__)


class PositionMonitor:
    """
    Background thread that monitors all open positions and triggers
    exit orders based on PartialExitManager rules.

    Parameters
    ----------
    bridge : IBKRBridge
    order_manager : OrderManager
    ohlcv_getter : Callable[[str], pd.DataFrame]
        Function that returns fresh OHLCV data for a symbol
    entry_prices : dict[str, float]
        Entry price per symbol (for R calculation)
    stop_prices : dict[str, float]
        Stop price per symbol (for R calculation)
    interval : float
        Seconds between check cycles (default 60)
    regime : str
        Current market regime (affects exit aggressiveness)
    """

    def __init__(
        self,
        bridge: IBKRBridge,
        order_manager: OrderManager,
        ohlcv_getter: Callable[[str], Optional[pd.DataFrame]],
        entry_prices: Optional[dict[str, float]] = None,
        stop_prices:  Optional[dict[str, float]] = None,
        interval: float = 60.0,
        regime: str = "BULL",
    ) -> None:
        self.bridge        = bridge
        self.order_mgr     = order_manager
        self.ohlcv_getter  = ohlcv_getter
        self.entry_prices  = entry_prices or {}
        self.stop_prices   = stop_prices  or {}
        self.interval      = interval
        self.regime        = regime

        self._stop_event   = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._check_count  = 0
        self._action_log: list[dict] = []

    def start(self) -> None:
        """Start the background monitoring thread."""
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
        """Stop the monitoring thread."""
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

    def add_position(self, symbol: str, entry_price: float, stop_price: float) -> None:
        """Register a new position for monitoring."""
        self.entry_prices[symbol] = entry_price
        self.stop_prices[symbol]  = stop_price
        log.info("Monitoring %s: entry=%.2f stop=%.2f", symbol, entry_price, stop_price)

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

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

    def _check_one(self, symbol: str, pos: PortfolioPosition) -> None:
        """Run exit logic for a single position."""
        try:
            df = self.ohlcv_getter(symbol)
            if df is None or len(df) < 10:
                log.debug("%s: no fresh data", symbol)
                return

            entry = self.entry_prices.get(symbol, pos.avg_cost)
            stop  = self.stop_prices.get(symbol, 0.0)
            current_price = float(df["close"].iloc[-1])

            # Run PartialExitManager
            try:
                from martin_quant.exit.partial_exit import PartialExitManager
                mgr = PartialExitManager(regime=self.regime)
                result = mgr.evaluate(
                    symbol=symbol,
                    entry_price=entry,
                    stop_price=stop,
                    current_price=current_price,
                    ohlcv_df=df,
                    shares_held=int(abs(pos.quantity)),
                )
            except ImportError:
                from martin_quant.risk.exit_manager import ExitManager
                em = ExitManager()
                exit_result = em.check(
                    symbol=symbol, entry=entry, stop=stop,
                    current_price=current_price, ohlcv_df=df,
                )
                if not exit_result.should_exit:
                    return
                result = type("R", (), {
                    "should_exit": True,
                    "shares_to_sell": int(abs(pos.quantity)),
                    "exit_type": "full",
                    "urgency": "normal",
                    "reason": exit_result.reason,
                })()

            if not result.should_exit:
                log.debug("%s: hold (R=%.2f)", symbol,
                           (current_price - entry) / max(entry - stop, 0.01))
                return

            # Execute exit
            log.info(
                "EXIT SIGNAL: %s sell=%d type=%s urgency=%s reason=%s",
                symbol, result.shares_to_sell, result.exit_type,
                result.urgency, result.reason,
            )
            use_market = (result.urgency == "immediate")
            exec_result = self.order_mgr.execute_exit(
                symbol=symbol,
                shares_to_sell=result.shares_to_sell,
                exit_type=result.exit_type,
                use_market=use_market,
            )
            self._action_log.append({
                "time":    _now_str(),
                "symbol":  symbol,
                "action":  f"exit_{result.exit_type}",
                "shares":  result.shares_to_sell,
                "price":   current_price,
                "reason":  result.reason,
                "order_id": exec_result.order_id,
            })

        except Exception as e:
            log.error("_check_one %s error: %s", symbol, e)

    @property
    def action_log(self) -> list[dict]:
        return list(self._action_log)


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
