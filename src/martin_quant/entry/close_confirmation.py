from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from martin_quant.features.ema import compute_ema


Relation = Literal["above", "below"]
TradeDirection = Literal["long", "short"]
ConfirmationPhase = Literal["entry", "exit", "generic"]


@dataclass
class CloseConfirmationConfig:
    ema_span: int = 9
    required_bars_long: int = 2
    required_bars_short: int = 1
    entry_required_bars_long: int = 1
    entry_required_bars_short: int = 1
    exit_required_bars_long: Optional[int] = None
    exit_required_bars_short: Optional[int] = None

    def __post_init__(self) -> None:
        if self.exit_required_bars_long is None:
            self.exit_required_bars_long = self.required_bars_long
        if self.exit_required_bars_short is None:
            self.exit_required_bars_short = self.required_bars_short


@dataclass
class CloseConfirmationPolicy:
    phase: ConfirmationPhase
    trade_direction: TradeDirection
    relation: Relation
    required_bars: int
    reference_label: str

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "trade_direction": self.trade_direction,
            "relation": self.relation,
            "required_bars": self.required_bars,
            "reference_label": self.reference_label,
        }


@dataclass
class CloseConfirmationResult:
    confirmed: bool
    relation: Relation
    required_bars: int
    confirmed_bars: int
    reference_label: str
    reference_value: float
    last_close: float
    reason: str
    phase: ConfirmationPhase = "generic"
    trade_direction: str = ""
    policy: Optional[dict[str, object]] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "confirmed": self.confirmed,
            "relation": self.relation,
            "required_bars": self.required_bars,
            "confirmed_bars": self.confirmed_bars,
            "reference_label": self.reference_label,
            "reference_value": self.reference_value,
            "last_close": self.last_close,
            "reason": self.reason,
            "phase": self.phase,
            "trade_direction": self.trade_direction,
            "policy": self.policy,
        }


class CloseConfirmation:
    def __init__(self, config: Optional[CloseConfirmationConfig] = None) -> None:
        self.config = config or CloseConfirmationConfig()

    def policy_for_entry(
        self,
        trade_direction: TradeDirection,
        reference_label: str = "level",
        relation: Optional[Relation] = None,
        required_bars: Optional[int] = None,
    ) -> CloseConfirmationPolicy:
        bars = required_bars
        if bars is None:
            bars = self.config.entry_required_bars_long if trade_direction == "long" else self.config.entry_required_bars_short
        entry_relation: Relation = relation or ("above" if trade_direction == "long" else "below")
        return CloseConfirmationPolicy(
            phase="entry",
            trade_direction=trade_direction,
            relation=entry_relation,
            required_bars=int(bars),
            reference_label=reference_label,
        )

    def policy_for_exit(
        self,
        trade_direction: TradeDirection,
        reference_label: str,
        relation: Optional[Relation] = None,
        required_bars: Optional[int] = None,
    ) -> CloseConfirmationPolicy:
        bars = required_bars
        if bars is None:
            bars = self.config.exit_required_bars_long if trade_direction == "long" else self.config.exit_required_bars_short
        exit_relation: Relation = relation or ("below" if trade_direction == "long" else "above")
        return CloseConfirmationPolicy(
            phase="exit",
            trade_direction=trade_direction,
            relation=exit_relation,
            required_bars=int(bars),
            reference_label=reference_label,
        )

    def confirm_level(
        self,
        df: pd.DataFrame,
        level: float,
        relation: Relation,
        required_bars: int = 1,
        reference_label: str = "level",
        phase: ConfirmationPhase = "generic",
        trade_direction: str = "",
        policy: Optional[CloseConfirmationPolicy] = None,
    ) -> CloseConfirmationResult:
        closes = self._normalized_closes(df)
        if len(closes) < required_bars:
            return CloseConfirmationResult(
                confirmed=False,
                relation=relation,
                required_bars=required_bars,
                confirmed_bars=0,
                reference_label=reference_label,
                reference_value=float(level),
                last_close=float(closes.iloc[-1]) if not closes.empty else 0.0,
                reason="insufficient bars for close confirmation",
                phase=phase,
                trade_direction=trade_direction,
                policy=policy.to_dict() if policy else None,
            )

        recent = closes.tail(required_bars)
        confirmed_bars = sum(1 for close in recent if self._matches(float(close), float(level), relation))
        confirmed = confirmed_bars >= required_bars
        comparator = ">" if relation == "above" else "<"
        reason = f"{confirmed_bars}/{required_bars} closes {comparator} {reference_label}({float(level):.2f})"
        return CloseConfirmationResult(
            confirmed=confirmed,
            relation=relation,
            required_bars=required_bars,
            confirmed_bars=confirmed_bars,
            reference_label=reference_label,
            reference_value=float(level),
            last_close=float(closes.iloc[-1]),
            reason=reason,
            phase=phase,
            trade_direction=trade_direction,
            policy=policy.to_dict() if policy else None,
        )

    def confirm_ema(
        self,
        df: pd.DataFrame,
        relation: Relation,
        span: Optional[int] = None,
        required_bars: int = 1,
        phase: ConfirmationPhase = "generic",
        trade_direction: str = "",
        policy: Optional[CloseConfirmationPolicy] = None,
    ) -> CloseConfirmationResult:
        closes = self._normalized_closes(df)
        ema_span = span or self.config.ema_span
        if len(closes) < max(required_bars, ema_span):
            return CloseConfirmationResult(
                confirmed=False,
                relation=relation,
                required_bars=required_bars,
                confirmed_bars=0,
                reference_label=f"EMA{ema_span}",
                reference_value=0.0,
                last_close=float(closes.iloc[-1]) if not closes.empty else 0.0,
                reason="insufficient bars for EMA close confirmation",
                phase=phase,
                trade_direction=trade_direction,
                policy=policy.to_dict() if policy else None,
            )

        ema = compute_ema(closes, ema_span)
        recent_closes = closes.tail(required_bars)
        recent_ema = ema.tail(required_bars)
        confirmed_bars = 0
        for close, ema_value in zip(recent_closes, recent_ema):
            if pd.isna(ema_value):
                continue
            if self._matches(float(close), float(ema_value), relation):
                confirmed_bars += 1
        last_ema = float(ema.iloc[-1]) if not pd.isna(ema.iloc[-1]) else 0.0
        comparator = ">" if relation == "above" else "<"
        return CloseConfirmationResult(
            confirmed=confirmed_bars >= required_bars,
            relation=relation,
            required_bars=required_bars,
            confirmed_bars=confirmed_bars,
            reference_label=f"EMA{ema_span}",
            reference_value=last_ema,
            last_close=float(closes.iloc[-1]),
            reason=f"{confirmed_bars}/{required_bars} closes {comparator} EMA{ema_span}({last_ema:.2f})",
            phase=phase,
            trade_direction=trade_direction,
            policy=policy.to_dict() if policy else None,
        )

    def confirm_entry_level(
        self,
        df: pd.DataFrame,
        trade_direction: TradeDirection,
        level: float,
        reference_label: str = "level",
        required_bars: Optional[int] = None,
    ) -> CloseConfirmationResult:
        policy = self.policy_for_entry(
            trade_direction=trade_direction,
            reference_label=reference_label,
            required_bars=required_bars,
        )
        return self.confirm_level(
            df=df,
            level=level,
            relation=policy.relation,
            required_bars=policy.required_bars,
            reference_label=policy.reference_label,
            phase=policy.phase,
            trade_direction=policy.trade_direction,
            policy=policy,
        )

    def confirm_trade_failure(
        self,
        df: pd.DataFrame,
        trade_direction: TradeDirection,
        span: Optional[int] = None,
        required_bars: Optional[int] = None,
    ) -> CloseConfirmationResult:
        ema_span = span or self.config.ema_span
        policy = self.policy_for_exit(
            trade_direction=trade_direction,
            reference_label=f"EMA{ema_span}",
            required_bars=required_bars,
        )
        return self.confirm_ema(
            df=df,
            relation=policy.relation,
            span=ema_span,
            required_bars=policy.required_bars,
            phase=policy.phase,
            trade_direction=policy.trade_direction,
            policy=policy,
        )

    def confirm_trade_entry(
        self,
        df: pd.DataFrame,
        trade_direction: TradeDirection,
        span: Optional[int] = None,
        required_bars: Optional[int] = None,
    ) -> CloseConfirmationResult:
        ema_span = span or self.config.ema_span
        policy = self.policy_for_entry(
            trade_direction=trade_direction,
            reference_label=f"EMA{ema_span}",
            required_bars=required_bars,
        )
        return self.confirm_ema(
            df=df,
            relation=policy.relation,
            span=ema_span,
            required_bars=policy.required_bars,
            phase=policy.phase,
            trade_direction=policy.trade_direction,
            policy=policy,
        )

    @staticmethod
    def _normalized_closes(df: pd.DataFrame) -> pd.Series:
        if "close" in df.columns:
            return pd.to_numeric(df["close"], errors="coerce")
        lowered = {str(col).lower(): col for col in df.columns}
        close_col = lowered.get("close")
        if close_col is None:
            raise KeyError("close column not found")
        return pd.to_numeric(df[close_col], errors="coerce")

    @staticmethod
    def _matches(close: float, reference: float, relation: Relation) -> bool:
        if relation == "above":
            return close > reference
        return close < reference
