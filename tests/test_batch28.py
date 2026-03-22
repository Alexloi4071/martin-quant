from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest


@pytest.fixture
def bridge_sim():
    from martin_quant.broker import IBKRBridge
    b = IBKRBridge(paper=True)
    b._connected.set()
    b._next_order_id = 100
    return b


@pytest.fixture
def plan_order_manager(bridge_sim, tmp_path):
    from martin_quant.broker import OrderManager
    return OrderManager(
        bridge=bridge_sim,
        equity=100_000,
        max_signals=3,
        dry_run=True,
        allow_shorts=True,
        orders_log=str(tmp_path / "orders_v2.csv"),
    )


def _plan(symbol: str, direction: str = "long", active: bool = True, shares: int = 10):
    return SimpleNamespace(
        symbol=symbol,
        direction=direction,
        setup_type="pullback" if direction == "long" else "short_retest_breakdown",
        entry_price=100.0,
        stop_price=96.0 if direction == "long" else 104.0,
        target_price=112.0 if direction == "long" else 90.0,
        shares=shares,
        total_score=0.86,
        active=active,
        block_reason="missing_trade_levels" if not active else "",
        priority_tier="A",
        execution_style="orb_breakout_after_first_15m" if direction == "long" else "short_retest_breakdown_after_bounce",
    )


class TestOrderManagerExecutionPlans:
    def test_execute_plans_submits_long_and_short(self, plan_order_manager):
        results = plan_order_manager.execute_plans([
            _plan("NVDA", "long"),
            _plan("TSLA", "short"),
        ])
        submitted = [r for r in results if r.status == "submitted"]
        assert len(submitted) == 2
        assert submitted[0].action == "BUY"
        assert submitted[1].action == "SELL"

    def test_execute_plans_skips_inactive_plan(self, plan_order_manager):
        results = plan_order_manager.execute_plans([_plan("BAD", active=False)])
        assert results[0].status == "skipped"
        assert results[0].reason == "missing_trade_levels"


class TestPositionMonitorExecutionPlanRegistration:
    def test_register_execution_plan_tracks_direction_and_levels(self, bridge_sim):
        from martin_quant.broker import OrderManager, PositionMonitor

        mgr = OrderManager(bridge=bridge_sim, equity=100_000, dry_run=True, allow_shorts=True)
        monitor = PositionMonitor(
            bridge=bridge_sim,
            order_manager=mgr,
            ohlcv_getter=lambda symbol: pd.DataFrame({"close": [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]}),
        )
        plan = _plan("TSLA", "short", shares=20)
        monitor.register_execution_plan(plan)

        tracked = monitor._positions_meta["TSLA"]
        assert tracked.direction == "short"
        assert tracked.stop_price == 104.0
        assert tracked.current_shares == 20

    def test_check_one_uses_exit_manager_path(self, bridge_sim):
        from martin_quant.broker import OrderManager, PositionMonitor
        from martin_quant.risk.exit_manager import ExitSignal

        mgr = OrderManager(bridge=bridge_sim, equity=100_000, dry_run=True, allow_shorts=True)
        monitor = PositionMonitor(
            bridge=bridge_sim,
            order_manager=mgr,
            ohlcv_getter=lambda symbol: pd.DataFrame({"close": [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]}),
        )
        contract = MagicMock()
        contract.symbol = "NVDA"
        bridge_sim.updatePortfolio(contract, 10, 100.0, 1000.0, 100.0, 0.0, 0.0, "DU123")
        monitor.add_position("NVDA", entry_price=100.0, stop_price=95.0, target_price=115.0, shares=10)
        monitor.exit_mgr.evaluate = MagicMock(return_value=ExitSignal(
            symbol="NVDA",
            should_exit=True,
            exit_type="partial_3r",
            exit_pct=0.5,
            exit_price=109.0,
            reason="Reached 3R target",
            r_current=3.0,
            urgency="eod",
        ))

        monitor._check_one("NVDA", bridge_sim.get_position("NVDA"))
        assert monitor.action_log
        assert monitor.action_log[-1]["action"] == "exit_partial_3r"
