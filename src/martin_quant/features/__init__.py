"""martin_quant.features package exports."""
from martin_quant.features.atr import compute_atr
from martin_quant.features.candlestick import add_candlestick_features
from martin_quant.features.ema import (
    add_ema_features,
    add_ema_slope_features,
    add_ema_stack_state,
    add_price_vs_ema_distance,
    compute_ema,
)
from martin_quant.features.gap_context import GapContext, GapContextAnalyzer, GapContextConfig, analyze_gap_context
from martin_quant.features.rs_score import add_rs_features, calc_rs_percentile, calc_rs_weighted
from martin_quant.features.volume_quality import add_volume_features, calc_rvol, calc_vol_ratio, is_volume_dry
from martin_quant.features.weekly_context import get_weekly_context

# Backward-compatible alias expected by older imports/tests.
add_weekly_context = get_weekly_context

__all__ = [
    "compute_ema",
    "add_ema_features",
    "add_ema_stack_state",
    "add_ema_slope_features",
    "add_price_vs_ema_distance",
    "compute_atr",
    "add_candlestick_features",
    "GapContext",
    "GapContextAnalyzer",
    "GapContextConfig",
    "analyze_gap_context",
    "get_weekly_context",
    "add_weekly_context",
    "calc_vol_ratio",
    "calc_rvol",
    "is_volume_dry",
    "add_volume_features",
    "calc_rs_weighted",
    "calc_rs_percentile",
    "add_rs_features",
]
