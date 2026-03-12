"""weekly_report.py

Weekly Report Generator
========================
從 TradeReviewer 結果生成完整週報，支援：
  - 純文字 (print)
  - Markdown 文件
  - Telegram 訊息（via alert_manager）

Usage:
    from martin_quant.review.weekly_report import WeeklyReport

    report = WeeklyReport(csv_path="trades.csv")
    report.generate()          # 本週，印到 stdout
    report.generate(weeks=4)   # 近4週
    report.save_markdown()     # 存成 weekly_report_YYYY-MM-DD.md
    report.send_telegram()     # 傳送到 Telegram
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional
import logging

from martin_quant.review.trade_reviewer import TradeReviewer, ReviewResult

log = logging.getLogger(__name__)


class WeeklyReport:
    """
    週報生成器。

    Parameters
    ----------
    csv_path : str
        trades.csv 路徑
    output_dir : str
        週報 Markdown 輸出目錄，預設 'reports/'
    """

    def __init__(
        self,
        csv_path: str = "trades.csv",
        output_dir: str = "reports",
    ) -> None:
        self.reviewer   = TradeReviewer(csv_path=csv_path)
        self.output_dir = Path(output_dir)
        self._result: Optional[ReviewResult] = None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def generate(
        self,
        weeks: int = 1,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        print_report: bool = True,
    ) -> ReviewResult:
        """產生週報並（可選）印到 stdout"""
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
        """生成週報並存成 Markdown 文件，回傳路徑"""
        result = self.generate(
            weeks=weeks,
            start_date=start_date,
            end_date=end_date,
            print_report=False,
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        date_str  = datetime.today().strftime("%Y-%m-%d")
        filename  = self.output_dir / f"weekly_report_{date_str}.md"
        content   = self._to_markdown(result)
        filename.write_text(content, encoding="utf-8")
        log.info("Weekly report saved: %s", filename)
        return filename

    def send_telegram(
        self,
        weeks: int = 1,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> None:
        """透過 alert_manager 傳送 Telegram 週報摘要"""
        try:
            from martin_quant.utils.alert_manager import AlertManager
            result = self.generate(
                weeks=weeks,
                start_date=start_date,
                end_date=end_date,
                print_report=False,
            )
            msg = self._to_telegram(result)
            am  = AlertManager()
            am.send_message(msg)
            log.info("Weekly report sent to Telegram")
        except Exception as e:
            log.warning("send_telegram failed: %s", e)

    # -----------------------------------------------------------------------
    # Formatters
    # -----------------------------------------------------------------------

    def _to_markdown(self, r: ReviewResult) -> str:
        """生成 Markdown 格式週報"""
        lines = [
            f"# Martin Quant Weekly Report",
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
            for s in sorted(r.by_setup, key=lambda x: x.total_r, reverse=True):
                lines.append(
                    f"| {s.setup_type} | {s.n_trades} "
                    f"| {s.win_rate*100:.0f}% "
                    f"| {s.avg_r:+.2f}R "
                    f"| {s.total_r:+.2f}R "
                    f"| {s.profit_factor:.2f} |"
                )
            lines.append("")

        if r.by_sector:
            lines.append("## By Sector\n")
            lines.append("| Sector | Total R |")
            lines.append("|---|---|")
            for sec, tr in sorted(r.by_sector.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"| {sec} | {tr:+.2f}R |")
            lines.append("")

        if r.by_regime:
            lines.append("## By Regime\n")
            lines.append("| Regime | Win Rate |")
            lines.append("|---|---|")
            for reg, wr in r.by_regime.items():
                lines.append(f"| {reg} | {wr*100:.1f}% |")
            lines.append("")

        if r.improvement_notes:
            lines.append("## Improvement Notes\n")
            for note in r.improvement_notes:
                lines.append(f"- {note}")
            lines.append("")

        return "\n".join(lines)

    def _to_telegram(self, r: ReviewResult) -> str:
        """Telegram 格式（簡短）"""
        emoji = "📈" if r.total_r > 0 else "📉"
        lines = [
            f"{emoji} *Weekly Review* — {r.period_label}",
            f"Trades: {r.n_trades} ({r.n_wins}W/{r.n_losses}L)  WR: {r.win_rate*100:.0f}%",
            f"Avg R: {r.avg_r:+.2f}  Total R: {r.total_r:+.2f}  PnL: ${r.total_pnl:,.0f}",
            f"PF: {r.profit_factor:.2f}  Expect: {r.expectancy:+.3f}R",
            f"Best: {r.best_trade}  Worst: {r.worst_trade}",
        ]
        if r.improvement_notes:
            lines.append(f"\n📌 {r.improvement_notes[0]}")
        return "\n".join(lines)
