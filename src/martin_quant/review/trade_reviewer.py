"""trade_reviewer.py

Trade Reviewer
===================================
Reads trades.csv and computes review metrics for weekly reporting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
import logging

import pandas as pd

log = logging.getLogger(__name__)

REQUIRED_COLS = {
    "date", "symbol", "pnl_dollars", "r_multiple", "outcome"
}
OPTIONAL_COLS = [
    "setup_type", "sector", "entry", "exit_price", "shares",
    "pnl_pct", "exit_reason", "holding_days", "regime",
    "confirmation_mode", "confirmation_bars", "confirmation_reason",
    "weekly_trend_state", "gap_label",
]


@dataclass
class SetupStats:
    setup_type: str
    n_trades: int
    win_rate: float
    avg_r: float
    total_r: float
    profit_factor: float
    best_trade_r: float
    worst_trade_r: float


@dataclass
class ReviewResult:
    period_label: str
    start_date: str
    end_date: str
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    avg_r: float
    total_r: float
    total_pnl: float
    profit_factor: float
    expectancy: float
    max_drawdown_r: float
    best_trade: str
    worst_trade: str
    by_setup: list[SetupStats] = field(default_factory=list)
    by_sector: dict = field(default_factory=dict)
    by_regime: dict = field(default_factory=dict)
    by_confirmation: dict = field(default_factory=dict)
    by_weekly_trend: dict = field(default_factory=dict)
    by_gap_label: dict = field(default_factory=dict)
    improvement_notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"\n{'='*55}",
            f"  Trade Review: {self.period_label}",
            f"{'='*55}",
            f"  Trades : {self.n_trades}  ({self.n_wins}W / {self.n_losses}L)",
            f"  Win %  : {self.win_rate*100:.1f}%",
            f"  Avg R  : {self.avg_r:+.2f}R",
            f"  Total R: {self.total_r:+.2f}R",
            f"  PnL $  : ${self.total_pnl:,.0f}",
            f"  PF     : {self.profit_factor:.2f}",
            f"  Expect : {self.expectancy:+.3f}R",
            f"  MaxDD  : {self.max_drawdown_r:.2f}R",
            f"  Best   : {self.best_trade}",
            f"  Worst  : {self.worst_trade}",
        ]
        if self.by_setup:
            lines.append("\n  By Setup:")
            for s in sorted(self.by_setup, key=lambda x: x.total_r, reverse=True):
                lines.append(
                    f"    {s.setup_type:20s}  {s.n_trades:2d}T  "
                    f"WR={s.win_rate*100:.0f}%  AvgR={s.avg_r:+.2f}  "
                    f"TotalR={s.total_r:+.2f}"
                )
        if self.by_confirmation:
            lines.append("\n  By Confirmation:")
            for key, value in sorted(self.by_confirmation.items(), key=lambda item: item[1], reverse=True):
                lines.append(f"    {key:24s} {value:+.2f}R")
        if self.by_weekly_trend:
            lines.append("\n  By Weekly Trend:")
            for key, value in sorted(self.by_weekly_trend.items(), key=lambda item: item[1], reverse=True):
                lines.append(f"    {key:24s} {value:+.2f}R")
        if self.by_gap_label:
            lines.append("\n  By Gap Label:")
            for key, value in sorted(self.by_gap_label.items(), key=lambda item: item[1], reverse=True):
                lines.append(f"    {key:24s} {value:+.2f}R")
        if self.improvement_notes:
            lines.append("\n  Improvement Notes:")
            for note in self.improvement_notes:
                lines.append(f"    - {note}")
        lines.append(f"{'='*55}\n")
        return "\n".join(lines)


class TradeReviewer:
    def __init__(self, csv_path: str = "trades.csv") -> None:
        self.csv_path = Path(csv_path)
        self._df: Optional[pd.DataFrame] = None

    def load(self) -> pd.DataFrame:
        if not self.csv_path.exists():
            log.warning("TradeReviewer: %s not found, returning empty df", self.csv_path)
            return pd.DataFrame()
        df = pd.read_csv(self.csv_path)
        df.columns = [c.lower().strip() for c in df.columns]

        missing = REQUIRED_COLS - set(df.columns)
        if missing:
            log.warning("trades.csv missing columns: %s", missing)
            return pd.DataFrame()

        for col in OPTIONAL_COLS:
            if col not in df.columns:
                df[col] = None

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df = df.sort_values("date").reset_index(drop=True)

        df["outcome"] = df["outcome"].astype(str).str.lower().str.strip()
        df["outcome"] = df["outcome"].map(
            lambda x: "win" if str(x).startswith("w")
            else ("loss" if str(x).startswith("l") else "breakeven")
        )
        self._df = df
        return df

    def review(
        self,
        weeks: int = 1,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> ReviewResult:
        if self._df is None:
            self.load()

        df = self._df
        if df is None or df.empty:
            return self._empty_result("No data")

        end_dt = pd.Timestamp(end_date) if end_date else pd.Timestamp.today()
        start_dt = pd.Timestamp(start_date) if start_date else end_dt - pd.Timedelta(weeks=weeks, days=1)

        period_df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)].copy()
        period_label = f"Week of {start_dt.strftime('%Y-%m-%d')}" if weeks == 1 else f"{start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')}"

        if period_df.empty:
            return self._empty_result(period_label)

        return self._calc_stats(period_df, period_label, start_dt.strftime('%Y-%m-%d'), end_dt.strftime('%Y-%m-%d'))

    def all_time(self) -> ReviewResult:
        if self._df is None:
            self.load()
        df = self._df
        if df is None or df.empty:
            return self._empty_result("All Time")
        start = str(df["date"].min().date())
        end = str(df["date"].max().date())
        return self._calc_stats(df, "All Time", start, end)

    def _calc_stats(self, df: pd.DataFrame, label: str, start: str, end: str) -> ReviewResult:
        r = df["r_multiple"].astype(float)
        pnl = df["pnl_dollars"].astype(float) if "pnl_dollars" in df.columns else r * 500

        wins = df[df["outcome"] == "win"]
        losses = df[df["outcome"] == "loss"]
        n_wins = len(wins)
        n_losses = len(losses)
        n_total = len(df)
        win_rate = n_wins / n_total if n_total > 0 else 0.0

        avg_r = float(r.mean()) if n_total > 0 else 0.0
        total_r = float(r.sum())
        total_pnl = float(pnl.sum())

        avg_win_r = float(wins["r_multiple"].astype(float).mean()) if n_wins > 0 else 0.0
        avg_loss_r = abs(float(losses["r_multiple"].astype(float).mean())) if n_losses > 0 else 0.0
        expectancy = win_rate * avg_win_r - (1 - win_rate) * avg_loss_r

        gross_profit = float(wins["r_multiple"].astype(float).sum()) if n_wins > 0 else 0.0
        gross_loss = abs(float(losses["r_multiple"].astype(float).sum())) if n_losses > 0 else 1e-9
        profit_factor = gross_profit / gross_loss

        cum_r = r.cumsum()
        roll_max = cum_r.cummax()
        drawdown = cum_r - roll_max
        max_dd = float(drawdown.min()) if not drawdown.empty else 0.0

        if n_total > 0:
            best_idx = r.idxmax()
            worst_idx = r.idxmin()
            best_sym = str(df.loc[best_idx, "symbol"]) if "symbol" in df.columns else "?"
            worst_sym = str(df.loc[worst_idx, "symbol"]) if "symbol" in df.columns else "?"
            best_trade = f"{best_sym} {r[best_idx]:+.1f}R"
            worst_trade = f"{worst_sym} {r[worst_idx]:+.1f}R"
        else:
            best_trade = worst_trade = "N/A"

        by_setup: list[SetupStats] = []
        if "setup_type" in df.columns:
            for stype, grp in df.groupby("setup_type"):
                gr = grp["r_multiple"].astype(float)
                gw = grp[grp["outcome"] == "win"]
                gl = grp[grp["outcome"] == "loss"]
                gp = float(gw["r_multiple"].astype(float).sum()) if len(gw) > 0 else 0.0
                gloss = abs(float(gl["r_multiple"].astype(float).sum())) if len(gl) > 0 else 1e-9
                by_setup.append(SetupStats(
                    setup_type=str(stype),
                    n_trades=len(grp),
                    win_rate=len(gw) / len(grp),
                    avg_r=float(gr.mean()),
                    total_r=float(gr.sum()),
                    profit_factor=gp / gloss,
                    best_trade_r=float(gr.max()),
                    worst_trade_r=float(gr.min()),
                ))

        by_sector = self._sum_group(df, "sector")
        by_regime = self._win_rate_group(df, "regime")
        by_confirmation = self._sum_group(df, "confirmation_mode")
        by_weekly_trend = self._sum_group(df, "weekly_trend_state")
        by_gap_label = self._sum_group(df, "gap_label")

        notes = self._generate_notes(
            win_rate,
            avg_r,
            expectancy,
            by_setup,
            by_confirmation=by_confirmation,
            by_weekly_trend=by_weekly_trend,
            by_gap_label=by_gap_label,
        )

        return ReviewResult(
            period_label=label,
            start_date=start,
            end_date=end,
            n_trades=n_total,
            n_wins=n_wins,
            n_losses=n_losses,
            win_rate=round(win_rate, 3),
            avg_r=round(avg_r, 3),
            total_r=round(total_r, 2),
            total_pnl=round(total_pnl, 2),
            profit_factor=round(profit_factor, 2),
            expectancy=round(expectancy, 3),
            max_drawdown_r=round(abs(max_dd), 2),
            best_trade=best_trade,
            worst_trade=worst_trade,
            by_setup=by_setup,
            by_sector=by_sector,
            by_regime=by_regime,
            by_confirmation=by_confirmation,
            by_weekly_trend=by_weekly_trend,
            by_gap_label=by_gap_label,
            improvement_notes=notes,
        )

    @staticmethod
    def _sum_group(df: pd.DataFrame, column: str) -> dict:
        if column not in df.columns:
            return {}
        payload: dict[str, float] = {}
        clean = df[df[column].notna() & (df[column].astype(str).str.strip() != "")]
        for key, grp in clean.groupby(column):
            payload[str(key)] = round(float(grp["r_multiple"].astype(float).sum()), 2)
        return payload

    @staticmethod
    def _win_rate_group(df: pd.DataFrame, column: str) -> dict:
        if column not in df.columns:
            return {}
        payload: dict[str, float] = {}
        clean = df[df[column].notna() & (df[column].astype(str).str.strip() != "")]
        for key, grp in clean.groupby(column):
            wins = grp[grp["outcome"] == "win"]
            payload[str(key)] = round(len(wins) / len(grp), 3)
        return payload

    @staticmethod
    def _generate_notes(win_rate, avg_r, expectancy, by_setup, by_confirmation=None, by_weekly_trend=None, by_gap_label=None) -> list[str]:
        notes = []
        if win_rate < 0.40:
            notes.append(f"Win rate {win_rate*100:.0f}% below 40% review entry timing")
        if avg_r < 0.5:
            notes.append(f"Avg R {avg_r:.2f} below 0.5R cut losers faster or hold winners longer")
        if expectancy < 0:
            notes.append("Negative expectancy system not profitable, pause trading")
        if by_setup:
            worst_setup = min(by_setup, key=lambda s: s.total_r)
            if worst_setup.total_r < -2.0:
                notes.append(f"Setup '{worst_setup.setup_type}' dragging performance ({worst_setup.total_r:+.1f}R) consider pausing")
        if by_confirmation:
            weakest = min(by_confirmation.items(), key=lambda item: item[1])
            if weakest[1] < 0:
                notes.append(f"Confirmation bucket '{weakest[0]}' is net negative ({weakest[1]:+.1f}R)")
        if by_weekly_trend:
            weakest_weekly = min(by_weekly_trend.items(), key=lambda item: item[1])
            if weakest_weekly[1] < 0:
                notes.append(f"Weekly trend bucket '{weakest_weekly[0]}' underperformed ({weakest_weekly[1]:+.1f}R)")
        if by_gap_label:
            weakest_gap = min(by_gap_label.items(), key=lambda item: item[1])
            if weakest_gap[1] < 0:
                notes.append(f"Gap context '{weakest_gap[0]}' underperformed ({weakest_gap[1]:+.1f}R)")
        if not notes:
            notes.append("Performance within acceptable range stay consistent")
        return notes

    @staticmethod
    def _empty_result(label: str) -> ReviewResult:
        return ReviewResult(
            period_label=label, start_date="", end_date="",
            n_trades=0, n_wins=0, n_losses=0,
            win_rate=0.0, avg_r=0.0, total_r=0.0, total_pnl=0.0,
            profit_factor=0.0, expectancy=0.0, max_drawdown_r=0.0,
            best_trade="N/A", worst_trade="N/A",
        )
