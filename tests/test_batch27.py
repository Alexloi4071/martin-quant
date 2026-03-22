from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from martin_quant.regime import BreadthParticipationSnapshot


def _scan_result(symbol: str, direction: str = "long", total_score: float = 0.85, sector_state: str = "STRONG"):
    return SimpleNamespace(
        symbol=symbol,
        direction=direction,
        setup_type="pullback" if direction == "long" else "short_resistance_reversal",
        total_score=total_score,
        trade_quality_state="GO",
        breadth_state="EXPANDING",
        sector_strength_state=sector_state,
        sector="Semiconductors" if direction == "long" else "Technology",
        entry_price=100.0 if direction == "long" else 100.0,
        stop_price=96.0 if direction == "long" else 104.0,
        target_price=112.0 if direction == "long" else 90.0,
        orb_signal=object() if direction == "long" else None,
        timing_signal=object() if direction == "short" else None,
        entry_note="avwap_support, breadth=expanding, sector_rs=strong" if direction == "long" else "short_bias, breadth=very_weak",
    )


class TestPositionSizerShortSupport:
    def test_size_handles_short_trade(self):
        from martin_quant.risk.position_sizer import PositionSizer

        result = PositionSizer().size(
            symbol="TSLA",
            entry_price=100.0,
            stop_price=104.0,
            equity=100_000,
            regime="BEAR",
            exposure_factor=0.8,
            direction="short",
        )

        assert result is not None
        assert result.shares > 0
        assert result.risk_pct > 0
        assert "short_position" in result.notes


class TestExecutionPlanner:
    def test_build_plan_creates_active_and_blocked_items(self, tmp_path):
        from martin_quant.execution import ExecutionPlanner, export_execution_plan_bundle

        planner = ExecutionPlanner()
        breadth = BreadthParticipationSnapshot(
            state="EXPANDING",
            universe_size=30,
            pct_above_ema21=0.8,
            pct_above_ema50=0.7,
            pct_bull_stack=0.5,
            leader_count=5,
            leader_ratio=0.16,
            exposure_factor=1.0,
            notes=[],
        )
        results = [
            _scan_result("NVDA", total_score=0.92, sector_state="STRONG"),
            _scan_result("AMD", total_score=0.84, sector_state="STRONG"),
            _scan_result("TSLA", direction="short", total_score=0.79, sector_state="WEAK"),
            SimpleNamespace(
                symbol="BAD",
                direction="long",
                setup_type="pullback",
                total_score=0.78,
                trade_quality_state="GO",
                breadth_state="EXPANDING",
                sector_strength_state="NEUTRAL",
                sector="Health Care",
                entry_price=None,
                stop_price=None,
                target_price=None,
                orb_signal=None,
                timing_signal=None,
                entry_note="",
            ),
        ]

        bundle = planner.build_plan(
            results=results,
            as_of="2026-03-20",
            equity=100_000,
            regime="BULL",
            trade_quality_state="GO",
            trade_quality_weight=1.0,
            breadth_snapshot=breadth,
            market_caps={"NVDA": 3e12, "AMD": 3e11, "TSLA": 8e11},
        )

        assert len(bundle.active_plans) == 3
        assert any(plan.direction == "short" for plan in bundle.active_plans)
        assert any(plan.block_reason == "missing_trade_levels" for plan in bundle.blocked_plans)
        assert bundle.summary()["planned_exposure_pct"] > 0

        paths = export_execution_plan_bundle(bundle, out_dir=str(tmp_path))
        assert Path(paths["json"]).exists()
        assert Path(paths["csv"]).exists()

        payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
        assert payload["summary"]["active_count"] == 3

    def test_build_plan_respects_max_new_trades(self):
        from martin_quant.execution import ExecutionPlanner, ExecutionPlannerConfig

        planner = ExecutionPlanner(ExecutionPlannerConfig(max_new_trades=1))
        breadth = BreadthParticipationSnapshot(
            state="SHRINKING",
            universe_size=10,
            pct_above_ema21=0.45,
            pct_above_ema50=0.4,
            pct_bull_stack=0.2,
            leader_count=1,
            leader_ratio=0.1,
            exposure_factor=0.6,
            notes=[],
        )
        bundle = planner.build_plan(
            results=[_scan_result("NVDA", total_score=0.9), _scan_result("AMD", total_score=0.88)],
            as_of="2026-03-20",
            equity=100_000,
            regime="WEAK_BULL",
            trade_quality_state="SELECTIVE",
            trade_quality_weight=0.8,
            breadth_snapshot=breadth,
            market_caps={"NVDA": 3e12, "AMD": 3e11},
        )

        assert len(bundle.active_plans) == 1
        assert any(plan.block_reason == "max_new_trades_reached" for plan in bundle.blocked_plans)
