"""daily_scan.py

Martin Luk Strategy — Main Daily Scan Pipeline

完整 9 步掃描流程，整合所有模組:

  Step 1  市場制度  MarketRegimeFilter    → Bull/Caution/Bear
  Step 2  週線背景  LeaderScanner         → 週線 RS 排名 + 市場健康
  Step 3  昨日強勢  PotentScanner         → 板塊輪動候選
  Step 4  開市缺口  PremarketGapScanner   → EPS gap-up 候選
  Step 5  股票篩選  WatchlistBuilder      → ADR5% + 美元量 + RS
  Step 6  主題排名  ThemeMomentumCalc     → 主題動能排名
  Step 7  Setup偵測 All setup detectors  → pullback/breakout/EPS/short
  Step 8  K線確認   CandlestickSummary   → inside_day/NR7 加分
  Step 9  風險計算  PositionSizer + Limits → 最終 shares + stop

輸出 DailyScanResult — 已排序的交易信號列表
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

# --- Internal imports ---
from martin_quant.filters.market_regime import (
    MarketRegimeFilter, MarketRegimeResult, MarketRegime,
)
from martin_quant.scanners.leader_scanner import (
    LeaderScanner, MarketHealthSnapshot,
)
from martin_quant.scanners.potent_scanner import PotentScanner
from martin_quant.scanners.premarket_gap_scanner import PremarketGapScanner
from martin_quant.universe.watchlist_builder import WatchlistBuilder, WatchlistEntry
from martin_quant.universe.theme_momentum import ThemeMomentumCalculator, ThemeStats
from martin_quant.setups.pullback_setup import PullbackSetupDetector
from martin_quant.setups.breakout_setup import BreakoutSetupDetector
from martin_quant.setups.eps_setup import EpsSetupDetector
from martin_quant.setups.short_setup import ShortSetupDetector
from martin_quant.setups.parabolic_long import ParabolicLongDetector
from martin_quant.features.candlestick import get_candlestick_summary
from martin_quant.features.weekly_context import get_weekly_context
from martin_quant.risk.position_sizer import PositionSizer
from martin_quant.risk.equity_curve_sizer import EquityCurveSizer
from martin_quant.risk.portfolio_limits import PortfolioLimitsChecker

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

@dataclass
class TradeSignal:
    symbol: str
    setup_type: str       # "pullback" | "breakout" | "eps" | "short" | "parabolic_long"
    direction: str        # "long" | "short"
    entry_price: float
    stop_price: float
    target_price: float
    stop_pct: float
    shares: int
    risk_dollars: float
    r_potential: float
    score: float
    category: str         # "leading" | "mediocre" | "lagging" | "pillar"
    sector: str
    theme: str
    candlestick_boost: float   # extra score from candlestick patterns
    total_score: float
    notes: str

    def to_dict(self) -> dict:
        return {
            "symbol":      self.symbol,
            "setup":       self.setup_type,
            "direction":   self.direction,
            "entry":       round(self.entry_price, 2),
            "stop":        round(self.stop_price, 2),
            "target":      round(self.target_price, 2),
            "stop_pct":    round(self.stop_pct, 2),
            "shares":      self.shares,
            "risk_$":      round(self.risk_dollars, 2),
            "R":           round(self.r_potential, 1),
            "score":       round(self.score, 3),
            "category":    self.category,
            "sector":      self.sector,
            "theme":       self.theme,
            "cs_boost":    round(self.candlestick_boost, 3),
            "total_score": round(self.total_score, 3),
            "notes":       self.notes,
        }


@dataclass
class DailyScanResult:
    date: str
    regime: MarketRegime
    regime_detail: MarketRegimeResult
    market_health: MarketHealthSnapshot
    top_themes: list[ThemeStats]
    watchlist_count: int
    signals: list[TradeSignal]
    skipped_reasons: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"=== Daily Scan {self.date} ===",
            f"Regime      : {self.regime.value}",
            f"Mkt Health  : {self.market_health.health_state} "
            f"({self.market_health.advancing}↑ / {self.market_health.declining}↓)",
            f"Top themes  : " + ", ".join(
                f"{t.theme}({t.momentum_state})" for t in self.top_themes[:3]
            ),
            f"Watchlist   : {self.watchlist_count} stocks passed filters",
            f"Signals     : {len(self.signals)}",
        ]
        for i, sig in enumerate(self.signals[:5], 1):
            lines.append(
                f"  {i}. {sig.symbol:6s} {sig.setup_type:14s} "
                f"entry={sig.entry_price:.2f} stop={sig.stop_price:.2f} "
                f"R={sig.r_potential:.1f} score={sig.total_score:.3f}"
            )
        return "\n".join(lines)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([s.to_dict() for s in self.signals])


# ---------------------------------------------------------------------------
# Scanner config
# ---------------------------------------------------------------------------

@dataclass
class DailyScanConfig:
    equity: float = 100_000.0
    per_trade_risk_pct: float = 0.5
    max_signals: int = 20

    # Toggle setups
    enable_pullback: bool = True
    enable_breakout: bool = True
    enable_eps: bool = True
    enable_short: bool = True
    enable_parabolic_long: bool = True

    # Regime overrides
    disable_longs_in_bear: bool = True
    disable_shorts_in_bull: bool = False   # Martin still does some shorts in bull

    # Candlestick boost weights
    inside_day_boost: float = 0.05
    nr7_boost: float = 0.08
    squeeze_boost: float = 0.12     # inside_day + NR7 combo
    engulfing_boost: float = 0.06


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

class DailyScanner:
    """
    Main daily scan orchestrator.

    Integrates all Martin Luk strategy modules into a single pipeline.

    Usage:
        scanner = DailyScanner(config=DailyScanConfig(equity=200_000))
        result = scanner.run(
            spy_df=spy_df,
            iwm_df=iwm_df,
            ohlcv_map=ohlcv_map,
            spy_df_for_rs=spy_df,
            metadata=meta,
            premarket_prices=premarket_px,
            eps_catalyst_set={"NVDA"},
        )
        print(result.summary())
        df = result.to_dataframe()
    """

    def __init__(self, config: Optional[DailyScanConfig] = None) -> None:
        self.cfg = config or DailyScanConfig()

        # Initialize all sub-modules
        self.regime_filter    = MarketRegimeFilter()
        self.leader_scanner   = LeaderScanner()
        self.potent_scanner   = PotentScanner()
        self.gap_scanner      = PremarketGapScanner()
        self.watchlist_bldr   = WatchlistBuilder()
        self.theme_calc       = ThemeMomentumCalculator()
        self.pullback_det     = PullbackSetupDetector()
        self.breakout_det     = BreakoutSetupDetector()
        self.eps_det          = EpsSetupDetector()
        self.short_det        = ShortSetupDetector()
        self.parabolic_det    = ParabolicLongDetector()
        self.position_sizer   = PositionSizer(equity=self.cfg.equity)
        self.ec_sizer         = EquityCurveSizer(initial_equity=self.cfg.equity)
        self.limits_checker   = PortfolioLimitsChecker()

    def run(
        self,
        spy_df: pd.DataFrame,
        iwm_df: pd.DataFrame,
        ohlcv_map: dict[str, pd.DataFrame],
        metadata: Optional[dict[str, dict]] = None,
        premarket_prices: Optional[dict[str, float]] = None,
        premarket_volumes: Optional[dict[str, float]] = None,
        eps_catalyst_set: Optional[set[str]] = None,
        current_equity: Optional[float] = None,
        orb_map: Optional[dict[str, pd.DataFrame]] = None,
        date_str: Optional[str] = None,
    ) -> DailyScanResult:
        cfg      = self.cfg
        equity   = current_equity or cfg.equity
        symbols  = list(ohlcv_map.keys())
        meta     = metadata or {}
        pm_px    = premarket_prices or {}
        pm_vol   = premarket_volumes or {}
        eps_set  = eps_catalyst_set or set()
        skipped: dict[str, int] = {}

        # ---------------------------------------------------------------
        # STEP 1: Market regime
        # ---------------------------------------------------------------
        regime_result = self.regime_filter.evaluate(spy_df, iwm_df)
        regime        = regime_result.regime
        log.info("[Step1] Regime: %s", regime.value)

        # Update portfolio limits + dynamic sizer
        self.limits_checker.set_market_regime(regime.value.lower().replace(" ", "_").split("_")[0])

        # ---------------------------------------------------------------
        # STEP 2: Leader scanner (weekly RS)
        # ---------------------------------------------------------------
        leaders, mkt_health = self.leader_scanner.scan(
            symbols, ohlcv_map, spy_df, meta
        )
        leader_set = {e.symbol for e in leaders}
        log.info("[Step2] Leaders: %d | Health: %s", len(leader_set), mkt_health.health_state)

        # ---------------------------------------------------------------
        # STEP 3: Potent scanner (yesterday's strongest)
        # ---------------------------------------------------------------
        potent = self.potent_scanner.scan(symbols, ohlcv_map, meta)
        potent_set = {c.symbol for c in potent}
        log.info("[Step3] Potent candidates: %d", len(potent_set))

        # ---------------------------------------------------------------
        # STEP 4: Pre-market gap scanner
        # ---------------------------------------------------------------
        gap_candidates = self.gap_scanner.scan(
            symbols, ohlcv_map, pm_px, pm_vol, eps_set
        ) if pm_px else []
        gap_set = {c.symbol for c in gap_candidates}
        log.info("[Step4] Gap candidates: %d", len(gap_set))

        # ---------------------------------------------------------------
        # STEP 5: Watchlist filtering
        # ---------------------------------------------------------------
        watchlist = self.watchlist_bldr.build(
            symbols, ohlcv_map, spy_df, meta
        )
        watchlist_syms = [e.symbol for e in watchlist]
        watchlist_map  = {e.symbol: e for e in watchlist}
        log.info("[Step5] Watchlist: %d stocks passed", len(watchlist_syms))

        # ---------------------------------------------------------------
        # STEP 6: Theme momentum ranking
        # ---------------------------------------------------------------
        theme_rankings = self.theme_calc.rank(ohlcv_map, spy_df)
        hot_themes = {t.theme for t in theme_rankings if t.momentum_state == "hot"}
        log.info("[Step6] Hot themes: %s", hot_themes)

        # ---------------------------------------------------------------
        # STEP 7 + 8 + 9: Setup detection per stock
        # ---------------------------------------------------------------
        all_signals: list[TradeSignal] = []

        # Check parabolic long first (market-level trigger)
        if cfg.enable_parabolic_long and orb_map:
            parab_signals = self.parabolic_det.scan(
                watchlist_syms, orb_map, spy_df, meta
            )
            for ps in parab_signals:
                wl_entry = watchlist_map.get(ps.symbol)
                risk_per_share = ps.entry_price - ps.stop_price
                risk_pct = self.ec_sizer.get_risk_pct(equity, regime.value)
                risk_dollars   = equity * risk_pct / 100
                shares         = max(1, int(risk_dollars / risk_per_share)) if risk_per_share > 0 else 0
                r_pot          = (ps.target_price - ps.entry_price) / risk_per_share if risk_per_share > 0 else 0
                all_signals.append(TradeSignal(
                    symbol=ps.symbol, setup_type="parabolic_long",
                    direction="long",
                    entry_price=ps.entry_price, stop_price=ps.stop_price,
                    target_price=ps.target_price, stop_pct=ps.stop_pct,
                    shares=shares, risk_dollars=round(risk_dollars, 2),
                    r_potential=round(r_pot, 1), score=ps.score,
                    category=wl_entry.category if wl_entry else "unknown",
                    sector=meta.get(ps.symbol, {}).get("sector", ""),
                    theme=meta.get(ps.symbol, {}).get("theme", ""),
                    candlestick_boost=0.0, total_score=ps.score,
                    notes=ps.notes,
                ))

        for sym in watchlist_syms:
            df = ohlcv_map.get(sym)
            if df is None or len(df) < 60:
                skipped["insufficient_data"] = skipped.get("insufficient_data", 0) + 1
                continue

            wl_entry: WatchlistEntry = watchlist_map[sym]
            sym_meta = meta.get(sym, {})

            # Regime gate
            if cfg.disable_longs_in_bear and regime == MarketRegime.BEAR:
                skipped["bear_regime_no_longs"] = skipped.get("bear_regime_no_longs", 0) + 1
                continue

            # ---- Step 7: Weekly context ----
            try:
                wctx = get_weekly_context(sym, df, spy_df)
                if not wctx.is_long_favourable() and regime != MarketRegime.BEAR:
                    skipped["weekly_context_fail"] = skipped.get("weekly_context_fail", 0) + 1
                    continue
            except Exception:
                pass

            # ---- Step 8: Candlestick patterns ----
            try:
                cs_summary = get_candlestick_summary(sym, df)
                cs_boost = 0.0
                if cs_summary.squeeze_signal:
                    cs_boost = cfg.squeeze_boost
                elif cs_summary.inside_day and cs_summary.nr7:
                    cs_boost = cfg.nr7_boost
                elif cs_summary.inside_day:
                    cs_boost = cfg.inside_day_boost
                elif cs_summary.engulfing_bull or cs_summary.engulfing_bear:
                    cs_boost = cfg.engulfing_boost
            except Exception:
                cs_boost = 0.0

            # ---- Theme boost ----
            sym_theme = sym_meta.get("theme", "")
            theme_boost = 0.05 if sym_theme in hot_themes else 0.0

            # ---- Leader boost ----
            leader_boost = 0.05 if sym in leader_set else 0.0

            # ---- Risk pct from equity curve sizer ----
            risk_pct = self.ec_sizer.get_risk_pct(equity, regime.value)

            # ---- Setup detection ----
            raw_setups: list[tuple[str, float, float, float, float]] = []
            # Returns list of (setup_type, entry, stop, target, score)

            if cfg.enable_pullback and regime != MarketRegime.BEAR:
                try:
                    sig = self.pullback_det.detect(sym, df)
                    if sig and sig.valid:
                        raw_setups.append(("pullback", sig.entry, sig.stop, sig.target, sig.score))
                except Exception:
                    pass

            if cfg.enable_breakout and regime != MarketRegime.BEAR:
                try:
                    sig = self.breakout_det.detect(sym, df)
                    if sig and sig.valid:
                        raw_setups.append(("breakout", sig.entry, sig.stop, sig.target, sig.score))
                except Exception:
                    pass

            if cfg.enable_eps:
                try:
                    eps_sigs = self.eps_det.scan([sym], {sym: df}, eps_set)
                    for es in eps_sigs:
                        raw_setups.append(("eps", es.entry_price, es.stop_price, es.target_3r, es.score))
                except Exception:
                    pass

            if cfg.enable_short and (regime == MarketRegime.BEAR or wl_entry.category == "lagging"):
                try:
                    sig = self.short_det.detect(sym, df)
                    if sig and sig.valid:
                        raw_setups.append(("short", sig.entry, sig.stop, sig.target, sig.score))
                except Exception:
                    pass

            # ---- Build signals ----
            for (setup_type, entry, stop, target, base_score) in raw_setups:
                direction = "short" if setup_type == "short" else "long"
                risk_per_share = abs(entry - stop)
                if risk_per_share <= 0:
                    continue

                risk_dollars   = equity * risk_pct / 100
                shares         = max(1, int(risk_dollars / risk_per_share))
                stop_pct       = risk_per_share / entry * 100
                r_potential    = abs(target - entry) / risk_per_share

                # Portfolio limits check
                can_trade, reasons = self.limits_checker.can_add_trade(
                    sym, wl_entry.sector,
                    shares * entry, equity,
                    direction, wl_entry.category,
                    wl_entry.market_cap,
                )
                if not can_trade:
                    skipped["portfolio_limits"] = skipped.get("portfolio_limits", 0) + 1
                    log.debug("%s blocked: %s", sym, reasons)
                    continue

                total_score = base_score + cs_boost + theme_boost + leader_boost

                all_signals.append(TradeSignal(
                    symbol=sym, setup_type=setup_type, direction=direction,
                    entry_price=round(entry, 2), stop_price=round(stop, 2),
                    target_price=round(target, 2), stop_pct=round(stop_pct, 2),
                    shares=shares, risk_dollars=round(risk_dollars, 2),
                    r_potential=round(r_potential, 1), score=base_score,
                    category=wl_entry.category,
                    sector=wl_entry.sector,
                    theme=sym_theme,
                    candlestick_boost=round(cs_boost, 3),
                    total_score=round(total_score, 3),
                    notes=(
                        f"RS={wl_entry.rs_rank_pct:.0f}% | "
                        f"ADR={wl_entry.adr_pct:.1f}% | "
                        f"category={wl_entry.category}"
                        + (f" | theme={sym_theme}[HOT]" if sym_theme in hot_themes else "")
                        + (f" | LEADER" if sym in leader_set else "")
                    ),
                ))

        # Sort by total_score desc, deduplicate (keep best per symbol)
        all_signals.sort(key=lambda s: s.total_score, reverse=True)
        seen: set[str] = set()
        deduped: list[TradeSignal] = []
        for sig in all_signals:
            if sig.symbol not in seen:
                seen.add(sig.symbol)
                deduped.append(sig)
            if len(deduped) >= cfg.max_signals:
                break

        log.info("[Step9] Final signals: %d", len(deduped))

        import datetime
        return DailyScanResult(
            date=date_str or str(datetime.date.today()),
            regime=regime,
            regime_detail=regime_result,
            market_health=mkt_health,
            top_themes=theme_rankings[:5],
            watchlist_count=len(watchlist_syms),
            signals=deduped,
            skipped_reasons=skipped,
        )
