"""weekly_reviewer.py

Weekly trade review and performance analyzer — Martin Luk style.

Every Friday / weekend Martin reviews:
  1. All trades taken that week (win rate, avg R, expectancy)
  2. Regime accuracy (did regime call match price action?)
  3. Setup breakdown (which setup type performed best?)
  4. Mistakes audit (stopped out at BE? Took profit too early?)
  5. Market leader health (leader count trend over the week)
  6. Next-week watchlist candidates

Produces a WeeklyReport dataclass + markdown report file.
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    date: str
    symbol: str
    setup_type: str
    direction: str          # long | short
    entry_price: float
    exit_price: float
    stop_price: float
    shares: int
    pnl_dollars: float
    r_realized: float       # actual R multiple
    exit_reason: str        # stop_hit | profit_target | ema9_violation | manual
    regime: str             # BULL | CAUTION | BEAR
    score: float
    notes: str

    @classmethod
    def from_dict(cls, d: dict) -> "TradeRecord":
        return cls(
            date=d.get("date", ""),
            symbol=d.get("symbol", ""),
            setup_type=d.get("setup_type", d.get("setup", "")),
            direction=d.get("direction", "long"),
            entry_price=float(d.get("entry_price", d.get("entry", 0))),
            exit_price=float(d.get("exit_price", d.get("exit", 0))),
            stop_price=float(d.get("stop_price", d.get("stop", 0))),
            shares=int(d.get("shares", 0)),
            pnl_dollars=float(d.get("pnl_dollars", d.get("pnl", 0))),
            r_realized=float(d.get("r_realized", d.get("r", 0))),
            exit_reason=d.get("exit_reason", ""),
            regime=d.get("regime", ""),
            score=float(d.get("score", 0)),
            notes=d.get("notes", ""),
        )


@dataclass
class SetupStats:
    setup_type: str
    trades: int
    winners: int
    win_rate: float
    avg_r: float
    total_pnl: float
    best_r: float
    worst_r: float


@dataclass
class WeeklyReport:
    week_start: str
    week_end: str
    total_trades: int
    winners: int
    losers: int
    breakeven: int
    win_rate: float
    avg_r: float
    expectancy: float       # win_rate * avg_win_r - loss_rate * avg_loss_r
    total_pnl: float
    max_drawdown: float     # worst consecutive loss streak in R
    setup_breakdown: list[SetupStats]
    regime_counts: dict[str, int]
    best_trade: Optional[TradeRecord]
    worst_trade: Optional[TradeRecord]
    mistake_count: int
    mistake_notes: list[str]
    grade: str              # A / B / C / D based on expectancy + execution
    markdown: str = ""

    def print_summary(self) -> None:
        print(self.markdown)


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------

class WeeklyReviewer:
    """
    Loads trade records from CSV and produces a WeeklyReport.

    CSV columns expected (flexible matching):
        date, symbol, setup_type, direction, entry_price, exit_price,
        stop_price, shares, pnl_dollars, r_realized, exit_reason, regime,
        score, notes

    Usage:
        reviewer = WeeklyReviewer(trades_csv="data/trades.csv")
        report   = reviewer.generate_weekly_report(week_end="2026-03-14")
        print(report.markdown)
        reviewer.save_report(report, output_dir="reports/")
    """

    def __init__(self, trades_csv: str = "data/trades.csv") -> None:
        self.trades_csv = trades_csv

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_trades(
        self,
        week_start: Optional[str] = None,
        week_end: Optional[str] = None,
    ) -> list[TradeRecord]:
        if not os.path.exists(self.trades_csv):
            log.warning("trades.csv not found: %s", self.trades_csv)
            return []
        records: list[TradeRecord] = []
        with open(self.trades_csv, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    t = TradeRecord.from_dict(row)
                    if week_start and t.date < week_start:
                        continue
                    if week_end and t.date > week_end:
                        continue
                    records.append(t)
                except Exception as e:
                    log.debug("Skip row: %s", e)
        return records

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    def _setup_breakdown(self, trades: list[TradeRecord]) -> list[SetupStats]:
        from collections import defaultdict
        buckets: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in trades:
            buckets[t.setup_type].append(t)
        stats: list[SetupStats] = []
        for stype, ts in buckets.items():
            n = len(ts)
            wins = [t for t in ts if t.r_realized > 0]
            r_vals = [t.r_realized for t in ts]
            stats.append(SetupStats(
                setup_type=stype,
                trades=n,
                winners=len(wins),
                win_rate=round(len(wins) / n * 100, 1) if n else 0,
                avg_r=round(sum(r_vals) / n, 2) if n else 0,
                total_pnl=round(sum(t.pnl_dollars for t in ts), 2),
                best_r=round(max(r_vals), 2) if r_vals else 0,
                worst_r=round(min(r_vals), 2) if r_vals else 0,
            ))
        stats.sort(key=lambda s: s.total_pnl, reverse=True)
        return stats

    def _detect_mistakes(self, trades: list[TradeRecord]) -> list[str]:
        """Heuristic mistake detection based on Martin's rules."""
        mistakes: list[str] = []
        for t in trades:
            risk_per_share = abs(t.entry_price - t.stop_price)
            # Mistake 1: Sold at breakeven when stock had potential
            if -0.1 < t.r_realized < 0.1 and t.exit_reason in ("manual", "breakeven"):
                if t.score >= 0.7:
                    mistakes.append(
                        f"{t.symbol} ({t.date}): BE exit on high-score setup ({t.score:.2f}) — "
                        "did you move stop up too fast?"
                    )
            # Mistake 2: Took very small profit on high-score setup
            if 0 < t.r_realized < 1.0 and t.score >= 0.75:
                mistakes.append(
                    f"{t.symbol} ({t.date}): Took only {t.r_realized:.1f}R on score={t.score:.2f} "
                    "setup — let winners run next time."
                )
            # Mistake 3: Ignored stop — loss > 1.5R
            if t.r_realized < -1.5:
                mistakes.append(
                    f"{t.symbol} ({t.date}): Loss of {t.r_realized:.1f}R — did you honor the stop?"
                )
            # Mistake 4: Trade taken in wrong regime
            if t.direction == "long" and t.regime == "BEAR":
                mistakes.append(
                    f"{t.symbol} ({t.date}): Long taken in BEAR regime — "
                    "should be avoided per Martin rules."
                )
        return mistakes

    def _compute_max_dd(self, trades: list[TradeRecord]) -> float:
        """Compute max consecutive R drawdown."""
        peak = 0.0
        trough = 0.0
        max_dd = 0.0
        cumr = 0.0
        for t in sorted(trades, key=lambda x: x.date):
            cumr += t.r_realized
            if cumr > peak:
                peak = cumr
                trough = cumr
            elif cumr < trough:
                trough = cumr
                dd = peak - trough
                if dd > max_dd:
                    max_dd = dd
        return round(max_dd, 2)

    def _grade(self, expectancy: float, win_rate: float, mistake_count: int) -> str:
        if expectancy >= 0.8 and mistake_count <= 1:
            return "A"
        if expectancy >= 0.5 and mistake_count <= 3:
            return "B"
        if expectancy >= 0.2 or win_rate >= 50:
            return "C"
        return "D"

    # ------------------------------------------------------------------
    # Build report
    # ------------------------------------------------------------------

    def generate_weekly_report(
        self,
        week_end: Optional[str] = None,
        extra_notes: str = "",
    ) -> WeeklyReport:
        we = week_end or str(date.today())
        we_dt = date.fromisoformat(we[:10])
        ws_dt = we_dt - timedelta(days=we_dt.weekday())  # Monday
        ws = str(ws_dt)

        trades = self.load_trades(week_start=ws, week_end=we)
        if not trades:
            log.info("No trades found for %s – %s", ws, we)

        n = len(trades)
        winners = [t for t in trades if t.r_realized > 0.2]
        losers  = [t for t in trades if t.r_realized < -0.2]
        be_list = [t for t in trades if -0.2 <= t.r_realized <= 0.2]

        win_rate   = round(len(winners) / n * 100, 1) if n else 0
        r_vals     = [t.r_realized for t in trades]
        avg_r      = round(sum(r_vals) / n, 3) if n else 0
        avg_win_r  = (
            round(sum(t.r_realized for t in winners) / len(winners), 2)
            if winners else 0
        )
        avg_loss_r = (
            round(sum(abs(t.r_realized) for t in losers) / len(losers), 2)
            if losers else 0
        )
        loss_rate  = round(len(losers) / n * 100, 1) if n else 0
        expectancy = round(
            (win_rate / 100) * avg_win_r - (loss_rate / 100) * avg_loss_r, 3
        )

        total_pnl   = round(sum(t.pnl_dollars for t in trades), 2)
        max_dd      = self._compute_max_dd(trades)
        setup_bd    = self._setup_breakdown(trades)
        mistakes    = self._detect_mistakes(trades)
        regime_cnt  = {}
        for t in trades:
            regime_cnt[t.regime] = regime_cnt.get(t.regime, 0) + 1

        best  = max(trades, key=lambda t: t.r_realized) if trades else None
        worst = min(trades, key=lambda t: t.r_realized) if trades else None
        grade = self._grade(expectancy, win_rate, len(mistakes))

        # Build markdown
        md = self._build_markdown(
            ws, we, n, len(winners), len(losers), len(be_list),
            win_rate, avg_r, expectancy, total_pnl, max_dd,
            setup_bd, regime_cnt, best, worst, mistakes, grade, extra_notes,
        )

        return WeeklyReport(
            week_start=ws, week_end=we,
            total_trades=n, winners=len(winners), losers=len(losers),
            breakeven=len(be_list), win_rate=win_rate, avg_r=avg_r,
            expectancy=expectancy, total_pnl=total_pnl, max_drawdown=max_dd,
            setup_breakdown=setup_bd, regime_counts=regime_cnt,
            best_trade=best, worst_trade=worst,
            mistake_count=len(mistakes), mistake_notes=mistakes,
            grade=grade, markdown=md,
        )

    def _build_markdown(self, ws, we, n, wins, losses, be,
                        win_rate, avg_r, expectancy, total_pnl, max_dd,
                        setup_bd, regime_cnt, best, worst, mistakes, grade, extra) -> str:
        lines = [
            f"# Weekly Review: {ws} → {we}",
            f"",
            f"## Grade: {grade}",
            f"",
            f"## Performance Summary",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Trades | {n} |",
            f"| Win / Loss / BE | {wins} / {losses} / {be} |",
            f"| Win Rate | {win_rate:.1f}% |",
            f"| Avg R | {avg_r:+.2f}R |",
            f"| Expectancy | {expectancy:+.3f}R |",
            f"| Total P&L | ${total_pnl:+,.2f} |",
            f"| Max DD (R) | {max_dd:.2f}R |",
            f"",
            f"## Setup Breakdown",
            f"| Setup | Trades | Win% | Avg R | Total P&L |",
            f"|-------|--------|------|-------|-----------|",
        ]
        for s in setup_bd:
            lines.append(
                f"| {s.setup_type} | {s.trades} | {s.win_rate:.0f}% "
                f"| {s.avg_r:+.2f}R | ${s.total_pnl:+,.0f} |"
            )
        lines += [
            f"",
            f"## Regime Distribution",
        ]
        for regime, cnt in regime_cnt.items():
            lines.append(f"- **{regime}**: {cnt} trade(s)")
        lines += [
            f"",
            f"## Best & Worst",
        ]
        if best:
            lines.append(
                f"- ✅ **Best**: {best.symbol} {best.setup_type} +{best.r_realized:.1f}R "
                f"(${best.pnl_dollars:+,.0f}) on {best.date}"
            )
        if worst:
            lines.append(
                f"- ❌ **Worst**: {worst.symbol} {worst.setup_type} {worst.r_realized:.1f}R "
                f"(${worst.pnl_dollars:+,.0f}) on {worst.date}"
            )
        if mistakes:
            lines += [f"", f"## Mistakes to Fix ({len(mistakes)})"] + [
                f"{i+1}. {m}" for i, m in enumerate(mistakes)
            ]
        if extra:
            lines += [f"", f"## Notes", extra]
        lines += [
            f"",
            f"---",
            f"*Generated by martin-quant WeeklyReviewer*",
        ]
        return "\n".join(lines)

    def save_report(self, report: WeeklyReport, output_dir: str = "reports") -> str:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        fname = f"{output_dir}/weekly_{report.week_end}.md"
        with open(fname, "w") as f:
            f.write(report.markdown)
        log.info("Report saved: %s", fname)
        return fname
