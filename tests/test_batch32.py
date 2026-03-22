from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
        telegram_token="token",
        telegram_chat_id="chat",
    )


class TestOrderManagerConfirmationOutputs:
    def test_orders_log_writes_confirmation_fields(self, plan_order_manager):
        plan = SimpleNamespace(
            symbol="NVDA",
            direction="long",
            entry_price=100.0,
            stop_price=96.0,
            target_price=112.0,
            shares=10,
            total_score=0.9,
            active=True,
            priority_tier="A",
            execution_style="orb_breakout_after_first_15m",
            entry_confirmation={
                "source": "orb",
                "mode": "bar_close",
                "required_bars": 1,
                "reason": "1/1 closes > OR_high_breakout(105.10)",
            },
        )

        plan_order_manager.execute_plans([plan])

        rows = list(csv.DictReader(Path(plan_order_manager.orders_log).open("r", encoding="utf-8")))
        assert rows
        assert rows[0]["confirmation_mode"] == "bar_close"
        assert rows[0]["confirmation_bars"] == "1"
        assert "OR_high_breakout" in rows[0]["confirmation_reason"]

    def test_telegram_message_includes_confirmation_reason(self, plan_order_manager):
        result = SimpleNamespace(
            status="submitted",
            action="BUY",
            symbol="NVDA",
            quantity=10,
            entry_price=100.0,
            stop_price=96.0,
            target_price=112.0,
            reason="plan_tier=A style=orb_breakout_after_first_15m",
            confirmation_mode="bar_close",
            confirmation_bars=1,
            confirmation_reason="1/1 closes > OR_high_breakout(105.10)",
        )

        with patch("requests.post") as post_mock:
            plan_order_manager._notify_telegram([result])

        payload = post_mock.call_args.kwargs["json"]["text"]
        assert "confirm=bar_close:1" in payload
        assert "OR_high_breakout" in payload


class TestRunLiveV2ConfirmationFormatting:
    def test_format_plan_confirmation(self):
        from martin_quant.scripts.run_live_v2 import _format_plan_confirmation

        plan = SimpleNamespace(
            entry_confirmation_mode="bar_close",
            entry_confirmation_bars=1,
            entry_confirmation_reason="1/1 closes > OR_high_breakout(105.10)",
        )

        text = _format_plan_confirmation(plan)
        assert text == "bar_close:1 1/1 closes > OR_high_breakout(105.10)"

    def test_format_execution_confirmation(self):
        from martin_quant.scripts.run_live_v2 import _format_execution_confirmation

        result = SimpleNamespace(
            confirmation_mode="bar_close",
            confirmation_bars=2,
            confirmation_reason="2/2 closes < EMA9(104.50)",
        )

        text = _format_execution_confirmation(result)
        assert text == "bar_close:2 2/2 closes < EMA9(104.50)"
