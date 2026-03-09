"""short_setup.py

做空 Setup 辨識 — Martin 影片 1:14:53 – 1:50:12

做空條件:
  1. 日線 EMA 空頭排列 (EMA9 < EMA20 < EMA50)
  2. 股價在 EMA9 下方 (close < ema9)
  3. 週線 EMA 也是空頭 (via WeeklyContext)
  4. 最近一根大陰線或 bearish engulfing (觸發確認)
  5. 反彈到 EMA9 或 intraday resistance 附近 → 入空
  6. Stop: 收在 EMA20 之上 or 過前日高點

注意: 做空只在 MarketRegime != BULL 時執行 (Martin 原則)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from martin_quant.core.enums import SetupType
from martin_quant.core.datatypes import SetupSignal
from martin_quant.features.ema import add_ema_features
from martin_quant.features.candlestick import (
    detect_engulfing_bear,
    detect_inside_day,
    detect_nr7,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class ShortSetupConfig:
    ema_fast: int = 9
    ema_mid: int  = 20
    ema_slow: int = 50

    # Distance from EMA9 to call it a "bounce into resistance"
    max_bounce_dist_pct: float = 3.0   # close within 3% below ema9

    # Confirmation: require bearish candlestick on signal bar
    require_bearish_candle: bool = True

    # RS filter: stock must be weaker than SPY (negative relative perf)
    require_negative_rs: bool = False   # optional — needs RS data

    # Minimum EMA decline slope (ema20 must be falling)
    require_ema20_declining: bool = True
    ema20_slope_lookback: int = 5       # bars to measure slope

    # Risk: stop above EMA20
    stop_buffer_pct: float = 0.5        # stop = ema20 * (1 + stop_buffer_pct%)

    # Target: 1:2 default R/R
    min_rr_ratio: float = 2.0

    min_history_bars: int = 60
    min_score: float = 0.3


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class ShortSetupDetector:
    """
    Scans daily OHLCV for short setup candidates.

    A setup is valid when:
      - Daily EMA bear stack (9 < 20 < 50)
      - Close is below EMA9 but bouncing toward it (within max_bounce_dist_pct)
      - EMA20 has been declining over the past N bars
      - Optional: bearish candle pattern on the signal bar

    Entry logic:
      - Short entry = current close (or next open)
      - Stop       = EMA20 + buffer%
      - Target     = entry - (stop - entry) * min_rr_ratio
    """

    def __init__(self, config: Optional[ShortSetupConfig] = None) -> None:
        self.config = config or ShortSetupConfig()

    def detect(
        self,
        symbol: str,
        df: pd.DataFrame,
        weekly_bear: bool = False,   # pass WeeklyContext.ema_bear_stack
    ) -> Optional[SetupSignal]:
        """
        Returns SetupSignal if a short setup is detected on the last bar,
        else None.
        """
        cfg = self.config
        if len(df) < cfg.min_history_bars:
            return None

        df = add_ema_features(df, spans=(cfg.ema_fast, cfg.ema_mid, cfg.ema_slow))
        last = df.iloc[-1]

        ema9  = last.get(f"ema_{cfg.ema_fast}")
        ema20 = last.get(f"ema_{cfg.ema_mid}")
        ema50 = last.get(f"ema_{cfg.ema_slow}")
        close = last["close"]

        if any(pd.isna([ema9, ema20, ema50])):
            return None

        # 1. Bear EMA stack
        if not (ema9 < ema20 < ema50):
            return None

        # 2. Close below EMA9
        if close >= ema9:
            return None

        # 3. Close within bounce zone (not too far below ema9)
        dist_pct = (ema9 - close) / ema9 * 100
        if dist_pct > cfg.max_bounce_dist_pct:
            return None

        # 4. EMA20 slope declining
        if cfg.require_ema20_declining:
            ema20_series = df[f"ema_{cfg.ema_mid}"].tail(cfg.ema20_slope_lookback)
            if ema20_series.iloc[-1] >= ema20_series.iloc[0]:
                return None

        # 5. Optional bearish candle
        score = 0.4   # base score for bear stack + bounce
        if cfg.require_bearish_candle:
            engulf = detect_engulfing_bear(df).iloc[-1]
            inside = detect_inside_day(df).iloc[-1]
            nr7    = detect_nr7(df).iloc[-1]
            if engulf:
                score += 0.3
            elif inside or nr7:
                score += 0.2
            else:
                return None  # require some pattern
        else:
            score += 0.2

        # 6. Weekly confirmation bonus
        if weekly_bear:
            score += 0.2

        if score < cfg.min_score:
            return None

        # --- Levels ---
        entry_price = close
        stop_price  = ema20 * (1 + cfg.stop_buffer_pct / 100)
        risk_per_share = stop_price - entry_price
        if risk_per_share <= 0:
            return None

        target_price = entry_price - risk_per_share * cfg.min_rr_ratio
        if target_price <= 0:
            return None

        return SetupSignal(
            symbol=symbol,
            setup_type=SetupType.BREAKDOWN,     # short breakdown
            score=round(min(score, 1.0), 3),
            entry_price=round(float(entry_price), 4),
            stop_price=round(float(stop_price), 4),
            target_price=round(float(target_price), 4),
            support_level=None,
            resistance_level=round(float(ema9), 4),  # EMA9 = short-side resistance
            invalidation_level=round(float(stop_price), 4),
            direction="short",
            notes=(
                f"Bear stack: EMA{cfg.ema_fast}={ema9:.2f} < "
                f"EMA{cfg.ema_mid}={ema20:.2f} < "
                f"EMA{cfg.ema_slow}={ema50:.2f}. "
                f"Bounce dist={dist_pct:.1f}%."
            ),
        )

    def scan_universe(
        self,
        symbols: list[str],
        ohlcv_map: dict[str, pd.DataFrame],
        weekly_bear_map: Optional[dict[str, bool]] = None,
    ) -> list[SetupSignal]:
        """
        Scan all symbols and return valid short setups sorted by score desc.

        Parameters
        ----------
        symbols : list[str]
        ohlcv_map : dict[str, pd.DataFrame]
            Daily OHLCV for each symbol.
        weekly_bear_map : dict[str, bool], optional
            Pre-computed {symbol: weekly_context.ema_bear_stack}.
        """
        results: list[SetupSignal] = []
        wbm = weekly_bear_map or {}

        for sym in symbols:
            df = ohlcv_map.get(sym)
            if df is None:
                continue
            try:
                sig = self.detect(
                    symbol=sym,
                    df=df,
                    weekly_bear=wbm.get(sym, False),
                )
                if sig is not None:
                    results.append(sig)
            except Exception:
                continue

        return sorted(results, key=lambda s: s.score, reverse=True)
