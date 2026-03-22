"""weekly_report.py

Weekly report generator built on top of TradeReviewer.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
import logging

from martin_quant.review.trade_reviewer import ReviewResult, TradeReviewer

log = logging.getLogger(__name__)


class WeeklyReport:
    """Generate console, Markdown, and Telegram weekly review output."""

    def __init__(
        self,
        csv_path: str = "trades.csv",
        output_dir: str = "reports",
    ) -> None:
        self.reviewer = TradeReviewer(csv_path=csv_path)
        self.output_dir = Path(output_dir)
        self._result: Optional[ReviewResult] = None

    def generate(
        self,
        weeks: int = 1,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        print_report: bool = True,
    ) -> ReviewResult:
        self.reviewer.load()
        self._result = self.reviewer.review(
            weeks=weeks,
            start_date=start_date,
            end_date=end_date,
        )
        if print_report:
            print(self._result.summary())
        return self._result

    def save_markdown(
        self,
        weeks: int = 1,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Path:
        result = self.generate(
            weeks=weeks,
            start_date=start_date,
            end_date=end_date,
            print_report=False,
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.today().strftime("%Y-%m-%d")
        filename = self.output_dir / f"weekly_report_{date_str}.md"
        filename.write_text(self._to_markdown(result), encoding="utf-8")
        log.info("Weekly report saved: %s", filename)
        return filename

    def send_telegram(
        self,
        weeks: int = 1,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> None:
        try:
            from martin_quant.utils.alert_manager import AlertManager

            result = self.generate(
                weeks=weeks,
                start_date=start_date,
                end_date=end_date,
                print_report=False,
            )
            AlertManager().send_message(self._to_telegram(result))
            log.info("Weekly report sent to Telegram")
        except Exception as exc:
            log.warning("send_telegram failed: %s", exc)

    def _to_markdown(self, r: ReviewResult) -> str:
        lines = [
            "# Martin Quant Weekly Report",
            f"**Period:** {r.period_label}  ",
            f"**Generated:** {datetime.today().strftime('%Y-%m-%d %H:%M')}\n",
            "## Performance Summary\n",
            "| Metric | Value |",
            "|---|---|",
            f"| Trades | {r.n_trades} ({r.n_wins}W / {r.n_losses}L) |",
            f"| Win Rate | {r.win_rate*100:.1f}% |",
            f"| Avg R | {r.avg_r:+.2f}R |",
            f"| Total R | {r.total_r:+.2f}R |",
            f"| Net P&L | ${r.total_pnl:,.0f} |",
            f"| Profit Factor | {r.profit_factor:.2f} |",
            f"| Expectancy | {r.expectancy:+.3f}R |",
            f"| Max Drawdown | {r.max_drawdown_r:.2f}R |",
            f"| Best Trade | {r.best_trade} |",
            f"| Worst Trade | {r.worst_trade} |\n",
        ]

        if r.by_setup:
            lines.append("## By Setup Type\n")
            lines.append("| Setup | Trades | Win% | Avg R | Total R | PF |")
            lines.append("|---|---|---|---|---|---|")
            for setup in sorted(r.by_setup, key=lambda item: item.total_r, reverse=True):
                lines.append(
                    f"| {setup.setup_type} | {setup.n_trades} "
                    f"| {setup.win_rate*100:.0f}% "
                    f"| {setup.avg_r:+.2f}R "
                    f"| {setup.total_r:+.2f}R "
                    f"| {setup.profit_factor:.2f} |"
                )
            lines.append("")

        self._append_metric_table(
            lines,
            title="By Sector",
            column_label="Sector",
            metric_label="Total R",
            values=r.by_sector,
            formatter=lambda value: f"{value:+.2f}R",
        )
        self._append_metric_table(
            lines,
            title="By Regime",
            column_label="Regime",
            metric_label="Win Rate",
            values=r.by_regime,
            formatter=lambda value: f"{value*100:.1f}%",
        )
        self._append_metric_table(
            lines,
            title="By Confirmation",
            column_label="Confirmation",
            metric_label="Total R",
            values=r.by_confirmation,
            formatter=lambda value: f"{value:+.2f}R",
        )
        self._append_metric_table(
            lines,
            title="By Weekly Trend",
            column_label="Weekly Trend",
            metric_label="Total R",
            values=r.by_weekly_trend,
            formatter=lambda value: f"{value:+.2f}R",
        )
        self._append_metric_table(
            lines,
            title="By Gap Label",
            column_label="Gap Label",
            metric_label="Total R",
            values=r.by_gap_label,
            formatter=lambda value: f"{value:+.2f}R",
        )

        if r.improvement_notes:
            lines.append("## Improvement Notes\n")
            for note in r.improvement_notes:
                lines.append(f"- {note}")
            lines.append("")

        return "\n".join(lines)

    def _to_telegram(self, r: ReviewResult) -> str:
        emoji = "[+]" if r.total_r > 0 else "[-]"
        lines = [
            f"{emoji} Weekly Review | {r.period_label}",
            f"Trades: {r.n_trades} ({r.n_wins}W/{r.n_losses}L)  WR: {r.win_rate*100:.0f}%",
            f"Avg R: {r.avg_r:+.2f}  Total R: {r.total_r:+.2f}  PnL: ${r.total_pnl:,.0f}",
            f"PF: {r.profit_factor:.2f}  Expect: {r.expectancy:+.3f}R",
            f"Best: {r.best_trade}  Worst: {r.worst_trade}",
        ]
        if r.improvement_notes:
            lines.append(f"\nNote: {r.improvement_notes[0]}")
        return "\n".join(lines)

    @staticmethod
    def _append_metric_table(
        lines: list[str],
        title: str,
        column_label: str,
        metric_label: str,
        values: dict,
        formatter: Callable[[float], str],
    ) -> None:
        if not values:
            return
        lines.append(f"## {title}\n")
        lines.append(f"| {column_label} | {metric_label} |")
        lines.append("|---|---|")
        for key, value in sorted(values.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"| {key} | {formatter(value)} |")
        lines.append("")
