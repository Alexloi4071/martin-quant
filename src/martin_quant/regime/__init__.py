"""regime package exports and compatibility wrappers."""
from __future__ import annotations

from dataclasses import dataclass

from martin_quant.filters.market_regime import MarketRegimeFilter
from .breadth_participation import (
    BreadthParticipationAnalyzer,
    BreadthParticipationConfig,
    BreadthParticipationSnapshot,
)
from .martin_market_context import MartinMarketContext, MartinMarketContextConfig, MartinMarketContextEvaluator
from .sector_regime_filter import (
    SectorRegimeFilter,
    SectorFilterResult,
    SECTOR_ETF_MAP,
    REGIME_SECTOR_CONFIG,
    normalize_sector_name,
)
from .sector_relative_strength import (
    DynamicSectorRelativeStrengthAnalyzer,
    SectorRelativeStrengthConfig,
    SectorStrengthSnapshot,
)
from .trade_quality_state import MartinTradeQualityEvaluator, MartinTradeQualityState


@dataclass
class RegimeResult:
    regime: str
    confidence: float


class MarketRegimeDetector:
    """Compatibility wrapper around MarketRegimeFilter."""

    def __init__(self) -> None:
        self._filter = MarketRegimeFilter()

    def detect(self, spy_df=None, iwm_df=None) -> RegimeResult:
        if spy_df is None or iwm_df is None:
            raise ValueError("spy_df and iwm_df are required for regime detection")
        state = self._filter.evaluate(spy_df=spy_df, iwm_df=iwm_df)
        mapping = {"bull": "BULL", "neutral": "CHOPPY", "bear": "BEAR"}
        return RegimeResult(regime=mapping.get(state.regime.value, "CHOPPY"), confidence=70.0)


__all__ = [
    "MarketRegimeDetector",
    "RegimeResult",
    "BreadthParticipationAnalyzer",
    "BreadthParticipationConfig",
    "BreadthParticipationSnapshot",
    "MartinMarketContext",
    "MartinMarketContextConfig",
    "MartinMarketContextEvaluator",
    "MartinTradeQualityEvaluator",
    "MartinTradeQualityState",
    "SectorRegimeFilter",
    "SectorFilterResult",
    "SECTOR_ETF_MAP",
    "REGIME_SECTOR_CONFIG",
    "normalize_sector_name",
    "DynamicSectorRelativeStrengthAnalyzer",
    "SectorRelativeStrengthConfig",
    "SectorStrengthSnapshot",
]
