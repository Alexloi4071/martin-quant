"""martin_quant.features  — feature engineering modules"""
from martin_quant.features.ema import (
    compute_ema,
    add_ema_features,
    add_ema_stack_state,
    add_ema_slope_features,
    add_price_vs_ema_distance,
)
from martin_quant.features.atr import compute_atr
from martin_quant.features.candlestick import add_candlestick_features
from martin_quant.features.weekly_context import add_weekly_context
from martin_quant.features.volume_quality import (
    calc_vol_ratio,
    calc_rvol,
    is_volume_dry,
    add_volume_features,
)
from martin_quant.features.rs_score import (
    calc_rs_weighted,
    calc_rs_percentile,
    add_rs_features,
)

__all__ = [
    # EMA
    "compute_ema",
    "add_ema_features",
    "add_ema_stack_state",
    "add_ema_slope_features",
    "add_price_vs_ema_distance",
    # ATR
    "compute_atr",
    # Candlestick
    "add_candlestick_features",
    # Weekly
    "add_weekly_context",
    # Volume Quality (NEW Batch 15)
    "calc_vol_ratio",
    "calc_rvol",
    "is_volume_dry",
    "add_volume_features",
    # RS Score (NEW Batch 15)
    "calc_rs_weighted",
    "calc_rs_percentile",
    "add_rs_features",
]
