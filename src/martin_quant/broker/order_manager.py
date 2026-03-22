"""order_manager.py

High-level order manager that bridges scan outputs directly to IBKR execution.

Responsibilities:
  - Receives TradeSignal or ExecutionPlan lists
  - Filters items by portfolio limits and existing positions
  - Submits bracket orders via IBKRBridge
  - Tracks all orders in orders_log.csv
  - Manages partial exits from ExitManager signals
  - Sends Telegram confirmation after each order
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from martin_quant.broker.ibkr_bridge import IBKRBridge

log = logging.getLogger(__name__)


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
    confirmation_mode: str = ""
    confirmation_bars: int = 0
    confirmation_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "order_id": self.order_id,
            "action": self.action,
            "qty": self.quantity,
            "entry": self.entry_price,
            "stop": self.stop_price,
            "target": self.target_price,
            "status": self.status,
            "reason": self.reason,
            "confirmation_mode": self.confirmation_mode,
            "confirmation_bars": self.confirmation_bars,
            "confirmation_reason": self.confirmation_reason,
        }


class OrderManager:
    """Converts scan outputs to live IBKR orders."""

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
        allow_shorts: bool = False,
    ) -> None:
        self.bridge = bridge
        self.equity = equity
        self.max_signals = max_signals
        self.use_limit = use_limit_entry
        self.orders_log = orders_log
        self.dry_run = dry_run
        self.tg_token = telegram_token
        self.tg_chat = telegram_chat_id
        self.allow_shorts = allow_shorts
        self._results: list[ExecutionResult] = []
        os.makedirs(os.path.dirname(orders_log), exist_ok=True) if "/" in orders_log else None
        log.info(
            "OrderManager init: equity=$%.0f max=%d dry_run=%s limit=%s allow_shorts=%s",
            equity, max_signals, dry_run, use_limit_entry, allow_shorts,
        )

    def execute_signals(
        self,
        signals: list,
        existing_symbols: Optional[set[str]] = None,
    ) -> list[ExecutionResult]:
        existing = existing_symbols or {
            sym for sym, pos in self.bridge.positions.items()
            if abs(pos.quantity) > 0
        }
        results: list[ExecutionResult] = []
        submitted = 0

        for sig in signals:
            if submitted >= self.max_signals:
                break
            if sig.symbol in existing:
                results.append(self._skip(sig.symbol, sig.direction, sig.entry_price, sig.stop_price, sig.target_price, "already_in_portfolio"))
                continue
            if sig.direction == "short" and not self.allow_shorts:
                results.append(self._skip(sig.symbol, sig.direction, sig.entry_price, sig.stop_price, sig.target_price, "shorts_disabled"))
                continue
            try:
                result = self._submit_order_candidate(
                    symbol=sig.symbol,
                    direction=sig.direction,
                    quantity=max(1, int(sig.shares)),
                    entry_price=sig.entry_price,
                    stop_price=sig.stop_price,
                    target_price=sig.target_price,
                    score=float(getattr(sig, "total_score", 0.0)),
                    confirmation=self._confirmation_payload(sig),
                )
                results.append(result)
                if result.status == "submitted":
                    submitted += 1
                    existing.add(sig.symbol)
            except Exception as e:
                log.error("Error executing %s: %s", sig.symbol, e)
                results.append(ExecutionResult(
                    symbol=sig.symbol, order_id=-1,
                    action="BUY" if sig.direction == "long" else "SELL", quantity=0,
                    entry_price=sig.entry_price, stop_price=sig.stop_price,
                    target_price=sig.target_price,
                    status="error", reason=str(e),
                    timestamp=_now_str(),
                ))

        self._finalize(results)
        return results

    def execute_plans(
        self,
        plans: list,
        existing_symbols: Optional[set[str]] = None,
    ) -> list[ExecutionResult]:
        existing = existing_symbols or {
            sym for sym, pos in self.bridge.positions.items()
            if abs(pos.quantity) > 0
        }
        results: list[ExecutionResult] = []
        submitted = 0

        for plan in plans:
            if submitted >= self.max_signals:
                break
            if not getattr(plan, "active", False):
                results.append(self._skip(plan.symbol, plan.direction, plan.entry_price, plan.stop_price, plan.target_price, getattr(plan, "block_reason", "inactive_plan") or "inactive_plan"))
                continue
            if plan.symbol in existing:
                results.append(self._skip(plan.symbol, plan.direction, plan.entry_price, plan.stop_price, plan.target_price, "already_in_portfolio"))
                continue
            if str(getattr(plan, "direction", "long")).lower() == "short" and not self.allow_shorts:
                results.append(self._skip(plan.symbol, plan.direction, plan.entry_price, plan.stop_price, plan.target_price, "shorts_disabled"))
                continue
            try:
                result = self._submit_order_candidate(
                    symbol=plan.symbol,
                    direction=plan.direction,
                    quantity=max(1, int(getattr(plan, "shares", 0))),
                    entry_price=plan.entry_price,
                    stop_price=plan.stop_price,
                    target_price=plan.target_price,
                    score=float(getattr(plan, "total_score", 0.0)),
                    reason=f"plan_tier={getattr(plan, 'priority_tier', '')} style={getattr(plan, 'execution_style', '')}".strip(),
                    confirmation=self._confirmation_payload(plan),
                )
                results.append(result)
                if result.status == "submitted":
                    submitted += 1
                    existing.add(plan.symbol)
            except Exception as e:
                log.error("Error executing plan %s: %s", plan.symbol, e)
                results.append(ExecutionResult(
                    symbol=plan.symbol, order_id=-1,
                    action="BUY" if str(plan.direction).lower() == "long" else "SELL", quantity=0,
                    entry_price=plan.entry_price or 0.0, stop_price=plan.stop_price or 0.0,
                    target_price=plan.target_price or 0.0,
                    status="error", reason=str(e), timestamp=_now_str(),
                ))

        self._finalize(results)
        return results

    def _submit_order_candidate(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        target_price: float,
        score: float = 0.0,
        reason: str = "",
        confirmation: dict[str, object] | None = None,
    ) -> ExecutionResult:
        action = "BUY" if str(direction).lower() == "long" else "SELL"
        qty = max(1, int(quantity))
        ts = _now_str()
        confirmation_mode = str(confirmation.get("mode", "")) if confirmation else ""
        confirmation_bars = int(confirmation.get("required_bars", 0) or 0) if confirmation else 0
        confirmation_reason = str(confirmation.get("reason", "")) if confirmation else ""

        if self.dry_run:
            log.info(
                "[DRY RUN] Would submit bracket: %s %s %d @ entry=%.2f stop=%.2f target=%.2f score=%.3f",
                action, symbol, qty, entry_price, stop_price, target_price, score,
            )
            return ExecutionResult(
                symbol=symbol, order_id=0, action=action, quantity=qty,
                entry_price=entry_price, stop_price=stop_price, target_price=target_price,
                status="submitted", reason=reason or "dry_run", timestamp=ts,
                confirmation_mode=confirmation_mode,
                confirmation_bars=confirmation_bars,
                confirmation_reason=confirmation_reason,
            )

        order_id = self.bridge.submit_bracket(
            symbol=symbol,
            action=action,
            quantity=qty,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            use_market_entry=not self.use_limit,
        )
        log.info(
            "Submitted bracket #%d: %s %s %d score=%.3f",
            order_id, action, symbol, qty, score,
        )
        return ExecutionResult(
            symbol=symbol, order_id=order_id,
            action=action, quantity=qty,
            entry_price=entry_price, stop_price=stop_price, target_price=target_price,
            status="submitted", reason=reason, timestamp=ts,
            confirmation_mode=confirmation_mode,
            confirmation_bars=confirmation_bars,
            confirmation_reason=confirmation_reason,
        )

    def _skip(self, symbol: str, direction: str, entry_price: float, stop_price: float, target_price: float, reason: str) -> ExecutionResult:
        log.debug("Skip %s: %s", symbol, reason)
        return ExecutionResult(
            symbol=symbol, order_id=-1,
            action="BUY" if str(direction).lower() == "long" else "SELL",
            quantity=0,
            entry_price=entry_price or 0.0, stop_price=stop_price or 0.0,
            target_price=target_price or 0.0,
            status="skipped", reason=reason, timestamp=_now_str(),
        )

    def execute_exit(
        self,
        symbol: str,
        shares_to_sell: int,
        exit_type: str = "partial",
        use_market: bool = True,
        limit_price: float = 0.0,
    ) -> ExecutionResult:
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

        action = "SELL" if pos.quantity > 0 else "BUY"
        if self.dry_run:
            log.info("[DRY RUN] EXIT %s: %s %d (%s)", symbol, action, qty, exit_type)
            return ExecutionResult(
                symbol=symbol, order_id=0, action=action,
                quantity=qty, entry_price=pos.avg_cost,
                stop_price=0, target_price=0,
                status="submitted", reason=f"dry_run_{exit_type}", timestamp=ts,
            )

        if use_market:
            oid = self.bridge.submit_market(symbol, action, qty)
        else:
            oid = self.bridge.submit_limit(symbol, action, qty, limit_price)

        log.info("EXIT %s: %s %d (type=%s, oid=%d)", symbol, action, qty, exit_type, oid)
        result = ExecutionResult(
            symbol=symbol, order_id=oid, action=action,
            quantity=qty, entry_price=pos.avg_cost,
            stop_price=0, target_price=0,
            status="submitted", reason=exit_type, timestamp=ts,
        )
        self._log_results([result])
        return result

    def _finalize(self, results: list[ExecutionResult]) -> None:
        self._log_results(results)
        self._results.extend(results)
        self._notify_telegram(results)

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

    def _notify_telegram(self, results: list[ExecutionResult]) -> None:
        submitted = [r for r in results if r.status == "submitted"]
        if not submitted or not self.tg_token or not self.tg_chat:
            return
        try:
            import requests

            lines = [f"Order Execution Report ({_now_str()})"]
            for r in submitted:
                line = (
                    f"{r.action} {r.symbol} x{r.quantity} | "
                    f"entry={r.entry_price:.2f} stop={r.stop_price:.2f} "
                    f"target={r.target_price:.2f}"
                )
                if r.confirmation_reason:
                    line += f" | confirm={r.confirmation_mode or 'bar_close'}:{r.confirmation_bars} {r.confirmation_reason}"
                if r.reason:
                    line += f" [{r.reason}]"
                lines.append(line)
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

    @staticmethod
    def _confirmation_payload(item: object) -> dict[str, object] | None:
        payload = getattr(item, "entry_confirmation", None)
        if isinstance(payload, dict):
            return payload
        return None



def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
