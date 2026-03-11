"""trade_logger.py

Trade Logger — CSV 交易記錄器
================================
每次進場/出場自動記錄到 trades.csv，
並計算 R 倍數、勝率、Profit Factor 等統計。

Usage:
    from martin_quant.utils.trade_logger import TradeLogger

    logger = TradeLogger(filepath="trades.csv")
    logger.log_entry(symbol="NVDA", setup="eps", entry=125.0, stop=121.0,
                     target=137.0, shares=100, risk_dollars=400.0)
    logger.log_exit(symbol="NVDA", exit_price=136.5, exit_type="partial_3r",
                    exit_pct=0.5, shares_exited=50)
    print(logger.get_stats())
"""
from __future__ import annotations

import csv
import os
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional
import datetime

log = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """單筆交易記錄"""
    trade_id: str
    symbol: str
    setup_type: str
    direction: str
    entry_date: str
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    risk_dollars: float
    sector: str = ""
    theme: str = ""
    regime: str = ""
    score: float = 0.0

    # Exit fields (填入後表示已出場)
    exit_date: str = ""
    exit_price: float = 0.0
    exit_type: str = ""
    exit_pct: float = 0.0
    shares_exited: int = 0
    pnl_dollars: float = 0.0
    r_multiple: float = 0.0
    status: str = "open"   # "open" | "closed" | "partial"


class TradeLogger:
    """
    CSV 交易日誌記錄器。

    Parameters
    ----------
    filepath : str
        CSV 檔案路徑，預設 trades.csv
    """

    FIELDNAMES = [
        "trade_id", "symbol", "setup_type", "direction",
        "entry_date", "entry_price", "stop_price", "target_price",
        "shares", "risk_dollars", "sector", "theme", "regime", "score",
        "exit_date", "exit_price", "exit_type", "exit_pct",
        "shares_exited", "pnl_dollars", "r_multiple", "status",
    ]

    def __init__(self, filepath: str = "trades.csv") -> None:
        self.filepath = filepath
        self._ensure_file()

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def log_entry(
        self,
        symbol: str,
        setup_type: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        shares: int,
        risk_dollars: float,
        direction: str = "long",
        sector: str = "",
        theme: str = "",
        regime: str = "",
        score: float = 0.0,
        entry_date: Optional[str] = None,
    ) -> str:
        """記錄進場，返回 trade_id"""
        today = entry_date or str(datetime.date.today())
        trade_id = f"{symbol}_{today}_{setup_type}"

        record = TradeRecord(
            trade_id=trade_id,
            symbol=symbol,
            setup_type=setup_type,
            direction=direction,
            entry_date=today,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            shares=shares,
            risk_dollars=risk_dollars,
            sector=sector,
            theme=theme,
            regime=regime,
            score=score,
            status="open",
        )
        self._append_row(asdict(record))
        log.info("TradeLogger: ENTRY %s %s @%.2f", symbol, setup_type, entry_price)
        return trade_id

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    def log_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_type: str,
        exit_pct: float = 1.0,
        shares_exited: Optional[int] = None,
        exit_date: Optional[str] = None,
        trade_id: Optional[str] = None,
    ) -> bool:
        """更新對應交易的出場資訊。返回 True 若找到記錄。"""
        today = exit_date or str(datetime.date.today())
        rows = self._read_all()
        updated = False

        for row in rows:
            # 匹配邏輯：同 symbol + open 狀態（或指定 trade_id）
            if trade_id:
                match = row["trade_id"] == trade_id
            else:
                match = row["symbol"] == symbol and row["status"] in ("open", "partial")

            if match and not updated:
                entry   = float(row["entry_price"])
                stop    = float(row["stop_price"])
                risk_ps = abs(entry - stop)
                direction = row.get("direction", "long")

                if direction == "long":
                    pnl_ps = exit_price - entry
                else:
                    pnl_ps = entry - exit_price

                exited_shares = shares_exited or int(float(row["shares"]) * exit_pct)
                pnl_dollars   = pnl_ps * exited_shares
                r_multiple    = pnl_ps / risk_ps if risk_ps > 0 else 0.0

                row["exit_date"]     = today
                row["exit_price"]    = round(exit_price, 4)
                row["exit_type"]     = exit_type
                row["exit_pct"]      = exit_pct
                row["shares_exited"] = exited_shares
                row["pnl_dollars"]   = round(pnl_dollars, 2)
                row["r_multiple"]    = round(r_multiple, 2)
                row["status"]        = "closed" if exit_pct >= 1.0 else "partial"
                updated = True
                log.info(
                    "TradeLogger: EXIT %s @%.2f R=%.1f pnl=$%.0f",
                    symbol, exit_price, r_multiple, pnl_dollars,
                )

        if updated:
            self._write_all(rows)
        return updated

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """計算已關閉交易的統計數據"""
        rows = self._read_all()
        closed = [r for r in rows if r["status"] == "closed"]

        if not closed:
            return {"total_trades": 0, "message": "No closed trades yet"}

        r_multiples = [float(r["r_multiple"]) for r in closed]
        pnl_list    = [float(r["pnl_dollars"]) for r in closed]

        wins    = [r for r in r_multiples if r > 0]
        losses  = [r for r in r_multiples if r <= 0]
        win_rate = len(wins) / len(r_multiples) * 100 if r_multiples else 0

        gross_profit = sum(p for p in pnl_list if p > 0)
        gross_loss   = abs(sum(p for p in pnl_list if p < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return {
            "total_trades":   len(closed),
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate_pct":   round(win_rate, 1),
            "avg_win_r":      round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss_r":     round(sum(losses) / len(losses), 2) if losses else 0,
            "total_r":        round(sum(r_multiples), 2),
            "profit_factor":  round(profit_factor, 2),
            "total_pnl_$":    round(sum(pnl_list), 2),
            "best_trade_r":   round(max(r_multiples), 2),
            "worst_trade_r":  round(min(r_multiples), 2),
            "open_trades":    len([r for r in rows if r["status"] in ("open", "partial")]),
        }

    def get_open_trades(self) -> list[dict]:
        """返回所有未出場的持倉"""
        rows = self._read_all()
        return [r for r in rows if r["status"] in ("open", "partial")]

    # ------------------------------------------------------------------
    # File IO
    # ------------------------------------------------------------------

    def _ensure_file(self) -> None:
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDNAMES)
                writer.writeheader()
            log.info("TradeLogger: created %s", self.filepath)

    def _append_row(self, row: dict) -> None:
        with open(self.filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDNAMES)
            writer.writerow({k: row.get(k, "") for k in self.FIELDNAMES})

    def _read_all(self) -> list[dict]:
        with open(self.filepath, "r", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _write_all(self, rows: list[dict]) -> None:
        with open(self.filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
