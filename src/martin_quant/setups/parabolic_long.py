"""parabolic_long.py

Parabolic Long (Extreme Selloff Bounce) — Martin Luk 影片 2:35:17

背景: April correction trade — TQQ 13R in 30 minutes
Martin 說: "When the market is in an extreme selloff, you don't short.
           You wait for the ORB on the strongest names."

觸發條件:
  1. 市場 (SPY/QQQ) 收在 20日 EMA 下方 > 8%  (extreme oversold)
  2. 或 SPY 當日跌幅 > 5%  (panic selling day)
  3. VIX > 30 (fear spike) — 可選
  4. 個股: 強勢股 / 槓桿 ETF (TQQQ, SOXL etc.)
  5. 入場: 開市後 ORB 突破 (Opening Range High)

止損: ORB 低點 (非常緊)
目標: 快速 2R / 3R，不持倉過夜 (intraday only)

此 setup 是「反向思維」: 在恐慌中找買入機會，但只限極端情況。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class ParabolicLongConfig:
    # Market extreme condition triggers
    spy_below_ema20_pct: float = 8.0      # SPY > 8% below 20d EMA = extreme
    spy_single_day_drop_pct: float = 4.5  # OR single day drop > 4.5%
    ema_span_market: int = 20

    # ORB window (minutes from open)
    orb_minutes: int = 15              # 15-min ORB

    # Entry
    breakout_buffer_pct: float = 0.1   # enter 0.1% above ORB high

    # Stock quality on extreme days
    require_strong_sector: bool = True  # prefer tech/semis/crypto
    strong_sectors: tuple = ("Technology", "Semiconductors", "Crypto", "Biotech")

    # Leverage ETF shortlist
    leverage_etfs: tuple = ("TQQQ", "SOXL", "UPRO", "LABU", "FNGU")

    # Risk (intraday only — very tight)
    max_stop_pct: float = 2.0          # tight stop on intraday bounce
    target_r: float = 3.0
    intraday_only: bool = True         # always close by end of day


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass
class ParabolicLongSignal:
    symbol: str
    trigger_reason: str               # "spy_extreme_oversold" | "spy_panic_day"
    spy_vs_ema20_pct: float           # how far SPY is below EMA20
    spy_day_change_pct: float
    orb_high: float
    orb_low: float
    entry_price: float                # ORB high + buffer
    stop_price: float                 # ORB low
    target_price: float               # entry + 3R
    stop_pct: float
    is_leverage_etf: bool
    is_strong_sector: bool
    score: float
    notes: str

    def to_dict(self) -> dict:
        return {
            "symbol":           self.symbol,
            "trigger_reason":   self.trigger_reason,
            "spy_vs_ema20_pct": round(self.spy_vs_ema20_pct, 2),
            "spy_day_chg_pct":  round(self.spy_day_change_pct, 2),
            "orb_high":         round(self.orb_high, 2),
            "orb_low":          round(self.orb_low, 2),
            "entry":            round(self.entry_price, 2),
            "stop":             round(self.stop_price, 2),
            "target":           round(self.target_price, 2),
            "stop_pct":         round(self.stop_pct, 2),
            "is_lev_etf":       self.is_leverage_etf,
            "is_strong_sector": self.is_strong_sector,
            "score":            round(self.score, 3),
            "notes":            self.notes,
        }


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class ParabolicLongDetector:
    """
    Detects extreme selloff conditions and generates ORB long signals.

    Usage:
        detector = ParabolicLongDetector()

        # Check if market condition qualifies
        if detector.is_extreme_selloff(spy_daily_df):
            signals = detector.scan(
                symbols=["TQQQ", "NVDA", "SMCI"],
                orb_map={"TQQQ": tqqq_orb_df},   # first 15-min bars
                spy_daily_df=spy_daily_df,
                metadata=meta,
            )
    """

    def __init__(self, config: Optional[ParabolicLongConfig] = None) -> None:
        self.config = config or ParabolicLongConfig()

    def _spy_vs_ema20(self, spy_df: pd.DataFrame) -> float:
        """Returns how far SPY close is below its 20d EMA (positive = below)."""
        cfg = self.config
        if len(spy_df) < cfg.ema_span_market + 1:
            return 0.0
        ema20 = spy_df["close"].ewm(
            span=cfg.ema_span_market, adjust=False,
            min_periods=cfg.ema_span_market
        ).mean().iloc[-1]
        last_close = float(spy_df["close"].iloc[-1])
        return (ema20 - last_close) / ema20 * 100

    def _spy_day_change(self, spy_df: pd.DataFrame) -> float:
        if len(spy_df) < 2:
            return 0.0
        return float(
            (spy_df["close"].iloc[-1] - spy_df["close"].iloc[-2])
            / spy_df["close"].iloc[-2] * 100
        )

    def is_extreme_selloff(self, spy_df: pd.DataFrame) -> tuple[bool, str]:
        """
        Returns (is_extreme: bool, reason: str).
        Must return True before scan() is meaningful.
        """
        cfg = self.config
        vs_ema20   = self._spy_vs_ema20(spy_df)
        day_change = self._spy_day_change(spy_df)

        if vs_ema20 >= cfg.spy_below_ema20_pct:
            return True, f"spy_extreme_oversold (vs EMA20: -{vs_ema20:.1f}%)"
        if abs(day_change) >= cfg.spy_single_day_drop_pct and day_change < 0:
            return True, f"spy_panic_day ({day_change:.1f}%)"
        return False, ""

    def _get_orb(self, orb_bars: pd.DataFrame) -> tuple[float, float]:
        """Extract ORB high/low from the opening range bars."""
        return float(orb_bars["high"].max()), float(orb_bars["low"].min())

    def scan(
        self,
        symbols: list[str],
        orb_map: dict[str, pd.DataFrame],
        spy_daily_df: pd.DataFrame,
        metadata: Optional[dict[str, dict]] = None,
    ) -> list[ParabolicLongSignal]:
        """
        Parameters
        ----------
        symbols     : list[str]
        orb_map     : dict {symbol: first_15min_bars_df}
        spy_daily_df: SPY daily OHLCV
        metadata    : {symbol: {sector, theme}}

        Returns
        -------
        list[ParabolicLongSignal] sorted by score desc.
        ONLY call this when is_extreme_selloff() returns True.
        """
        cfg  = self.config
        meta = metadata or {}

        is_extreme, trigger = self.is_extreme_selloff(spy_daily_df)
        if not is_extreme:
            return []

        vs_ema20   = self._spy_vs_ema20(spy_daily_df)
        day_change = self._spy_day_change(spy_daily_df)

        results: list[ParabolicLongSignal] = []
        for sym in symbols:
            orb_bars = orb_map.get(sym)
            if orb_bars is None or len(orb_bars) == 0:
                continue

            orb_high, orb_low = self._get_orb(orb_bars)
            entry      = orb_high * (1 + cfg.breakout_buffer_pct / 100)
            stop       = orb_low
            stop_pct   = (entry - stop) / entry * 100

            if stop_pct <= 0 or stop_pct > cfg.max_stop_pct:
                continue

            risk_per_share = entry - stop
            target         = entry + risk_per_share * cfg.target_r

            sym_upper  = sym.upper()
            is_lev_etf = sym_upper in cfg.leverage_etfs
            sym_sector = meta.get(sym_upper, {}).get("sector", "")
            is_strong  = sym_sector in cfg.strong_sectors or is_lev_etf

            if cfg.require_strong_sector and not is_strong:
                continue

            # Score: extreme market + tight stop + leverage ETF bonus
            score = (
                min(vs_ema20 / 20.0, 1.0) * 0.35
                + min(abs(day_change) / 10.0, 1.0) * 0.30
                + (0.20 if is_lev_etf else 0.0)
                + (0.15 if is_strong else 0.0)
            )

            results.append(ParabolicLongSignal(
                symbol=sym_upper,
                trigger_reason=trigger,
                spy_vs_ema20_pct=round(vs_ema20, 2),
                spy_day_change_pct=round(day_change, 2),
                orb_high=round(orb_high, 2),
                orb_low=round(orb_low, 2),
                entry_price=round(entry, 4),
                stop_price=round(stop, 2),
                target_price=round(target, 2),
                stop_pct=round(stop_pct, 2),
                is_leverage_etf=is_lev_etf,
                is_strong_sector=is_strong,
                score=round(score, 3),
                notes=(
                    f"{trigger} | ORB breakout | "
                    f"Stop {stop_pct:.1f}% | Target {cfg.target_r}R"
                    + (" | LEVERAGE ETF" if is_lev_etf else "")
                ),
            ))

        return sorted(results, key=lambda s: s.score, reverse=True)
