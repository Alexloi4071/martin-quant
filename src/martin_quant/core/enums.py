from __future__ import annotations

from enum import Enum


class SetupType(str, Enum):
    PULLBACK = "pullback"
    BREAKOUT = "breakout"


class TriggerType(str, Enum):
    RECLAIM = "reclaim"
    OPENING_RANGE_BREAKOUT = "opening_range_breakout"
    AVWAP_RECLAIM = "avwap_reclaim"


class ReviewLabelType(str, Enum):
    A_PLUS = "a_plus"
    A = "a"
    B = "b"
    C = "c"
    SKIP = "skip"
