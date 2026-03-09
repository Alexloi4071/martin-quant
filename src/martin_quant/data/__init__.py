"""data/__init__.py  — unified data provider interface

Usage:
    from martin_quant.data import get_provider

    # Crypto
    p = get_provider("binance")
    df = p.get_daily("BTCUSDT", limit=500)

    # US Equities
    p = get_provider("ibkr")   # falls back to yfinance if TWS not running
    df = p.get_daily("NVDA", days=365)

    # Pre-market
    p = get_provider("premarket")
    px = p.get_premarket_prices(["NVDA", "SMCI"])
"""
from martin_quant.data.binance_provider import BinanceProvider
from martin_quant.data.ibkr_provider import IBKRProvider
from martin_quant.data.premarket_provider import PremarketProvider

_REGISTRY = {
    "binance":   BinanceProvider,
    "ibkr":      IBKRProvider,
    "premarket": PremarketProvider,
}


def get_provider(name: str, **kwargs):
    """Factory: get_provider("binance") → BinanceProvider instance."""
    cls = _REGISTRY.get(name.lower())
    if cls is None:
        raise ValueError(f"Unknown provider '{name}'. Available: {list(_REGISTRY)}")
    return cls(**kwargs)


__all__ = [
    "BinanceProvider",
    "IBKRProvider",
    "PremarketProvider",
    "get_provider",
]
