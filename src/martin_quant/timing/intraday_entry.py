"""intraday_entry.py

5分鐘精準入場模組 — Martin Luk 影片 31:47 核心數學優勢

關鍵數學 (Martin 原話):
  日線入場 : stop = 日内低點 (3%)、同樣 dollar risk 對應 16.7% 倉位
  5分鐘入場: stop = 5分鐘柱底部 (1.5%)、同樣 dollar risk 對應 33.3% 倉位
  相同 Target 25% 回報:
    日線 stop: 25% / 3% = 8.3R
    5分鐘 stop: 25% / 1.5% = 16.7R   ← R multiple 翻倍 !

如何選擇 5分鐘入場處:
  1. 日線 setup 被觸發 (長線 reclaim / ORB / AVWAP)
  2. 切換到4H 图，等候 5分鐘 老黎 (ema9 reclaim on 5m chart)
  3. 入場点 = 5分鐘 ema9 之上收盤
  4. Stop    = 5分鐘入場柱的左側低點 (或 entry candle low)
  5. Target  = 日線層面上方 2R / 3R

此模組接受 5分鐘 OHLCV，自動計算最佳入場處、stop、shares。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class IntradayEntryConfig:
    ema_span: int = 9                  # 5m EMA span for reclaim detection
    max_stop_pct: float = 2.5          # max acceptable stop width %
    min_stop_pct: float = 0.3          # min stop (avoid noise-level stops)
    min_rvol_on_entry: float = 1.2     # entry candle must have >= 1.2x RVOL
    per_trade_risk_pct: float = 0.5    # % of equity to risk per trade
    r_target_multiple: float = 3.0     # initial target = entry + 3R
    use_entry_candle_low: bool = True  # stop = entry candle low (tighter)
    lookback_rvol: int = 20            # bars for avg volume


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass
class IntradayEntrySignal:
    symbol: str
    timeframe: str           # "5m"
    entry_price: float
    stop_price: float
    target_price: float
    stop_pct: float          # stop width as % of entry
    shares: int
    risk_dollars: float
    r_multiple_potential: float   # how many R to the initial target
    rvol: float
    ema9_5m: float
    trigger_reason: str

    @property
    def daily_r_equivalent(self) -> float:
        """Equivalent R if stop were at 3% (daily bar stop)."""
        if self.stop_pct <= 0:
            return 0.0
        return 3.0 / self.stop_pct * self.r_multiple_potential

    def to_dict(self) -> dict:
        return {
            "symbol":           self.symbol,
            "timeframe":        self.timeframe,
            "entry_price":      round(self.entry_price, 4),
            "stop_price":       round(self.stop_price, 4),
            "target_price":     round(self.target_price, 4),
            "stop_pct":         round(self.stop_pct, 2),
            "shares":           self.shares,
            "risk_dollars":     round(self.risk_dollars, 2),
            "r_potential":      round(self.r_multiple_potential, 1),
            "daily_r_equiv":    round(self.daily_r_equivalent, 1),
            "rvol":             round(self.rvol, 2),
            "ema9_5m":          round(self.ema9_5m, 4),
            "trigger":          self.trigger_reason,
        }


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class IntradayEntryDetector:
    """
    Given 5-minute OHLCV data and an equity amount, finds the optimal
    intraday entry point for a pre-identified daily setup.

    Usage:
        detector = IntradayEntryDetector(equity=100_000)
        signal = detector.find_entry(
            symbol="NVDA",
            df_5m=nvda_5m,
            daily_target=165.0,   # from daily setup
            daily_stop=148.0,     # daily stop (for context only)
        )
    """

    def __init__(
        self,
        equity: float = 100_000.0,
        config: Optional[IntradayEntryConfig] = None,
    ) -> None:
        self.equity = equity
        self.config = config or IntradayEntryConfig()

    def find_entry(
        self,
        symbol: str,
        df_5m: pd.DataFrame,
        daily_target: Optional[float] = None,
        daily_stop: Optional[float] = None,
    ) -> Optional[IntradayEntrySignal]:
        """
        Scan the most recent 5-minute bars for an intraday EMA9 reclaim entry.

        Parameters
        ----------
        symbol : str
        df_5m : pd.DataFrame
            5-minute OHLCV. Must have: open, high, low, close, volume.
        daily_target : float, optional
            Target price from the daily setup (used to compute R potential).
        daily_stop : float, optional
            Daily stop for context; if tighter intraday stop found, use that.

        Returns
        -------
        IntradayEntrySignal or None
        """
        cfg = self.config
        if len(df_5m) < cfg.lookback_rvol + cfg.ema_span:
            return None

        close = df_5m["close"]
        high  = df_5m["high"]
        low   = df_5m["low"]
        vol   = df_5m["volume"]

        # Compute 5m EMA9
        ema9 = close.ewm(span=cfg.ema_span, adjust=False,
                          min_periods=cfg.ema_span).mean()

        # Check last bar for EMA9 reclaim
        last_close = float(close.iloc[-1])
        last_low   = float(low.iloc[-1])
        last_high  = float(high.iloc[-1])
        last_ema9  = float(ema9.iloc[-1])
        prev_close = float(close.iloc[-2])
        prev_ema9  = float(ema9.iloc[-2])

        # EMA9 Reclaim: prev close < ema9, current close > ema9
        reclaim_trigger = (prev_close < prev_ema9) and (last_close > last_ema9)
        # Above EMA9 with strong close
        strength_trigger = last_close > last_ema9 and last_close >= last_high * 0.98

        if not (reclaim_trigger or strength_trigger):
            return None

        # RVOL check
        avg_vol = float(vol.iloc[-cfg.lookback_rvol:-1].mean())
        rvol    = float(vol.iloc[-1]) / avg_vol if avg_vol > 0 else 0.0
        if rvol < cfg.min_rvol_on_entry:
            return None

        # Entry price = last close (or next bar open — use close for calc)
        entry = last_close

        # Stop = entry candle low (tighter) or previous candle low
        if cfg.use_entry_candle_low:
            stop = last_low
        else:
            stop = float(low.iloc[-2:].min())

        # Widen stop slightly if too narrow (noise)
        stop_pct = (entry - stop) / entry * 100
        if stop_pct < cfg.min_stop_pct:
            stop     = entry * (1 - cfg.min_stop_pct / 100)
            stop_pct = cfg.min_stop_pct

        # Reject if stop too wide
        if stop_pct > cfg.max_stop_pct:
            return None

        # If daily stop is tighter than intraday, use daily stop
        if daily_stop is not None and daily_stop > stop:
            stop     = daily_stop
            stop_pct = (entry - stop) / entry * 100

        if stop >= entry:
            return None

        # Position sizing: risk_dollars = equity * per_trade_risk_pct / 100
        risk_dollars   = self.equity * cfg.per_trade_risk_pct / 100
        risk_per_share = entry - stop
        shares         = max(1, int(risk_dollars / risk_per_share))

        # Target: use daily target if provided, else 3R
        if daily_target is not None and daily_target > entry:
            target = daily_target
        else:
            target = entry + risk_per_share * cfg.r_target_multiple

        r_potential = (target - entry) / risk_per_share if risk_per_share > 0 else 0.0

        trigger_reason = "ema9_reclaim_5m" if reclaim_trigger else "ema9_strength_5m"

        return IntradayEntrySignal(
            symbol=symbol,
            timeframe="5m",
            entry_price=round(entry, 4),
            stop_price=round(stop, 4),
            target_price=round(target, 4),
            stop_pct=round(stop_pct, 2),
            shares=shares,
            risk_dollars=round(risk_dollars, 2),
            r_multiple_potential=round(r_potential, 1),
            rvol=round(rvol, 2),
            ema9_5m=round(last_ema9, 4),
            trigger_reason=trigger_reason,
        )

    def compare_with_daily(
        self,
        intraday: IntradayEntrySignal,
        daily_stop_pct: float = 3.0,
        target_pct: float = 25.0,
    ) -> dict:
        """
        Show the R-multiple advantage of the 5m entry vs daily entry.
        Martin's key teaching: tighter stop = same $ risk = more R.
        """
        daily_r  = target_pct / daily_stop_pct
        intra_r  = target_pct / intraday.stop_pct
        return {
            "daily_stop_pct":   daily_stop_pct,
            "intraday_stop_pct": intraday.stop_pct,
            "daily_R":          round(daily_r, 1),
            "intraday_R":       round(intra_r, 1),
            "R_improvement":    round(intra_r - daily_r, 1),
            "position_size_ratio": round(daily_stop_pct / intraday.stop_pct, 2),
            "summary": (
                f"5m entry ({intraday.stop_pct:.1f}% stop) gives "
                f"{intra_r:.1f}R vs daily ({daily_stop_pct:.1f}% stop) "
                f"{daily_r:.1f}R — "
                f"{round(intra_r/daily_r, 1)}x better."
            ),
        }
