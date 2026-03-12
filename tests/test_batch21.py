"""test_batch21.py — Unit tests for Batch 21 (IBKR broker modules)."""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np
from datetime import date
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge_sim():
    """IBKRBridge in simulation mode (ibapi not required)."""
    from martin_quant.broker import IBKRBridge
    b = IBKRBridge(paper=True)
    # Simulate connected state without real TWS
    b._connected.set()
    b._next_order_id = 100
    return b


@pytest.fixture
def order_manager(bridge_sim, tmp_path):
    from martin_quant.broker import OrderManager
    return OrderManager(
        bridge=bridge_sim,
        equity=100_000,
        max_signals=3,
        dry_run=True,      # dry run — no real orders
        orders_log=str(tmp_path / "orders.csv"),
    )


@pytest.fixture
def fake_signal():
    """Minimal TradeSignal-like object."""
    return MagicMock(
        symbol="NVDA",
        setup_type="pullback",
        direction="long",
        entry_price=500.0,
        stop_price=492.0,
        target_price=524.0,
        shares=10,
        total_score=0.85,
    )


@pytest.fixture
def ohlcv_df():
    n = 100
    np.random.seed(7)
    c = 100 * np.cumprod(1 + np.random.normal(0.001, 0.015, n))
    idx = pd.date_range(end=date.today(), periods=n, freq="B")
    return pd.DataFrame(
        {"open": c, "high": c*1.01, "low": c*0.99, "close": c, "volume": np.ones(n)*1e6},
        index=idx,
    )


# ---------------------------------------------------------------------------
# IBKRBridge tests
# ---------------------------------------------------------------------------

class TestIBKRBridge:

    def test_port_map_paper_tws(self):
        from martin_quant.broker import IBKRBridge
        b = IBKRBridge(paper=True, use_gateway=False)
        assert b.port == 7497

    def test_port_map_live_tws(self):
        from martin_quant.broker import IBKRBridge
        b = IBKRBridge(paper=False, use_gateway=False)
        assert b.port == 7496

    def test_port_map_paper_gateway(self):
        from martin_quant.broker import IBKRBridge
        b = IBKRBridge(paper=True, use_gateway=True)
        assert b.port == 4002

    def test_port_map_live_gateway(self):
        from martin_quant.broker import IBKRBridge
        b = IBKRBridge(paper=False, use_gateway=True)
        assert b.port == 4001

    def test_order_id_increment(self, bridge_sim):
        id1 = bridge_sim._get_next_order_id()
        id2 = bridge_sim._get_next_order_id()
        assert id2 == id1 + 1

    def test_submit_market_sim(self, bridge_sim):
        oid = bridge_sim.submit_market("NVDA", "BUY", 10)
        assert oid >= 0
        rec = bridge_sim.get_order(oid)
        assert rec is not None
        assert rec.symbol == "NVDA"
        assert rec.action == "BUY"
        assert rec.order_type == "MKT"

    def test_submit_limit_sim(self, bridge_sim):
        oid = bridge_sim.submit_limit("AMD", "BUY", 20, 120.5)
        rec = bridge_sim.get_order(oid)
        assert rec.entry_price == 120.5
        assert rec.order_type == "LMT"

    def test_submit_bracket_sim(self, bridge_sim):
        oid = bridge_sim.submit_bracket(
            symbol="NVDA", action="BUY", quantity=10,
            entry_price=500.0, stop_price=492.0, target_price=516.0,
        )
        rec = bridge_sim.get_order(oid)
        assert rec is not None
        assert rec.stop_price == 492.0
        assert rec.target_price == 516.0
        assert rec.order_type == "BRACKET"

    def test_cancel_order(self, bridge_sim):
        oid = bridge_sim.submit_market("META", "BUY", 5)
        bridge_sim.cancel_order(oid)
        assert bridge_sim.get_order(oid).status == "Cancelled"

    def test_open_orders_filter(self, bridge_sim):
        oid = bridge_sim.submit_market("TSLA", "BUY", 5)
        open_before = len(bridge_sim.open_orders)
        bridge_sim.cancel_order(oid)
        open_after = len(bridge_sim.open_orders)
        assert open_after < open_before

    def test_order_status_callback(self, bridge_sim):
        """Simulate orderStatus EWrapper callback."""
        oid = bridge_sim.submit_limit("AAPL", "BUY", 5, 220.0)
        bridge_sim.orderStatus(
            oid, "Filled", 5, 0, 219.95, 0, 0, 219.95, 1, "", 0
        )
        rec = bridge_sim.get_order(oid)
        assert rec.status == "Filled"
        assert rec.filled_qty == 5
        assert abs(rec.avg_fill_price - 219.95) < 0.01

    def test_portfolio_update_callback(self, bridge_sim):
        contract = MagicMock()
        contract.symbol = "NVDA"
        bridge_sim.updatePortfolio(contract, 10, 505.0, 5050.0, 500.0, 50.0, 0.0, "DU123")
        pos = bridge_sim.get_position("NVDA")
        assert pos is not None
        assert pos.quantity == 10
        assert pos.unrealized_pnl == 50.0


# ---------------------------------------------------------------------------
# OrderManager tests
# ---------------------------------------------------------------------------

class TestOrderManager:

    def test_execute_single_signal(self, order_manager, fake_signal):
        results = order_manager.execute_signals([fake_signal])
        assert len(results) == 1
        assert results[0].status == "submitted"
        assert results[0].symbol == "NVDA"

    def test_skip_existing_position(self, order_manager, fake_signal, bridge_sim):
        contract = MagicMock()
        contract.symbol = "NVDA"
        bridge_sim.updatePortfolio(contract, 5, 490.0, 2450.0, 490.0, 50.0, 0.0, "DU123")
        results = order_manager.execute_signals([fake_signal])
        # Should skip because NVDA already in portfolio
        skip = [r for r in results if r.reason == "already_in_portfolio"]
        assert len(skip) == 1

    def test_max_signals_limit(self, order_manager):
        signals = [
            MagicMock(symbol=f"SYM{i}", direction="long",
                      entry_price=100.0, stop_price=95.0, target_price=110.0,
                      shares=10, total_score=0.8)
            for i in range(10)
        ]
        results = order_manager.execute_signals(signals)
        submitted = [r for r in results if r.status == "submitted"]
        assert len(submitted) <= order_manager.max_signals

    def test_skip_short_signals(self, order_manager):
        short_sig = MagicMock(
            symbol="BYND", direction="short",
            entry_price=10.0, stop_price=11.0, target_price=7.0,
            shares=20, total_score=0.6,
        )
        results = order_manager.execute_signals([short_sig])
        assert results[0].status == "skipped"
        assert results[0].reason == "shorts_disabled"

    def test_orders_log_written(self, order_manager, fake_signal, tmp_path):
        order_manager.execute_signals([fake_signal])
        log_path = order_manager.orders_log
        assert os.path.exists(log_path)

    def test_execute_exit_no_position(self, order_manager):
        result = order_manager.execute_exit("GHOST", 10)
        assert result.status == "skipped"
        assert result.reason == "no_position"

    def test_execute_exit_with_position(self, order_manager, bridge_sim):
        contract = MagicMock()
        contract.symbol = "GOOG"
        bridge_sim.updatePortfolio(contract, 5, 150.0, 750.0, 150.0, 25.0, 0.0, "DU123")
        result = order_manager.execute_exit("GOOG", 3, exit_type="partial")
        assert result.status == "submitted"
        assert result.quantity == 3


# ---------------------------------------------------------------------------
# PositionMonitor tests
# ---------------------------------------------------------------------------

class TestPositionMonitor:

    def test_start_stop(self, bridge_sim, order_manager):
        from martin_quant.broker import PositionMonitor
        monitor = PositionMonitor(
            bridge=bridge_sim,
            order_manager=order_manager,
            ohlcv_getter=lambda s: None,
            interval=0.1,
        )
        monitor.start()
        assert monitor.is_running
        monitor.stop()
        assert not monitor.is_running

    def test_add_position(self, bridge_sim, order_manager):
        from martin_quant.broker import PositionMonitor
        monitor = PositionMonitor(
            bridge=bridge_sim,
            order_manager=order_manager,
            ohlcv_getter=lambda s: None,
        )
        monitor.add_position("NVDA", entry_price=500.0, stop_price=492.0)
        assert monitor.entry_prices["NVDA"] == 500.0
        assert monitor.stop_prices["NVDA"] == 492.0

    def test_update_regime(self, bridge_sim, order_manager):
        from martin_quant.broker import PositionMonitor
        monitor = PositionMonitor(
            bridge=bridge_sim,
            order_manager=order_manager,
            ohlcv_getter=lambda s: None,
        )
        monitor.update_regime("BEAR")
        assert monitor.regime == "BEAR"

    def test_check_no_positions(self, bridge_sim, order_manager, caplog):
        """Should not error when no positions exist."""
        from martin_quant.broker import PositionMonitor
        import logging
        monitor = PositionMonitor(
            bridge=bridge_sim,
            order_manager=order_manager,
            ohlcv_getter=lambda s: None,
        )
        with caplog.at_level(logging.DEBUG):
            monitor._check_positions()  # should not raise
        assert monitor._check_count == 1


import os
