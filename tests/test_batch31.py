from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from martin_quant.regime import BreadthParticipationSnapshot


def _scan_result_with_confirmation(symbol: str, direction: str = "long"):
    if direction == "long":
        orb_signal = SimpleNamespace(
            confirmation_mode="bar_close",
            confirmation_bars=1,
            confirmation_reason="1/1 closes > OR_high_breakout(105.10)",
        )
        timing_signal = None
    else:
        orb_signal = None
        timing_signal = SimpleNamespace(
            context={
                "entry_confirmation": {
                    "phase": "entry",
                    "trade_direction": "short",
                    "relation": "below",
                    "required_bars": 1,
                    "reason": "1/1 closes < retest_low_break(95.00)",
                }
            }
        )

    return SimpleNamespace(
        symbol=symbol,
        direction=direction,
        setup_type="pullback" if direction == "long" else "short_resistance_reversal",
        total_score=0.9,
        trade_quality_state="GO",
        breadth_state="EXPANDING",
        sector_strength_state="STRONG" if direction == "long" else "WEAK",
        sector="Semiconductors" if direction == "long" else "Technology",
        entry_price=100.0,
        stop_price=96.0 if direction == "long" else 104.0,
        target_price=112.0 if direction == "long" else 90.0,
        orb_signal=orb_signal,
        timing_signal=timing_signal,
        entry_note="weekly=bull, avwap_support" if direction == "long" else "weekly=bear, short_bias",
    )


class TestExecutionPlanConfirmationMetadata:
    def test_build_plan_preserves_entry_confirmation(self, tmp_path):
        from martin_quant.execution import ExecutionPlanner, export_execution_plan_bundle

        planner = ExecutionPlanner()
        breadth = BreadthParticipationSnapshot(
            state="EXPANDING",
            universe_size=20,
            pct_above_ema21=0.8,
            pct_above_ema50=0.7,
            pct_bull_stack=0.6,
            leader_count=4,
            leader_ratio=0.2,
            exposure_factor=1.0,
            notes=[],
        )
        bundle = planner.build_plan(
            results=[_scan_result_with_confirmation("NVDA", "long"), _scan_result_with_confirmation("TSLA", "short")],
            as_of="2026-03-21",
            equity=100_000,
            regime="BULL",
            trade_quality_state="GO",
            trade_quality_weight=1.0,
            breadth_snapshot=breadth,
            market_caps={"NVDA": 3e12, "TSLA": 8e11},
        )

        assert len(bundle.active_plans) == 2
        assert bundle.summary()["confirmed_entry_count"] == 2
        assert bundle.active_plans[0].entry_confirmation is not None
        assert bundle.active_plans[0].entry_confirmation_reason
        assert any("entry_confirmation=" in note for note in bundle.active_plans[0].notes)

        paths = export_execution_plan_bundle(bundle, out_dir=str(tmp_path))
        payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
        assert payload["active_plans"][0]["entry_confirmation"] is not None


class TestPositionMonitorConfirmationMetadata:
    def test_action_log_keeps_entry_and_exit_confirmation(self):
        from martin_quant.broker import IBKRBridge, OrderManager, PositionMonitor
        from martin_quant.risk.exit_manager import ExitSignal

        bridge = IBKRBridge(paper=True)
        bridge._connected.set()
        bridge._next_order_id = 200
        mgr = OrderManager(bridge=bridge, equity=100_000, dry_run=True, allow_shorts=True)
        monitor = PositionMonitor(
            bridge=bridge,
            order_manager=mgr,
            ohlcv_getter=lambda symbol: pd.DataFrame({"close": [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]}),
        )
        plan = SimpleNamespace(
            symbol="NVDA",
            direction="long",
            entry_price=100.0,
            stop_price=95.0,
            target_price=115.0,
            shares=10,
            entry_confirmation={
                "source": "orb",
                "mode": "bar_close",
                "required_bars": 1,
                "reason": "1/1 closes > OR_high_breakout(105.10)",
            },
        )
        monitor.register_execution_plan(plan)

        contract = MagicMock()
        contract.symbol = "NVDA"
        bridge.updatePortfolio(contract, 10, 100.0, 1000.0, 100.0, 0.0, 0.0, "DU123")
        monitor.exit_mgr.evaluate = MagicMock(return_value=ExitSignal(
            symbol="NVDA",
            should_exit=True,
            exit_type="ema9_close_confirm",
            exit_pct=1.0,
            exit_price=106.0,
            reason="2 close(s) below EMA9 confirmed exit",
            r_current=1.5,
            urgency="next_open",
            confirmation={
                "phase": "exit",
                "trade_direction": "long",
                "relation": "below",
                "required_bars": 2,
                "reason": "2/2 closes < EMA9(104.50)",
            },
        ))

        monitor._check_one("NVDA", bridge.get_position("NVDA"))

        assert monitor.action_log
        last = monitor.action_log[-1]
        assert last["entry_confirmation"]["source"] == "orb"
        assert last["exit_confirmation"]["phase"] == "exit"
        assert last["action"] == "exit_ema9_close_confirm"
