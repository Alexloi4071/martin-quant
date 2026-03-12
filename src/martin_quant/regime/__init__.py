"""regime — Market regime detection & sector filtering"""
try:
    from .market_regime import MarketRegimeDetector, RegimeResult
except ImportError:
    MarketRegimeDetector = None  # type: ignore
    RegimeResult = None          # type: ignore

from .sector_regime_filter import (
    SectorRegimeFilter,
    SectorFilterResult,
    SECTOR_ETF_MAP,
    REGIME_SECTOR_CONFIG,
)

__all__ = [
    "MarketRegimeDetector",
    "RegimeResult",
    "SectorRegimeFilter",
    "SectorFilterResult",
    "SECTOR_ETF_MAP",
    "REGIME_SECTOR_CONFIG",
]
