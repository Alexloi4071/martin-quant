from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .breadth_participation import BreadthParticipationSnapshot
from .martin_market_context import MartinMarketContext


@dataclass
class MartinTradeQualityState:
    state: str
    quality_weight: float
    allow_longs: bool
    allow_shorts: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "quality_weight": round(self.quality_weight, 3),
            "allow_longs": self.allow_longs,
            "allow_shorts": self.allow_shorts,
            "notes": self.notes,
        }


class MartinTradeQualityEvaluator:
    def _evaluate_base(self, market_context: Optional[MartinMarketContext]) -> MartinTradeQualityState:
        if market_context is None:
            return MartinTradeQualityState(
                state="SELECTIVE",
                quality_weight=0.75,
                allow_longs=True,
                allow_shorts=False,
                notes=["missing market context, defaulting to selective mode"],
            )

        notes = list(market_context.notes)
        if market_context.regime == "BULL" and market_context.breakout_friendly and not market_context.trade_less:
            return MartinTradeQualityState(
                state="GO",
                quality_weight=1.0,
                allow_longs=True,
                allow_shorts=False,
                notes=notes + ["full-risk long environment"],
            )

        if market_context.regime == "BEAR" and market_context.short_bias_ok:
            return MartinTradeQualityState(
                state="SELECTIVE",
                quality_weight=0.8,
                allow_longs=False,
                allow_shorts=True,
                notes=notes + ["short-only selective environment"],
            )

        if market_context.avoid_new_shorts_on_open and market_context.trade_less and not market_context.breakout_friendly:
            return MartinTradeQualityState(
                state="OBSERVE_ONLY",
                quality_weight=0.0,
                allow_longs=False,
                allow_shorts=False,
                notes=notes + ["hard gap/weak participation, observation preferred"],
            )

        if market_context.trade_less:
            return MartinTradeQualityState(
                state="SELECTIVE",
                quality_weight=0.7,
                allow_longs=market_context.regime in {"WEAK_BULL", "CHOPPY"},
                allow_shorts=market_context.short_bias_ok,
                notes=notes + ["trade less and demand stronger confirmation"],
            )

        return MartinTradeQualityState(
            state="SELECTIVE",
            quality_weight=0.85,
            allow_longs=True,
            allow_shorts=market_context.short_bias_ok,
            notes=notes + ["mixed but tradable environment"],
        )

    def evaluate(
        self,
        market_context: Optional[MartinMarketContext],
        breadth_snapshot: Optional[BreadthParticipationSnapshot] = None,
    ) -> MartinTradeQualityState:
        base = self._evaluate_base(market_context)
        if breadth_snapshot is None:
            return base

        notes = list(base.notes) + [f"breadth_state={breadth_snapshot.state.lower()}"]
        state = base.state
        weight = base.quality_weight
        allow_longs = base.allow_longs
        allow_shorts = base.allow_shorts

        if breadth_snapshot.state == "EXPANDING":
            if state == "SELECTIVE" and allow_longs and (market_context is None or not market_context.trade_less):
                weight = max(weight, 0.95)
            notes.extend(breadth_snapshot.notes)
            notes.append("leader participation expanding")
        elif breadth_snapshot.state == "SHRINKING":
            if state == "GO":
                state = "SELECTIVE"
            weight = min(weight, 0.75)
            notes.extend(breadth_snapshot.notes)
            notes.append("leader participation shrinking")
        elif breadth_snapshot.state == "VERY_WEAK":
            notes.extend(breadth_snapshot.notes)
            if market_context is not None and market_context.short_bias_ok:
                state = "SELECTIVE"
                weight = min(weight, 0.6)
                allow_longs = False
                allow_shorts = True
                notes.append("leader participation collapsed, favor only tactical shorts")
            else:
                return MartinTradeQualityState(
                    state="OBSERVE_ONLY",
                    quality_weight=0.0,
                    allow_longs=False,
                    allow_shorts=False,
                    notes=notes + ["leader participation collapsed, stand aside"],
                )
        else:
            notes.extend(breadth_snapshot.notes)

        return MartinTradeQualityState(
            state=state,
            quality_weight=weight,
            allow_longs=allow_longs,
            allow_shorts=allow_shorts,
            notes=notes,
        )
