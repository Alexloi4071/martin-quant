from __future__ import annotations

import pytest

from martin_quant.risk.position_sizer import PositionSizer, PositionSizerConfig, PositionSizeResult


class TestPositionSizer:
    @pytest.fixture()
    def sizer(self) -> PositionSizer:
        cfg = PositionSizerConfig(
            per_trade_risk_pct=1.0,
            max_position_pct=25.0,
            max_position_small_cap_pct=15.0,
            small_cap_threshold_mcap=2_000_000_000.0,
        )
        return PositionSizer(config=cfg)

    def test_basic_size(self, sizer: PositionSizer) -> None:
        result = sizer.size(
            symbol="TEST",
            equity=100_000.0,
            entry_price=50.0,
            stop_price=47.5,
        )
        assert result is not None
        assert isinstance(result, PositionSizeResult)
        # risk per share = 2.5, risk dollars = 1000 => 400 shares
        assert result.shares == 400
        assert result.risk_per_share == pytest.approx(2.5)

    def test_position_capped_by_max_pct(self, sizer: PositionSizer) -> None:
        result = sizer.size(
            symbol="TEST",
            equity=100_000.0,
            entry_price=50.0,
            stop_price=49.99,
        )
        assert result is not None
        assert result.position_pct_of_equity <= 25.0 + 1e-6

    def test_small_cap_lower_cap(self, sizer: PositionSizer) -> None:
        result_normal = sizer.size(
            symbol="BIG", equity=100_000.0,
            entry_price=50.0, stop_price=47.5,
            market_cap=10_000_000_000.0,
        )
        result_small = sizer.size(
            symbol="SMALL", equity=100_000.0,
            entry_price=50.0, stop_price=47.5,
            market_cap=500_000_000.0,
        )
        assert result_small is not None and result_normal is not None
        assert result_small.position_value <= result_normal.position_value

    def test_returns_none_when_stop_above_entry(self, sizer: PositionSizer) -> None:
        result = sizer.size(
            symbol="BAD",
            equity=100_000.0,
            entry_price=50.0,
            stop_price=55.0,
        )
        assert result is None

    def test_to_dict_keys(self, sizer: PositionSizer) -> None:
        result = sizer.size(
            symbol="X", equity=100_000.0,
            entry_price=50.0, stop_price=47.5,
        )
        assert result is not None
        d = result.to_dict()
        for key in ("symbol", "shares", "position_value", "risk_per_trade", "entry_price"):
            assert key in d
