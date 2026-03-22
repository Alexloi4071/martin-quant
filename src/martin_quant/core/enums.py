from __future__ import annotations

from enum import Enum


class SetupType(str, Enum):
    PULLBACK = "pullback"
    BREAKOUT = "breakout"
    BREAKDOWN = "breakdown"
    SHORT_RESISTANCE_REVERSAL = "short_resistance_reversal"
    FAILED_BREAKOUT_SHORT = "failed_breakout_short"
    PARABOLIC_SHORT = "parabolic_short"
    BREAKDOWN_CONTINUATION_SHORT = "breakdown_continuation_short"


class TriggerType(str, Enum):
    RECLAIM = "reclaim"
    OPENING_RANGE_BREAKOUT = "opening_range_breakout"
    AVWAP_RECLAIM = "avwap_reclaim"
    SHORT_RETEST_BREAKDOWN = "short_retest_breakdown"


class ReviewLabelType(str, Enum):
    A_PLUS = "a_plus"
    A = "a"
    B = "b"
    C = "c"
    SKIP = "skip"
