from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from martin_quant.regime.breadth_participation import BreadthParticipationSnapshot
from martin_quant.risk import PortfolioLimitsChecker, PositionSizer
from martin_quant.risk.portfolio_limits import OpenPosition


@dataclass
class ExecutionPlannerConfig:
    max_new_trades: int = 5
    min_total_score: float = 0.60
    min_position_pct: float = 0.03
    active_tier_a_score: float = 0.85
    active_tier_b_score: float = 0.75


@dataclass
class ExecutionPlan:
    symbol: str
    direction: str
    setup_type: str
    priority_rank: int
    priority_tier: str
    action: str
    execution_style: str
    order_style: str
    regime: str
    trade_quality_state: str
    breadth_state: str
    sector_strength_state: str
    sector: str
    total_score: float
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    entry_confirmation_mode: str = ""
    entry_confirmation_bars: int = 0
    entry_confirmation_reason: str = ""
    entry_confirmation: dict[str, object] | None = None
    shares: int = 0
    dollar_size: float = 0.0
    position_pct: float = 0.0
    risk_dollars: float = 0.0
    risk_pct: float = 0.0
    category: str = "mediocre"
    active: bool = False
    block_reason: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "setup_type": self.setup_type,
            "priority_rank": self.priority_rank,
            "priority_tier": self.priority_tier,
            "action": self.action,
            "execution_style": self.execution_style,
            "order_style": self.order_style,
            "regime": self.regime,
            "trade_quality_state": self.trade_quality_state,
            "breadth_state": self.breadth_state,
            "sector_strength_state": self.sector_strength_state,
            "sector": self.sector,
            "total_score": round(self.total_score, 3),
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "entry_confirmation_mode": self.entry_confirmation_mode,
            "entry_confirmation_bars": self.entry_confirmation_bars,
            "entry_confirmation_reason": self.entry_confirmation_reason,
            "entry_confirmation": self.entry_confirmation,
            "shares": self.shares,
            "dollar_size": round(self.dollar_size, 2),
            "position_pct": round(self.position_pct, 4),
            "risk_dollars": round(self.risk_dollars, 2),
            "risk_pct": round(self.risk_pct, 4),
            "category": self.category,
            "active": self.active,
            "block_reason": self.block_reason,
            "notes": self.notes,
        }


@dataclass
class ExecutionPlanBundle:
    as_of: str
    equity: float
    regime: str
    trade_quality_state: str
    breadth_state: str
    active_plans: list[ExecutionPlan] = field(default_factory=list)
    blocked_plans: list[ExecutionPlan] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        total_exposure = sum(item.position_pct for item in self.active_plans)
        total_risk = sum(item.risk_pct for item in self.active_plans)
        confirmation_count = sum(1 for item in self.active_plans if item.entry_confirmation is not None)
        return {
            "as_of": self.as_of,
            "equity": self.equity,
            "regime": self.regime,
            "trade_quality_state": self.trade_quality_state,
            "breadth_state": self.breadth_state,
            "active_count": len(self.active_plans),
            "blocked_count": len(self.blocked_plans),
            "planned_exposure_pct": round(total_exposure * 100, 1),
            "planned_risk_pct": round(total_risk * 100, 2),
            "confirmed_entry_count": confirmation_count,
            "notes": self.notes,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary(),
            "active_plans": [item.to_dict() for item in self.active_plans],
            "blocked_plans": [item.to_dict() for item in self.blocked_plans],
        }


class ExecutionPlanner:
    def __init__(self, config: Optional[ExecutionPlannerConfig] = None) -> None:
        self.config = config or ExecutionPlannerConfig()
        self._sizer = PositionSizer()
        self._limits = PortfolioLimitsChecker()

    def build_plan(
        self,
        results: Iterable[object],
        as_of: str,
        equity: float,
        regime: str,
        trade_quality_state: str,
        trade_quality_weight: float,
        breadth_snapshot: Optional[BreadthParticipationSnapshot] = None,
        market_caps: Optional[dict[str, float]] = None,
    ) -> ExecutionPlanBundle:
        cfg = self.config
        self._limits = PortfolioLimitsChecker()
        caps = market_caps or {}
        breadth_state = breadth_snapshot.state if breadth_snapshot is not None else "UNKNOWN"
        exposure_factor = trade_quality_weight
        if breadth_snapshot is not None:
            exposure_factor *= breadth_snapshot.exposure_factor
        exposure_factor = max(0.25, min(exposure_factor, 1.0))

        regime_key = str(regime or "CHOPPY").upper()
        if regime_key in {"BULL", "WEAK_BULL"}:
            self._limits.set_market_regime("bull")
        elif regime_key == "BEAR":
            self._limits.set_market_regime("bear")
        else:
            self._limits.set_market_regime("neutral")

        bundle = ExecutionPlanBundle(
            as_of=as_of,
            equity=equity,
            regime=regime,
            trade_quality_state=trade_quality_state,
            breadth_state=breadth_state,
            notes=[f"exposure_factor={exposure_factor:.2f}"] + ([f"breadth={breadth_state.lower()}"] if breadth_snapshot is not None else []),
        )

        active_count = 0
        ranked_results = sorted(results, key=lambda item: float(getattr(item, "total_score", 0.0)), reverse=True)
        for rank, result in enumerate(ranked_results, 1):
            total_score = float(getattr(result, "total_score", 0.0))
            direction = str(getattr(result, "direction", "long")).lower()
            sector_strength_state = str(getattr(result, "sector_strength_state", "UNKNOWN"))
            sector = str(getattr(result, "sector", ""))
            entry_confirmation = self._entry_confirmation(result)
            plan = ExecutionPlan(
                symbol=str(getattr(result, "symbol", "")).upper(),
                direction=direction,
                setup_type=str(getattr(result, "setup_type", "unknown")),
                priority_rank=rank,
                priority_tier=self._priority_tier(total_score),
                action="BUY" if direction == "long" else "SELL",
                execution_style=self._execution_style(result),
                order_style="limit_bracket" if getattr(result, "entry_price", None) is not None else "watch_only",
                regime=regime,
                trade_quality_state=trade_quality_state,
                breadth_state=breadth_state,
                sector_strength_state=sector_strength_state,
                sector=sector,
                total_score=total_score,
                entry_price=getattr(result, "entry_price", None),
                stop_price=getattr(result, "stop_price", None),
                target_price=getattr(result, "target_price", None),
                entry_confirmation_mode=str(entry_confirmation.get("mode", "")) if entry_confirmation else "",
                entry_confirmation_bars=int(entry_confirmation.get("required_bars", 0)) if entry_confirmation else 0,
                entry_confirmation_reason=str(entry_confirmation.get("reason", "")) if entry_confirmation else "",
                entry_confirmation=entry_confirmation,
                category=self._category_for(result),
                notes=self._notes_for(result, entry_confirmation=entry_confirmation),
            )

            if total_score < cfg.min_total_score:
                plan.block_reason = "score_below_execution_threshold"
                bundle.blocked_plans.append(plan)
                continue
            if active_count >= cfg.max_new_trades:
                plan.block_reason = "max_new_trades_reached"
                bundle.blocked_plans.append(plan)
                continue
            if plan.entry_price is None or plan.stop_price is None or plan.target_price is None:
                plan.block_reason = "missing_trade_levels"
                bundle.blocked_plans.append(plan)
                continue

            sizing = self._sizer.size(
                symbol=plan.symbol,
                entry_price=float(plan.entry_price),
                stop_price=float(plan.stop_price),
                equity=equity,
                regime=regime,
                exposure_factor=exposure_factor,
                market_cap=caps.get(plan.symbol),
                current_exposure=sum(item.position_pct for item in bundle.active_plans),
                r_multiple_target=self._r_multiple_target(plan),
                direction=direction,
            )
            if sizing is None:
                plan.block_reason = "position_sizer_rejected"
                bundle.blocked_plans.append(plan)
                continue

            if sizing.position_pct < cfg.min_position_pct:
                plan.block_reason = "position_too_small"
                bundle.blocked_plans.append(plan)
                continue

            can_add, reasons = self._limits.can_add_trade(
                symbol=plan.symbol,
                sector=plan.sector,
                new_position_value=sizing.dollar_size,
                equity=equity,
                direction=direction,
                category=plan.category,
                market_cap=caps.get(plan.symbol),
            )
            if not can_add:
                plan.block_reason = " | ".join(reasons)
                bundle.blocked_plans.append(plan)
                continue

            plan.active = True
            plan.shares = sizing.shares
            plan.dollar_size = sizing.dollar_size
            plan.position_pct = sizing.position_pct
            plan.risk_dollars = sizing.risk_dollars
            plan.risk_pct = sizing.risk_pct
            self._limits.add_position(
                OpenPosition(
                    symbol=plan.symbol,
                    sector=plan.sector,
                    position_value=plan.dollar_size,
                    entry_price=float(plan.entry_price),
                    shares=plan.shares,
                    direction=direction,
                    category=plan.category,
                    market_cap=caps.get(plan.symbol),
                )
            )
            bundle.active_plans.append(plan)
            active_count += 1

        return bundle

    def _priority_tier(self, total_score: float) -> str:
        cfg = self.config
        if total_score >= cfg.active_tier_a_score:
            return "A"
        if total_score >= cfg.active_tier_b_score:
            return "B"
        return "C"

    def _execution_style(self, result: object) -> str:
        direction = str(getattr(result, "direction", "long")).lower()
        if direction == "long" and getattr(result, "orb_signal", None) is not None:
            return "orb_breakout_after_first_15m"
        if direction == "short" and getattr(result, "timing_signal", None) is not None:
            return "short_retest_breakdown_after_bounce"
        if direction == "short":
            return "short_only_on_failed_bounce"
        if str(getattr(result, "trade_quality_state", "GO")) != "GO":
            return "wait_for_close_confirmation"
        return "buy_strength_with_confirmation"

    def _category_for(self, result: object) -> str:
        direction = str(getattr(result, "direction", "long")).lower()
        sector_state = str(getattr(result, "sector_strength_state", "UNKNOWN")).upper()
        score = float(getattr(result, "total_score", 0.0))
        if direction == "short":
            return "lagging"
        if sector_state == "STRONG" and score >= 0.80:
            return "leading"
        if sector_state == "WEAK":
            return "mediocre"
        return "leading" if score >= 0.85 else "mediocre"

    def _r_multiple_target(self, plan: ExecutionPlan) -> float:
        if plan.entry_price is None or plan.stop_price is None or plan.target_price is None:
            return 2.0
        risk = abs(float(plan.entry_price) - float(plan.stop_price))
        if risk <= 0:
            return 2.0
        reward = abs(float(plan.target_price) - float(plan.entry_price))
        return max(1.5, reward / risk)

    def _entry_confirmation(self, result: object) -> dict[str, object] | None:
        orb_signal = getattr(result, "orb_signal", None)
        if orb_signal is not None:
            reason = str(getattr(orb_signal, "confirmation_reason", "")).strip()
            bars = int(getattr(orb_signal, "confirmation_bars", 0) or 0)
            mode = str(getattr(orb_signal, "confirmation_mode", "bar_close"))
            if reason or bars:
                return {
                    "source": "orb",
                    "mode": mode,
                    "required_bars": bars,
                    "reason": reason,
                }

        timing_signal = getattr(result, "timing_signal", None)
        if timing_signal is not None:
            context = getattr(timing_signal, "context", {}) or {}
            confirmation = context.get("entry_confirmation")
            if isinstance(confirmation, dict):
                payload = dict(confirmation)
                payload.setdefault("source", "timing_signal")
                payload["mode"] = "bar_close"
                return payload
        return None

    def _notes_for(self, result: object, entry_confirmation: dict[str, object] | None = None) -> list[str]:
        notes = []
        note_text = str(getattr(result, "entry_note", "")).strip()
        if note_text:
            notes.extend([item.strip() for item in note_text.split(',') if item.strip()])
        breadth_state = str(getattr(result, "breadth_state", "UNKNOWN"))
        sector_state = str(getattr(result, "sector_strength_state", "UNKNOWN"))
        if breadth_state != "UNKNOWN":
            notes.append(f"breadth={breadth_state.lower()}")
        if sector_state != "UNKNOWN":
            notes.append(f"sector_rs={sector_state.lower()}")
        if entry_confirmation is not None:
            source = str(entry_confirmation.get("source", "entry_confirmation"))
            bars = int(entry_confirmation.get("required_bars", 0) or 0)
            notes.append(f"entry_confirmation={source}:{bars}bar")
        return notes


def export_execution_plan_bundle(bundle: ExecutionPlanBundle, out_dir: str = "outputs/signals") -> dict[str, str]:
    base_dir = Path(out_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    snapshot = bundle.as_of or "latest"

    json_path = base_dir / f"execution_plan_{snapshot}.json"
    csv_path = base_dir / f"execution_plan_{snapshot}.csv"

    json_path.write_text(json.dumps(bundle.to_dict(), indent=2, ensure_ascii=True), encoding="utf-8")

    fieldnames = list(ExecutionPlan(symbol="", direction="", setup_type="", priority_rank=0, priority_tier="", action="", execution_style="", order_style="", regime="", trade_quality_state="", breadth_state="", sector_strength_state="", sector="", total_score=0.0, entry_price=None, stop_price=None, target_price=None).to_dict().keys())
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for plan in bundle.active_plans + bundle.blocked_plans:
            writer.writerow(plan.to_dict())

    return {"json": str(json_path), "csv": str(csv_path)}
