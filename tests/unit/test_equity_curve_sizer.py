"""tests/unit/test_equity_curve_sizer.py

Unit tests for risk/equity_curve_sizer.py
"""
import pytest
from martin_quant.risk.equity_curve_sizer import EquityCurveSizer, EquityCurveSizerConfig


class TestEquityCurveSizer:
    def setup_method(self):
        self.sizer = EquityCurveSizer(
            initial_equity=100_000,
            config=EquityCurveSizerConfig(
                base_risk_pct=0.5,
                reduced_risk_pct=0.25,
                min_risk_pct=0.1,
                consecutive_loss_threshold=3,
                equity_ma_period=10,
                bull_boost_pct=0.0,   # no boost for cleaner tests
            ),
        )

    def test_base_risk_at_start(self):
        risk = self.sizer.get_risk_pct(100_000, "bull")
        assert risk == pytest.approx(0.5, abs=0.01)

    def test_reduced_after_consecutive_losses(self):
        # Record 3 consecutive losses
        equity = 100_000
        for _ in range(3):
            equity -= 500
            self.sizer.record_trade(equity, won=False)
        risk = self.sizer.get_risk_pct(equity, "neutral")
        assert risk <= 0.25

    def test_below_equity_ma_reduces_risk(self):
        # Push equity below its MA
        sizer = EquityCurveSizer(initial_equity=100_000)
        # Record 10 losing trades to drop equity below MA
        equity = 100_000
        for _ in range(12):
            equity -= 1_000
            sizer.record_trade(equity, won=False)
        risk = sizer.get_risk_pct(equity, "neutral")
        assert risk < 0.5

    def test_bear_regime_reduces_risk(self):
        risk_bull = self.sizer.get_risk_pct(100_000, "bull")
        risk_bear = self.sizer.get_risk_pct(100_000, "bear")
        assert risk_bear < risk_bull

    def test_recovery_after_wins(self):
        sizer = EquityCurveSizer(initial_equity=100_000)
        equity = 100_000
        for _ in range(3):
            equity -= 500
            sizer.record_trade(equity, won=False)
        assert sizer.get_risk_pct(equity, "neutral") <= 0.25

        # Win 3 times in a row
        for _ in range(5):
            equity += 1_000
            sizer.record_trade(equity, won=True)
        risk_after_recovery = sizer.get_risk_pct(equity, "bull")
        assert risk_after_recovery >= 0.4  # back to near full
