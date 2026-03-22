"""Batch 34 review/report regression tests."""
from __future__ import annotations

import pytest


SAMPLE_CSV = """date,symbol,setup_type,sector,entry,exit_price,shares,pnl_dollars,pnl_pct,r_multiple,outcome,exit_reason,holding_days,regime,confirmation_mode,confirmation_bars,confirmation_reason,weekly_trend_state,gap_label
2026-03-03,NVDA,pullback,semiconductors,125.0,131.25,100,625,5.0,2.5,win,ema9_exit,3,BULL,bar_close,1,close_above_orb_high,BULL,gap_down_into_support
2026-03-04,AMD,breakout,semiconductors,95.0,93.1,200,-380,-2.0,-1.0,loss,stop_loss,1,BEAR,intrabar,0,anticipatory_break,BEAR,gap_up_into_resistance
2026-03-05,MSFT,pullback,technology,415.0,423.3,50,415,2.0,1.5,win,target_hit,2,BULL,bar_close,1,close_above_orb_high,BULL,gap_down_into_support
2026-03-06,AAPL,breakout,technology,220.0,216.0,80,-320,-1.8,-1.0,loss,stop_loss,1,BEAR,intrabar,0,anticipatory_break,BEAR,gap_up_into_resistance
"""


@pytest.fixture
def tmp_csv(tmp_path):
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(SAMPLE_CSV, encoding="utf-8")
    return str(csv_file)


def test_trade_reviewer_groups_by_transcript_context(tmp_csv):
    from martin_quant.review.trade_reviewer import TradeReviewer

    reviewer = TradeReviewer(csv_path=tmp_csv)
    result = reviewer.review(start_date="2026-03-03", end_date="2026-03-06")

    assert result.by_confirmation == {"bar_close": 4.0, "intrabar": -2.0}
    assert result.by_weekly_trend == {"BULL": 4.0, "BEAR": -2.0}
    assert result.by_gap_label == {
        "gap_down_into_support": 4.0,
        "gap_up_into_resistance": -2.0,
    }
    assert any("Confirmation bucket 'intrabar' is net negative" in note for note in result.improvement_notes)
    assert any("Weekly trend bucket 'BEAR' underperformed" in note for note in result.improvement_notes)
    assert any("Gap context 'gap_up_into_resistance' underperformed" in note for note in result.improvement_notes)


def test_trade_reviewer_summary_lists_transcript_sections(tmp_csv):
    from martin_quant.review.trade_reviewer import TradeReviewer

    reviewer = TradeReviewer(csv_path=tmp_csv)
    result = reviewer.review(start_date="2026-03-03", end_date="2026-03-06")
    summary = result.summary()

    assert "By Confirmation:" in summary
    assert "By Weekly Trend:" in summary
    assert "By Gap Label:" in summary
    assert "bar_close" in summary
    assert "gap_up_into_resistance" in summary


def test_weekly_report_markdown_contains_transcript_context_tables(tmp_csv, tmp_path):
    from martin_quant.review.weekly_report import WeeklyReport

    report = WeeklyReport(csv_path=tmp_csv, output_dir=str(tmp_path / "reports"))
    path = report.save_markdown(start_date="2026-03-03", end_date="2026-03-06")
    content = path.read_text(encoding="utf-8")

    assert "## By Confirmation" in content
    assert "| Confirmation | Total R |" in content
    assert "| bar_close | +4.00R |" in content
    assert "## By Weekly Trend" in content
    assert "| BULL | +4.00R |" in content
    assert "## By Gap Label" in content
    assert "| gap_up_into_resistance | -2.00R |" in content
