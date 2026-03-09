from __future__ import annotations

import pytest

from martin_quant.backtest.performance import compute_performance
from martin_quant.backtest.trade_simulator import SimulatedTrade


def _make_trade(
    symbol: str = "TEST",
    r_multiple: float = 1.0,
    pnl: float = 500.0,
    holding_days: int = 5,
    exit_reason: str = "target",
    setup_type: str = "pullback",
    trigger_type: str = "reclaim",
) -> SimulatedTrade:
    entry = 100.0
    exit_p = entry + r_multiple * 2.5
    return SimulatedTrade(
        symbol=symbol,
        entry_date="2024-01-01",
        exit_date="2024-01-10",
        entry_price=entry,
        exit_price=exit_p,
        stop_price=97.5,
        shares=200,
        pnl=pnl,
        pnl_pct=r_multiple * 2.5,
        r_multiple=r_multiple,
        exit_reason=exit_reason,
        holding_days=holding_days,
        trigger_type=trigger_type,
        setup_type=setup_type,
    )


class TestComputePerformance:
    def test_empty_trades(self) -> None:
        metrics = compute_performance([])
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0.0
        assert metrics.total_pnl == 0.0

    def test_all_winners(self) -> None:
        trades = [_make_trade(r_multiple=3.0, pnl=600.0) for _ in range(10)]
        metrics = compute_performance(trades)
        assert metrics.win_rate == pytest.approx(1.0)
        assert metrics.avg_r == pytest.approx(3.0)
        assert metrics.total_pnl == pytest.approx(6000.0)

    def test_all_losers(self) -> None:
        trades = [_make_trade(r_multiple=-1.0, pnl=-200.0, exit_reason="stop") for _ in range(5)]
        metrics = compute_performance(trades)
        assert metrics.win_rate == pytest.approx(0.0)
        assert metrics.total_pnl == pytest.approx(-1000.0)

    def test_mixed_trades_win_rate(self) -> None:
        winners = [_make_trade(r_multiple=3.0, pnl=500.0) for _ in range(6)]
        losers  = [_make_trade(r_multiple=-1.0, pnl=-150.0, exit_reason="stop") for _ in range(4)]
        metrics = compute_performance(winners + losers)
        assert metrics.win_rate == pytest.approx(0.6)

    def test_profit_factor_gt_1_for_profitable(self) -> None:
        winners = [_make_trade(r_multiple=3.0, pnl=600.0) for _ in range(7)]
        losers  = [_make_trade(r_multiple=-1.0, pnl=-200.0, exit_reason="stop") for _ in range(3)]
        metrics = compute_performance(winners + losers)
        assert metrics.profit_factor > 1.0

    def test_to_dict_contains_keys(self) -> None:
        trades = [_make_trade() for _ in range(5)]
        metrics = compute_performance(trades)
        d = metrics.to_dict()
        for key in (
            "total_trades", "win_rate", "avg_r", "profit_factor",
            "total_pnl", "max_drawdown_pct", "sharpe_ratio",
        ):
            assert key in d

    def test_trades_by_exit_reason(self) -> None:
        trades = [
            _make_trade(exit_reason="target"),
            _make_trade(exit_reason="target"),
            _make_trade(exit_reason="stop"),
        ]
        metrics = compute_performance(trades)
        assert metrics.trades_by_exit.get("target") == 2
        assert metrics.trades_by_exit.get("stop") == 1
