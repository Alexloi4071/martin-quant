"""order_manager.py

High-level order manager that bridges DailyScanResult signals
directly to IBKR execution.

Responsibilities:
  - Receives TradeSignal list from DailyScanner
  - Filters signals by portfolio limits and existing positions
  - Submits bracket orders via IBKRBridge
  - Tracks all orders in orders_log.csv
  - Manages partial exits from PartialExitManager signals
  - Sends Telegram confirmation after each order

Usage:
    bridge = IBKRBridge(paper=True)
    bridge.connect()
    mgr = OrderManager(bridge, equity=150_000)
    mgr.execute_signals(scan_result.signals)
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from martin_quant.broker.ibkr_bridge import IBKRBridge, OrderRecord

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Execution result
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    symbol: str
    order_id: int
    action: str
    quantity: int
    entry_price: float
    stop_price: float
    target_price: float
    status: str       # submitted | skipped | error
    reason: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp":    self.timestamp,
            "symbol":       self.symbol,
            "order_id":     self.order_id,
            "action":       self.action,
            "qty":          self.quantity,
            "entry":        self.entry_price,
            "stop":         self.stop_price,
            "target":       self.target_price,
            "status":       self.status,
            "reason":       self.reason,
        }


# ---------------------------------------------------------------------------
# Order Manager
# ---------------------------------------------------------------------------

class OrderManager:
    """
    Converts scan signals to live IBKR orders.

    Parameters
    ----------
    bridge : IBKRBridge
        Connected (or simulation) bridge instance
    equity : float
        Current account equity (used for position sizing)
    max_signals : int
        Maximum number of new orders per run (default 5)
    use_limit_entry : bool
        True = limit order at entry_price, False = market order
    orders_log : str
        Path to CSV log file for all order activity
    dry_run : bool
        True = log only, do not actually submit orders
    telegram_token / telegram_chat_id :
        Optional Telegram credentials for order notifications
    """

    def __init__(
        self,
        bridge: IBKRBridge,
        equity: float = 100_000.0,
        max_signals: int = 5,
        use_limit_entry: bool = True,
        orders_log: str = "data/orders_log.csv",
        dry_run: bool = False,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
    ) -> None:
        self.bridge           = bridge
        self.equity           = equity
        self.max_signals      = max_signals
        self.use_limit        = use_limit_entry
        self.orders_log       = orders_log
        self.dry_run          = dry_run
        self.tg_token         = telegram_token
        self.tg_chat          = telegram_chat_id
        self._results: list[ExecutionResult] = []
        os.makedirs(os.path.dirname(orders_log), exist_ok=True) if "/" in orders_log else None
        log.info(
            "OrderManager init: equity=$%.0f max=%d dry_run=%s limit=%s",
            equity, max_signals, dry_run, use_limit_entry,
        )

    # ------------------------------------------------------------------
    # Main execute
    # ------------------------------------------------------------------

    def execute_signals(
        self,
        signals: list,     # list[TradeSignal] from daily_scan
        existing_symbols: Optional[set[str]] = None,
    ) -> list[ExecutionResult]:
        """
        Execute top N signals from DailyScanResult.

        Parameters
        ----------
        signals : list[TradeSignal]
            Already sorted by total_score desc
        existing_symbols : set[str]
            Symbols already in portfolio (skip duplicates)
        """
        existing = existing_symbols or {
            sym for sym, pos in self.bridge.positions.items()
            if abs(pos.quantity) > 0
        }
        results: list[ExecutionResult] = []
        submitted = 0

        for sig in signals:
            if submitted >= self.max_signals:
                break

            # Skip existing positions
            if sig.symbol in existing:
                results.append(self._skip(sig, "already_in_portfolio"))
                continue

            # Skip short signals unless we explicitly allow shorts
            if sig.direction == "short":
                results.append(self._skip(sig, "shorts_disabled"))
                continue

            # Submit
            try:
                result = self._submit_signal(sig)
                results.append(result)
                if result.status == "submitted":
                    submitted += 1
                    existing.add(sig.symbol)
            except Exception as e:
                log.error("Error executing %s: %s", sig.symbol, e)
                results.append(ExecutionResult(
                    symbol=sig.symbol, order_id=-1,
                    action="BUY", quantity=0,
                    entry_price=sig.entry_price, stop_price=sig.stop_price,
                    target_price=sig.target_price,
                    status="error", reason=str(e),
                    timestamp=_now_str(),
                ))

        self._log_results(results)
        self._results.extend(results)
        self._notify_telegram(results)
        return results

    def _submit_signal(self, sig) -> ExecutionResult:
        action   = "BUY" if sig.direction == "long" else "SELL"
        qty      = max(1, sig.shares)
        ts       = _now_str()

        if self.dry_run:
            log.info(
                "[DRY RUN] Would submit bracket: %s %s %d @ entry=%.2f "
                "stop=%.2f target=%.2f score=%.3f",
                action, sig.symbol, qty,
                sig.entry_price, sig.stop_price, sig.target_price, sig.total_score,
            )
            return ExecutionResult(
                symbol=sig.symbol, order_id=0, action=action, quantity=qty,
                entry_price=sig.entry_price, stop_price=sig.stop_price,
                target_price=sig.target_price,
                status="submitted", reason="dry_run", timestamp=ts,
            )

        order_id = self.bridge.submit_bracket(
            symbol=sig.symbol,
            action=action,
            quantity=qty,
            entry_price=sig.entry_price,
            stop_price=sig.stop_price,
            target_price=sig.target_price,
            use_market_entry=not self.use_limit,
        )
        log.info(
            "Submitted bracket #%d: %s %s %d score=%.3f",
            order_id, action, sig.symbol, qty, sig.total_score,
        )
        return ExecutionResult(
            symbol=sig.symbol, order_id=order_id,
            action=action, quantity=qty,
            entry_price=sig.entry_price, stop_price=sig.stop_price,
            target_price=sig.target_price,
            status="submitted", timestamp=ts,
        )

    def _skip(self, sig, reason: str) -> ExecutionResult:
        log.debug("Skip %s: %s", sig.symbol, reason)
        return ExecutionResult(
            symbol=sig.symbol, order_id=-1,
            action="BUY" if sig.direction == "long" else "SELL",
            quantity=0,
            entry_price=sig.entry_price, stop_price=sig.stop_price,
            target_price=sig.target_price,
            status="skipped", reason=reason, timestamp=_now_str(),
        )

    # ------------------------------------------------------------------
    # Partial exit execution
    # ------------------------------------------------------------------

    def execute_exit(
        self,
        symbol: str,
        shares_to_sell: int,
        exit_type: str = "partial",     # partial | full
        use_market: bool = True,
        limit_price: float = 0.0,
    ) -> ExecutionResult:
        """Execute an exit order for an existing position."""
        ts = _now_str()
        pos = self.bridge.get_position(symbol)
        if pos is None:
            return ExecutionResult(
                symbol=symbol, order_id=-1, action="SELL",
                quantity=0, entry_price=0, stop_price=0, target_price=0,
                status="skipped", reason="no_position", timestamp=ts,
            )

        qty = min(shares_to_sell, int(abs(pos.quantity)))
        if qty <= 0:
            return ExecutionResult(
                symbol=symbol, order_id=-1, action="SELL",
                quantity=0, entry_price=0, stop_price=0, target_price=0,
                status="skipped", reason="qty_zero", timestamp=ts,
            )

        if self.dry_run:
            log.info("[DRY RUN] EXIT %s: SELL %d (%s)", symbol, qty, exit_type)
            return ExecutionResult(
                symbol=symbol, order_id=0, action="SELL",
                quantity=qty, entry_price=pos.avg_cost,
                stop_price=0, target_price=0,
                status="submitted", reason=f"dry_run_{exit_type}", timestamp=ts,
            )

        if use_market:
            oid = self.bridge.submit_market(symbol, "SELL", qty)
        else:
            oid = self.bridge.submit_limit(symbol, "SELL", qty, limit_price)

        log.info("EXIT %s: SELL %d (type=%s, oid=%d)", symbol, qty, exit_type, oid)
        result = ExecutionResult(
            symbol=symbol, order_id=oid, action="SELL",
            quantity=qty, entry_price=pos.avg_cost,
            stop_price=0, target_price=0,
            status="submitted", reason=exit_type, timestamp=ts,
        )
        self._log_results([result])
        return result

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_results(self, results: list[ExecutionResult]) -> None:
        if not results:
            return
        write_header = not os.path.exists(self.orders_log)
        with open(self.orders_log, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].to_dict().keys())
            if write_header:
                writer.writeheader()
            for r in results:
                writer.writerow(r.to_dict())

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------

    def _notify_telegram(self, results: list[ExecutionResult]) -> None:
        submitted = [r for r in results if r.status == "submitted"]
        if not submitted or not self.tg_token or not self.tg_chat:
            return
        try:
            import requests
            lines = [f"\U0001f4e4 Order Execution Report ({_now_str()})"]
            for r in submitted:
                lines.append(
                    f"{r.action} {r.symbol} x{r.quantity} | "
                    f"entry={r.entry_price:.2f} stop={r.stop_price:.2f} "
                    f"target={r.target_price:.2f}"
                    + (" [DRY RUN]" if r.reason == "dry_run" else "")
                )
            msg = "\n".join(lines)
            requests.post(
                f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                json={"chat_id": self.tg_chat, "text": msg},
                timeout=5,
            )
        except Exception as e:
            log.warning("Telegram notify failed: %s", e)

    @property
    def results(self) -> list[ExecutionResult]:
        return list(self._results)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
