"""weekly_report.py

Weekly Performance Report
=========================
Martin Luk 策略—自動產生週報:
  - 勝/輸統計
  - 平均 R 倍數
  - 最佳/最差交易
  - Setup 类型分析
  - 板塊/主題表現
  - 出場型態分析

Usage:
    from martin_quant.review.weekly_report import WeeklyReport
    report = WeeklyReport(trades_file="trades.csv")
    report.print_report(weeks=4)
    path = report.save_report(weeks=4)
"""
from __future__ import annotations

import csv
import datetime
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class WeeklyReport:
    """
    從 trades.csv 讀取交易展示週報。

    Parameters
    ----------
    trades_file : str
        CSV 路徑
    """

    def __init__(self, trades_file: str = "trades.csv") -> None:
        self.trades_file = trades_file

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def print_report(self, weeks: int = 4) -> None:
        """Print formatted report to stdout"""
        report = self._build(weeks)
        print(report)

    def save_report(self, weeks: int = 4) -> str:
        """Save report to markdown file, return path"""
        report = self._build(weeks)
        today = datetime.date.today()
        path  = f"review_{today}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        log.info("Report saved: %s", path)
        return path

    # ------------------------------------------------------------------
    # Build report
    # ------------------------------------------------------------------

    def _build(self, weeks: int = 4) -> str:
        trades = self._load_closed_trades(weeks)
        if not trades:
            return f"No closed trades in the last {weeks} weeks."

        today = datetime.date.today()
        lines = [
            f"# Martin Quant Weekly Review",
            f"**Period:** Last {weeks} weeks | **Generated:** {today}",
            "",
        ]

        # --- Overview ---
        r_list    = [t["r_multiple"] for t in trades]
        pnl_list  = [t["pnl_dollars"] for t in trades]
        wins      = [r for r in r_list if r > 0]
        losses    = [r for r in r_list if r <= 0]
        win_rate  = len(wins) / len(r_list) * 100 if r_list else 0
        total_r   = sum(r_list)
        avg_win   = sum(wins) / len(wins) if wins else 0
        avg_loss  = sum(losses) / len(losses) if losses else 0
        exp_val   = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)
        gross_p   = sum(p for p in pnl_list if p > 0)
        gross_l   = abs(sum(p for p in pnl_list if p < 0))
        pf        = gross_p / gross_l if gross_l > 0 else float("inf")

        lines += [
            "## Overview",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Trades | {len(trades)} |",
            f"| Win Rate | {win_rate:.1f}% |",
            f"| Total R | {total_r:+.2f}R |",
            f"| Avg Win | {avg_win:+.2f}R |",
            f"| Avg Loss | {avg_loss:+.2f}R |",
            f"| Expectancy | {exp_val:+.2f}R |",
            f"| Profit Factor | {pf:.2f} |",
            f"| Total P&L | ${sum(pnl_list):,.0f} |",
            "",
        ]

        # --- Setup breakdown ---
        setup_stats: dict[str, list] = defaultdict(list)
        for t in trades:
            setup_stats[t["setup_type"]].append(t["r_multiple"])

        lines += ["## Setup Breakdown", "| Setup | Trades | Win% | Avg R |", "|-------|--------|------|-------|",]
        for setup, rs in sorted(setup_stats.items(), key=lambda x: sum(x[1]), reverse=True):
            w = len([r for r in rs if r > 0])
            lines.append(
                f"| {setup} | {len(rs)} | {w/len(rs)*100:.0f}% | {sum(rs)/len(rs):+.2f}R |"
            )
        lines.append("")

        # --- Sector breakdown ---
        sector_stats: dict[str, list] = defaultdict(list)
        for t in trades:
            sector_stats[t.get("sector", "unknown")].append(t["r_multiple"])

        if sector_stats:
            lines += ["## Sector Performance", "| Sector | Trades | Total R |", "|--------|--------|--------|",]
            for sec, rs in sorted(sector_stats.items(), key=lambda x: sum(x[1]), reverse=True):
                lines.append(f"| {sec} | {len(rs)} | {sum(rs):+.2f}R |")
            lines.append("")

        # --- Exit type analysis ---
        exit_stats: dict[str, list] = defaultdict(list)
        for t in trades:
            exit_stats[t.get("exit_type", "unknown")].append(t["r_multiple"])

        lines += ["## Exit Type Analysis", "| Exit Type | Count | Avg R |", "|-----------|-------|-------|",]
        for etype, rs in sorted(exit_stats.items(), key=lambda x: len(x[1]), reverse=True):
            lines.append(f"| {etype} | {len(rs)} | {sum(rs)/len(rs):+.2f}R |")
        lines.append("")

        # --- Best & Worst ---
        sorted_trades = sorted(trades, key=lambda t: t["r_multiple"], reverse=True)
        lines.append("## Best Trades")
        for t in sorted_trades[:3]:
            lines.append(
                f"- **{t['symbol']}** `{t['setup_type']}` "
                f"{t['entry_date']} → {t['exit_date']} "
                f"R={t['r_multiple']:+.2f} P&L=${t['pnl_dollars']:,.0f}"
            )
        lines.append("")

        lines.append("## Worst Trades")
        for t in sorted_trades[-3:]:
            lines.append(
                f"- **{t['symbol']}** `{t['setup_type']}` "
                f"{t['entry_date']} → {t['exit_date']} "
                f"R={t['r_multiple']:+.2f} P&L=${t['pnl_dollars']:,.0f}"
            )
        lines.append("")

        # --- Lessons (auto-generated) ---
        lines.append("## Auto Insights")
        if win_rate < 40:
            lines.append("- ⚠️ Win rate below 40% — review entry criteria")
        if avg_win < 1.5:
            lines.append("- ⚠️ Average win < 1.5R — let winners run longer")
        if avg_loss < -1.5:
            lines.append("- ⚠️ Average loss > 1.5R — check stop placement")
        best_setup = max(setup_stats, key=lambda s: sum(setup_stats[s]))
        lines.append(f"- ✅ Best performing setup: **{best_setup}** ({sum(setup_stats[best_setup]):+.1f}R)")
        if exp_val > 0:
            lines.append(f"- ✅ Positive expectancy: {exp_val:+.2f}R per trade")
        else:
            lines.append(f"- ❌ Negative expectancy: {exp_val:+.2f}R — strategy review needed")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_closed_trades(self, weeks: int) -> list[dict]:
        """Load closed trades from last N weeks"""
        if not Path(self.trades_file).exists():
            log.warning("trades_file not found: %s", self.trades_file)
            return []

        cutoff = datetime.date.today() - datetime.timedelta(weeks=weeks)
        result = []

        with open(self.trades_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") != "closed":
                    continue
                try:
                    exit_date = datetime.date.fromisoformat(row["exit_date"])
                    if exit_date < cutoff:
                        continue
                    result.append({
                        "symbol":     row["symbol"],
                        "setup_type": row["setup_type"],
                        "sector":     row.get("sector", ""),
                        "theme":      row.get("theme", ""),
                        "entry_date": row["entry_date"],
                        "exit_date":  row["exit_date"],
                        "exit_type":  row.get("exit_type", ""),
                        "r_multiple": float(row.get("r_multiple", 0)),
                        "pnl_dollars": float(row.get("pnl_dollars", 0)),
                    })
                except Exception:
                    continue
        return result
