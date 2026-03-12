"""trade_reviewer.py

Trade Reviewer — 交易記錄分析引擎
===================================
從 trades.csv 讀取歷史交易，計算完整績效統計：
  - Win rate, Avg R, Expectancy
  - Profit factor, Max drawdown
  - 按 setup_type / sector 分組統計
  - 找出最佳/最差 setup 類型
  - 輸出可供 weekly_report.py 使用的 ReviewResult

Usage:
    from martin_quant.review.trade_reviewer import TradeReviewer

    reviewer = TradeReviewer(csv_path="trades.csv")
    result = reviewer.review(weeks=1)   # 本週
    result = reviewer.review(weeks=4)   # 近4週
    print(result.summary())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
import logging

import pandas as pd
import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Expected CSV columns
# ---------------------------------------------------------------------------
# date, symbol, setup_type, sector, entry, exit_price, shares,
# pnl_dollars, pnl_pct, r_multiple, outcome (win/loss/breakeven),
# exit_reason, holding_days, regime

REQUIRED_COLS = {
    "date", "symbol", "pnl_dollars", "r_multiple", "outcome"
}
OPTIONAL_COLS = [
    "setup_type", "sector", "entry", "exit_price", "shares",
    "pnl_pct", "exit_reason", "holding_days", "regime",
]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

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
    period_label: str           # e.g. "Week of 2026-03-09"
    start_date: str
    end_date: str
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float             # 0.0 ~ 1.0
    avg_r: float                # average R per trade
    total_r: float              # sum of all R
    total_pnl: float            # dollars
    profit_factor: float        # gross_profit / gross_loss
    expectancy: float           # win_rate * avg_win_r - loss_rate * avg_loss_r
    max_drawdown_r: float       # max consecutive R drawdown
    best_trade: str             # "SYMBOL +3.2R"
    worst_trade: str            # "SYMBOL -1.0R"
    by_setup: list[SetupStats] = field(default_factory=list)
    by_sector: dict = field(default_factory=dict)   # sector -> total_r
    by_regime: dict = field(default_factory=dict)   # regime -> win_rate
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
        if self.improvement_notes:
            lines.append("\n  Improvement Notes:")
            for note in self.improvement_notes:
                lines.append(f"    • {note}")
        lines.append(f"{'='*55}\n")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TradeReviewer
# ---------------------------------------------------------------------------

class TradeReviewer:
    """
    從 CSV 讀取交易記錄並計算績效統計。

    Parameters
    ----------
    csv_path : str or Path
        trades.csv 路徑
    """

    def __init__(self, csv_path: str = "trades.csv") -> None:
        self.csv_path = Path(csv_path)
        self._df: Optional[pd.DataFrame] = None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def load(self) -> pd.DataFrame:
        """載入並清洗 CSV"""
        if not self.csv_path.exists():
            log.warning("TradeReviewer: %s not found, returning empty df", self.csv_path)
            return pd.DataFrame()
        df = pd.read_csv(self.csv_path)
        df.columns = [c.lower().strip() for c in df.columns]

        # 確保必要欄位存在
        missing = REQUIRED_COLS - set(df.columns)
        if missing:
            log.warning("trades.csv missing columns: %s", missing)
            return pd.DataFrame()

        # 補充可選欄位
        for col in OPTIONAL_COLS:
            if col not in df.columns:
                df[col] = None

        # 解析日期
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df = df.sort_values("date").reset_index(drop=True)

        # 標準化 outcome
        df["outcome"] = df["outcome"].str.lower().str.strip()
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
        """
        計算指定時間範圍的績效。

        Parameters
        ----------
        weeks : int
            往前幾週（若未指定 start_date）
        start_date / end_date : str 'YYYY-MM-DD'
            手動指定範圍
        """
        if self._df is None:
            self.load()

        df = self._df
        if df is None or df.empty:
            return self._empty_result("No data")

        # 計算日期範圍
        end_dt   = pd.Timestamp(end_date) if end_date else pd.Timestamp.today()
        start_dt = pd.Timestamp(start_date) if start_date else end_dt - pd.Timedelta(weeks=weeks, days=1)

        period_df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)].copy()
        period_label = f"Week of {start_dt.strftime('%Y-%m-%d')}" if weeks == 1 \
            else f"{start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')}"

        if period_df.empty:
            return self._empty_result(period_label)

        return self._calc_stats(period_df, period_label,
                                start_dt.strftime('%Y-%m-%d'),
                                end_dt.strftime('%Y-%m-%d'))

    def all_time(self) -> ReviewResult:
        """計算全部歷史績效"""
        if self._df is None:
            self.load()
        df = self._df
        if df is None or df.empty:
            return self._empty_result("All Time")
        start = str(df["date"].min().date())
        end   = str(df["date"].max().date())
        return self._calc_stats(df, "All Time", start, end)

    # -----------------------------------------------------------------------
    # Core Stats Calculator
    # -----------------------------------------------------------------------

    def _calc_stats(self, df: pd.DataFrame, label: str,
                    start: str, end: str) -> ReviewResult:
        r = df["r_multiple"].astype(float)
        pnl = df["pnl_dollars"].astype(float) if "pnl_dollars" in df.columns else r * 500

        wins   = df[df["outcome"] == "win"]
        losses = df[df["outcome"] == "loss"]
        n_wins = len(wins)
        n_losses = len(losses)
        n_total = len(df)
        win_rate = n_wins / n_total if n_total > 0 else 0.0

        avg_r    = float(r.mean()) if n_total > 0 else 0.0
        total_r  = float(r.sum())
        total_pnl = float(pnl.sum())

        avg_win_r  = float(wins["r_multiple"].astype(float).mean()) if n_wins > 0 else 0.0
        avg_loss_r = abs(float(losses["r_multiple"].astype(float).mean())) if n_losses > 0 else 0.0
        expectancy = win_rate * avg_win_r - (1 - win_rate) * avg_loss_r

        gross_profit = float(wins["r_multiple"].astype(float).sum()) if n_wins > 0 else 0.0
        gross_loss   = abs(float(losses["r_multiple"].astype(float).sum())) if n_losses > 0 else 1e-9
        profit_factor = gross_profit / gross_loss

        # Max drawdown (cumulative R)
        cum_r = r.cumsum()
        roll_max = cum_r.cummax()
        drawdown = cum_r - roll_max
        max_dd = float(drawdown.min()) if not drawdown.empty else 0.0

        # Best / worst
        if n_total > 0:
            best_idx  = r.idxmax()
            worst_idx = r.idxmin()
            best_sym  = str(df.loc[best_idx, "symbol"]) if "symbol" in df.columns else "?"
            worst_sym = str(df.loc[worst_idx, "symbol"]) if "symbol" in df.columns else "?"
            best_trade  = f"{best_sym} {r[best_idx]:+.1f}R"
            worst_trade = f"{worst_sym} {r[worst_idx]:+.1f}R"
        else:
            best_trade = worst_trade = "N/A"

        # By setup
        by_setup = []
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

        # By sector
        by_sector: dict = {}
        if "sector" in df.columns:
            for sec, grp in df.groupby("sector"):
                by_sector[str(sec)] = round(float(grp["r_multiple"].astype(float).sum()), 2)

        # By regime
        by_regime: dict = {}
        if "regime" in df.columns:
            for reg, grp in df.groupby("regime"):
                gw2 = grp[grp["outcome"] == "win"]
                by_regime[str(reg)] = round(len(gw2) / len(grp), 3)

        # Improvement notes
        notes = self._generate_notes(win_rate, avg_r, expectancy, by_setup)

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
            improvement_notes=notes,
        )

    @staticmethod
    def _generate_notes(win_rate, avg_r, expectancy, by_setup) -> list[str]:
        notes = []
        if win_rate < 0.40:
            notes.append(f"Win rate {win_rate*100:.0f}% below 40% — review entry timing")
        if avg_r < 0.5:
            notes.append(f"Avg R {avg_r:.2f} below 0.5R — cut losers faster or hold winners longer")
        if expectancy < 0:
            notes.append("Negative expectancy — system not profitable, pause trading")
        if by_setup:
            worst_setup = min(by_setup, key=lambda s: s.total_r)
            if worst_setup.total_r < -2.0:
                notes.append(
                    f"Setup '{worst_setup.setup_type}' dragging performance "
                    f"({worst_setup.total_r:+.1f}R) — consider pausing"
                )
        if not notes:
            notes.append("Performance within acceptable range — stay consistent")
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
