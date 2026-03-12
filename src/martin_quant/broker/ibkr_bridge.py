"""ibkr_bridge.py

Interactive Brokers TWS / IB Gateway execution bridge.

Connects martin-quant scan signals directly to IBKR for order execution.
Supports both PAPER and LIVE trading modes with identical code paths.

Features:
  - Auto-connect to TWS or IB Gateway (configurable port)
  - Market / Limit / Stop / Bracket order submission
  - Real-time position and P&L monitoring via EWrapper callbacks
  - Order status tracking (filled, cancelled, partial)
  - Automatic stop-loss order attachment
  - Thread-safe order ID management
  - Graceful disconnect + error handling

Prerequisites:
    pip install ibapi
    TWS or IB Gateway running on localhost

Usage:
    bridge = IBKRBridge(paper=True)   # paper=True uses port 7497
    bridge.connect()
    order_id = bridge.submit_bracket(
        symbol="NVDA",
        action="BUY",
        quantity=10,
        entry_price=500.0,
        stop_price=492.0,
        target_price=516.0,
    )
    bridge.disconnect()
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IBKR import guard — graceful fallback if ibapi not installed
# ---------------------------------------------------------------------------
try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.order import Order
    from ibapi.common import OrderId, TickerId
    IBAPI_AVAILABLE = True
except ImportError:
    IBAPI_AVAILABLE = False
    # Stub base classes so the module still imports cleanly
    class EWrapper:  # type: ignore
        pass
    class EClient:   # type: ignore
        def __init__(self, wrapper): pass

log.debug("ibapi available: %s", IBAPI_AVAILABLE)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OrderRecord:
    order_id: int
    symbol: str
    action: str           # BUY | SELL
    order_type: str       # MKT | LMT | STP | BRACKET
    quantity: int
    entry_price: float
    stop_price: float
    target_price: float
    status: str = "Submitted"   # Submitted | Filled | Cancelled | PartialFill
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    pnl: float = 0.0
    submitted_at: str = ""
    filled_at: str = ""

    def to_dict(self) -> dict:
        return {
            "order_id":       self.order_id,
            "symbol":         self.symbol,
            "action":         self.action,
            "type":           self.order_type,
            "qty":            self.quantity,
            "entry":          self.entry_price,
            "stop":           self.stop_price,
            "target":         self.target_price,
            "status":         self.status,
            "filled_qty":     self.filled_qty,
            "avg_fill":       self.avg_fill_price,
            "pnl":            self.pnl,
            "submitted_at":   self.submitted_at,
            "filled_at":      self.filled_at,
        }


@dataclass
class PortfolioPosition:
    symbol: str
    quantity: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    account: str


# ---------------------------------------------------------------------------
# IBKR Bridge
# ---------------------------------------------------------------------------

class IBKRBridge(EWrapper, EClient):
    """
    Full-featured IBKR execution bridge.

    Parameters
    ----------
    paper : bool
        True  = Paper Trading (port 7497 TWS / 4002 Gateway)
        False = Live Trading  (port 7496 TWS / 4001 Gateway)
    host : str
        TWS/Gateway host (default: 127.0.0.1)
    client_id : int
        IBKR client ID (use different IDs for different scripts)
    use_gateway : bool
        True = IB Gateway ports, False = TWS ports
    on_fill_callback : Callable
        Optional callback when order is fully filled: fn(OrderRecord)
    """

    # Port map: (paper, use_gateway) -> port
    PORT_MAP = {
        (True,  False): 7497,   # TWS Paper
        (False, False): 7496,   # TWS Live
        (True,  True):  4002,   # Gateway Paper
        (False, True):  4001,   # Gateway Live
    }

    def __init__(
        self,
        paper: bool = True,
        host: str = "127.0.0.1",
        client_id: int = 1,
        use_gateway: bool = False,
        on_fill_callback: Optional[Callable[[OrderRecord], None]] = None,
    ) -> None:
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        self.paper       = paper
        self.host        = host
        self.client_id   = client_id
        self.port        = self.PORT_MAP[(paper, use_gateway)]
        self.on_fill_cb  = on_fill_callback

        self._next_order_id: int = 0
        self._order_id_lock     = threading.Lock()
        self._connected         = threading.Event()
        self._orders: dict[int, OrderRecord] = {}
        self._positions: dict[str, PortfolioPosition] = {}
        self._account_value: float = 0.0
        self._msg_thread: Optional[threading.Thread] = None

        mode = "PAPER" if paper else "LIVE"
        log.info("IBKRBridge init: %s mode, port=%d, clientId=%d", mode, self.port, client_id)
        if not IBAPI_AVAILABLE:
            log.warning("ibapi not installed — bridge in SIMULATION mode")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, timeout: float = 15.0) -> bool:
        """Connect to TWS/Gateway. Returns True if successful."""
        if not IBAPI_AVAILABLE:
            log.warning("ibapi not installed, using simulation mode")
            self._connected.set()
            return True
        try:
            super().connect(self.host, self.port, self.client_id)
            self._msg_thread = threading.Thread(
                target=self.run, daemon=True, name="ibkr-msg"
            )
            self._msg_thread.start()
            if not self._connected.wait(timeout=timeout):
                log.error("IBKR connection timeout after %.0fs", timeout)
                return False
            log.info("Connected to IBKR (paper=%s, port=%d)", self.paper, self.port)
            self.reqAccountUpdates(True, "")
            return True
        except Exception as e:
            log.error("IBKR connect failed: %s", e)
            return False

    def disconnect(self) -> None:
        if IBAPI_AVAILABLE:
            try:
                self.reqAccountUpdates(False, "")
                super().disconnect()
                log.info("Disconnected from IBKR")
            except Exception:
                pass
        self._connected.clear()

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    # ------------------------------------------------------------------
    # EWrapper callbacks
    # ------------------------------------------------------------------

    def nextValidId(self, orderId: int) -> None:  # type: ignore
        with self._order_id_lock:
            self._next_order_id = orderId
        self._connected.set()
        log.debug("nextValidId: %d", orderId)

    def orderStatus(  # type: ignore
        self,
        orderId, status, filled, remaining,
        avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice,
    ) -> None:
        rec = self._orders.get(orderId)
        if rec:
            rec.status         = status
            rec.filled_qty     = int(filled)
            rec.avg_fill_price = round(float(avgFillPrice), 4)
            log.info(
                "Order %d %s %s: status=%s filled=%d avgFill=%.4f",
                orderId, rec.symbol, rec.action, status, int(filled), float(avgFillPrice),
            )
            if status == "Filled" and self.on_fill_cb:
                rec.filled_at = _now_str()
                try:
                    self.on_fill_cb(rec)
                except Exception as e:
                    log.warning("on_fill_cb error: %s", e)

    def execDetails(self, reqId, contract, execution) -> None:  # type: ignore
        log.debug(
            "execDetails: %s %s %d @ %.4f",
            execution.side, contract.symbol, execution.shares, execution.price,
        )

    def updatePortfolio(  # type: ignore
        self,
        contract, position, marketPrice, marketValue,
        averageCost, unrealizedPNL, realizedPNL, accountName,
    ) -> None:
        sym = contract.symbol
        self._positions[sym] = PortfolioPosition(
            symbol=sym,
            quantity=float(position),
            avg_cost=round(float(averageCost), 4),
            market_value=round(float(marketValue), 2),
            unrealized_pnl=round(float(unrealizedPNL), 2),
            realized_pnl=round(float(realizedPNL), 2),
            account=accountName,
        )

    def updateAccountValue(self, key, val, currency, accountName) -> None:  # type: ignore
        if key == "NetLiquidation" and currency == "USD":
            try:
                self._account_value = float(val)
                log.debug("AccountValue NetLiq: $%.2f", self._account_value)
            except Exception:
                pass

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson="") -> None:  # type: ignore
        # Filter info-only codes (2104=mkt data ok, 2106=farm ok, 2158=etc)
        info_codes = {2104, 2106, 2107, 2108, 2158, 2119}
        if errorCode in info_codes:
            log.debug("IBKR info %d: %s", errorCode, errorString)
        else:
            log.error("IBKR error req=%d code=%d: %s", reqId, errorCode, errorString)

    # ------------------------------------------------------------------
    # Order ID management
    # ------------------------------------------------------------------

    def _get_next_order_id(self) -> int:
        with self._order_id_lock:
            oid = self._next_order_id
            self._next_order_id += 1
            return oid

    # ------------------------------------------------------------------
    # Contract builder
    # ------------------------------------------------------------------

    @staticmethod
    def _stock_contract(symbol: str, exchange: str = "SMART", currency: str = "USD") -> "Contract":
        if not IBAPI_AVAILABLE:
            return object()  # type: ignore
        c = Contract()
        c.symbol   = symbol
        c.secType  = "STK"
        c.exchange = exchange
        c.currency = currency
        return c

    # ------------------------------------------------------------------
    # Order builders
    # ------------------------------------------------------------------

    @staticmethod
    def _market_order(action: str, quantity: int) -> "Order":
        if not IBAPI_AVAILABLE:
            return object()  # type: ignore
        o = Order()
        o.action          = action
        o.orderType       = "MKT"
        o.totalQuantity   = quantity
        o.tif             = "DAY"
        return o

    @staticmethod
    def _limit_order(action: str, quantity: int, limit_price: float) -> "Order":
        if not IBAPI_AVAILABLE:
            return object()  # type: ignore
        o = Order()
        o.action          = action
        o.orderType       = "LMT"
        o.totalQuantity   = quantity
        o.lmtPrice        = round(limit_price, 2)
        o.tif             = "DAY"
        return o

    @staticmethod
    def _stop_order(action: str, quantity: int, stop_price: float) -> "Order":
        if not IBAPI_AVAILABLE:
            return object()  # type: ignore
        o = Order()
        o.action          = action
        o.orderType       = "STP"
        o.totalQuantity   = quantity
        o.auxPrice        = round(stop_price, 2)
        o.tif             = "GTC"
        return o

    # ------------------------------------------------------------------
    # Public submission API
    # ------------------------------------------------------------------

    def submit_market(self, symbol: str, action: str, quantity: int) -> int:
        """Submit a market order. Returns order_id."""
        oid = self._get_next_order_id()
        contract = self._stock_contract(symbol)
        order    = self._market_order(action, quantity)
        self._orders[oid] = OrderRecord(
            order_id=oid, symbol=symbol, action=action,
            order_type="MKT", quantity=quantity,
            entry_price=0, stop_price=0, target_price=0,
            submitted_at=_now_str(),
        )
        if IBAPI_AVAILABLE and self.is_connected:
            self.placeOrder(oid, contract, order)
            log.info("MKT order %d: %s %d %s", oid, action, quantity, symbol)
        else:
            log.info("[SIM] MKT order %d: %s %d %s", oid, action, quantity, symbol)
        return oid

    def submit_limit(self, symbol: str, action: str, quantity: int, limit_price: float) -> int:
        """Submit a limit order."""
        oid = self._get_next_order_id()
        contract = self._stock_contract(symbol)
        order    = self._limit_order(action, quantity, limit_price)
        self._orders[oid] = OrderRecord(
            order_id=oid, symbol=symbol, action=action,
            order_type="LMT", quantity=quantity,
            entry_price=limit_price, stop_price=0, target_price=0,
            submitted_at=_now_str(),
        )
        if IBAPI_AVAILABLE and self.is_connected:
            self.placeOrder(oid, contract, order)
            log.info("LMT order %d: %s %d %s @ %.2f", oid, action, quantity, symbol, limit_price)
        else:
            log.info("[SIM] LMT order %d: %s %d %s @ %.2f", oid, action, quantity, symbol, limit_price)
        return oid

    def submit_bracket(
        self,
        symbol: str,
        action: str,            # BUY | SELL
        quantity: int,
        entry_price: float,     # 0 = market
        stop_price: float,
        target_price: float,
        use_market_entry: bool = False,
    ) -> int:
        """
        Submit a bracket order (entry + stop-loss + profit target).
        Returns the parent order ID.
        """
        parent_id = self._get_next_order_id()
        sl_id     = self._get_next_order_id()
        tp_id     = self._get_next_order_id()

        exit_action = "SELL" if action == "BUY" else "BUY"
        contract    = self._stock_contract(symbol)

        # Parent order
        if use_market_entry or entry_price <= 0:
            parent_order = self._market_order(action, quantity)
        else:
            parent_order = self._limit_order(action, quantity, entry_price)

        # Stop-loss child
        sl_order = self._stop_order(exit_action, quantity, stop_price)
        # Profit target child
        tp_order = self._limit_order(exit_action, quantity, target_price)

        if IBAPI_AVAILABLE:
            parent_order.orderId      = parent_id
            parent_order.transmit     = False
            sl_order.orderId          = sl_id
            sl_order.parentId         = parent_id
            sl_order.transmit         = False
            tp_order.orderId          = tp_id
            tp_order.parentId         = parent_id
            tp_order.transmit         = True   # transmit all at once
            tp_order.ocaGroup         = f"OCA_{parent_id}"
            sl_order.ocaGroup         = f"OCA_{parent_id}"
            tp_order.ocaType          = 1
            sl_order.ocaType          = 1

            if self.is_connected:
                self.placeOrder(parent_id, contract, parent_order)
                self.placeOrder(sl_id,     contract, sl_order)
                self.placeOrder(tp_id,     contract, tp_order)

        self._orders[parent_id] = OrderRecord(
            order_id=parent_id, symbol=symbol, action=action,
            order_type="BRACKET", quantity=quantity,
            entry_price=entry_price, stop_price=stop_price,
            target_price=target_price, submitted_at=_now_str(),
        )
        log.info(
            "%s bracket %d: %s %d %s entry=%.2f stop=%.2f target=%.2f",
            "[SIM]" if not IBAPI_AVAILABLE else "",
            parent_id, action, quantity, symbol,
            entry_price, stop_price, target_price,
        )
        return parent_id

    def cancel_order(self, order_id: int) -> None:
        """Cancel an open order."""
        if IBAPI_AVAILABLE and self.is_connected:
            self.cancelOrder(order_id, "")
        rec = self._orders.get(order_id)
        if rec:
            rec.status = "Cancelled"
        log.info("Cancelled order %d", order_id)

    def cancel_all(self) -> None:
        """Cancel all open orders."""
        if IBAPI_AVAILABLE and self.is_connected:
            self.reqGlobalCancel()
        log.warning("reqGlobalCancel sent — all open orders cancelled")

    # ------------------------------------------------------------------
    # Account / Portfolio access
    # ------------------------------------------------------------------

    @property
    def account_value(self) -> float:
        return self._account_value

    @property
    def positions(self) -> dict[str, PortfolioPosition]:
        return dict(self._positions)

    @property
    def open_orders(self) -> list[OrderRecord]:
        return [
            r for r in self._orders.values()
            if r.status not in ("Filled", "Cancelled")
        ]

    def get_position(self, symbol: str) -> Optional[PortfolioPosition]:
        return self._positions.get(symbol)

    def get_order(self, order_id: int) -> Optional[OrderRecord]:
        return self._orders.get(order_id)

    def wait_for_fill(self, order_id: int, timeout: float = 30.0) -> bool:
        """Block until order is filled or timeout. Returns True if filled."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            rec = self._orders.get(order_id)
            if rec and rec.status == "Filled":
                return True
            time.sleep(0.25)
        return False


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
