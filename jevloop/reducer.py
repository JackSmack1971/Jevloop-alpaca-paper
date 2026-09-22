"""Pure, deterministic reduction of a validated tick into broker intentions.

The values returned here are descriptions, not executable broker commands.  Keeping
the reducer ignorant of clients and credentials lets the live and simulated paths
share policy while leaving authority at the runtime boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

from .assets import AssetSpec, quantize_quantity, quantity_for_notional
from .evidence import SCHEMA_VERSION
from .ladder import Rung, select_rung
from .limits import Limits
from .policy import (
    KILL,
    QUOTE_BOTH_SIDES,
    QUOTE_WIDE,
    WIDEN,
    Action,
    compose_action,
    fallback_action,
)
from .pricing import bounded_quote_prices
from .risk import RiskVerdict, check as risk_check


@dataclass(frozen=True)
class TickIdentity:
    """Evidence identity carried through every reduction in every runtime mode."""

    run_id: str
    runtime_mode: Literal["dry-real", "paper-real", "simulation"]
    data_source: str
    symbol: str
    provider_route: str | None
    provider_model: str | None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class TickInput:
    identity: TickIdentity
    snapshot: Mapping[str, Any]
    answers: Mapping[str, Any] | None
    asset: AssetSpec
    limits: Limits
    api_error_streak: int = 0
    decision_latency_ms: float | None = None
    decision_late: bool = False
    provider_down: bool = False
    owned_open_orders: int = 0
    rest_counter: int = 0


@dataclass(frozen=True)
class EffectIntent:
    """Inert description of an effect desired by the reducer."""

    kind: Literal["cancel_owned", "flatten", "limit_order"]
    side: Literal["buy", "sell"] | None = None
    quantity: float | None = None
    limit_price: float | None = None


@dataclass(frozen=True)
class IntendedEffect:
    intent: EffectIntent
    risk: RiskVerdict


@dataclass(frozen=True)
class SizedOrderDecision:
    side: Literal["buy", "sell"]
    quantity: float
    limit_price: float
    risk: RiskVerdict


@dataclass(frozen=True)
class TickDecision:
    identity: TickIdentity
    action: Action | None
    action_name: str
    rung: Rung
    risk: RiskVerdict
    order_decisions: tuple[SizedOrderDecision, ...]
    effects: tuple[IntendedEffect, ...]
    rest_counter: int


def _effect(intent: EffectIntent, risk: RiskVerdict) -> IntendedEffect:
    return IntendedEffect(intent, risk)


def reduce_tick(
    value: TickInput,
    *,
    compose: Callable[[dict, dict, Limits], Action] = compose_action,
) -> TickDecision:
    """Reduce validated state and decision data without I/O, clocks, or mutation."""
    snapshot, limits = value.snapshot, value.limits
    action = (
        None
        if value.decision_late
        else (
            fallback_action(dict(snapshot), limits)
            if value.provider_down or value.answers is None
            else compose(dict(value.answers), dict(snapshot), limits)
        )
    )
    hard = risk_check(
        dict(snapshot),
        0.0,
        limits,
        api_error_streak=value.api_error_streak,
        decision_latency_ms=value.decision_latency_ms,
        session_open_orders=value.owned_open_orders,
    )
    confidence = (
        value.answers.get("quote_environment", {}).get("confidence") if value.answers else None
    )
    health = value.answers.get("execution_health", {}).get("score") if value.answers else None
    rung = select_rung(
        risk_kill=hard.kill or (action is not None and action.kind == KILL),
        decision_late=value.decision_late,
        jev_down=value.provider_down,
        decision_confidence=confidence,
        low_confidence_threshold=limits.low_confidence_threshold,
        execution_health_score=health,
        execution_health_floor=limits.execution_health_floor,
        risk_ok=hard.ok,
    )
    cancel = _effect(EffectIntent("cancel_owned"), hard)
    if rung == Rung.KILL:
        return TickDecision(
            value.identity,
            action,
            "KILL",
            rung,
            hard,
            (),
            (cancel, _effect(EffectIntent("flatten"), hard)),
            0,
        )
    if rung in {Rung.HOLD_LATE, Rung.HOLD_BLOCKED, Rung.RULES_ONLY} or action is None:
        name = (
            "HOLD_LATE"
            if rung == Rung.HOLD_LATE
            else (
                "RISK_BLOCK"
                if rung == Rung.HOLD_BLOCKED
                else (action.kind if action else "STAND_DOWN")
            )
        )
        return TickDecision(value.identity, action, name, rung, hard, (), (cancel,), 0)
    if action.kind not in {QUOTE_BOTH_SIDES, QUOTE_WIDE, WIDEN}:
        return TickDecision(value.identity, action, action.kind, rung, hard, (), (cancel,), 0)

    next_rest = value.rest_counter + 1
    if value.owned_open_orders and next_rest < limits.quote_rest_ticks:
        return TickDecision(value.identity, action, action.kind, rung, hard, (), (), next_rest)

    effective_notional = limits.quote_notional_usd * (
        limits.reduce_size_factor if rung == Rung.REDUCE else 1.0
    )
    bid, ask = bounded_quote_prices(
        mid=snapshot["mid"],
        best_bid=snapshot["best_bid"],
        best_ask=snapshot["best_ask"],
        inventory_utilization=snapshot["inventory_utilization"] or 0.0,
        limits=limits,
        spec=value.asset,
        wide=action.kind in {QUOTE_WIDE, WIDEN},
    )
    effects: list[IntendedEffect] = [cancel]
    buy_qty = quantity_for_notional(effective_notional, bid, value.asset)
    buy_risk = risk_check(
        dict(snapshot),
        buy_qty * bid,
        limits,
        api_error_streak=value.api_error_streak,
        decision_latency_ms=value.decision_latency_ms,
        session_open_orders=0,
        projected_inventory_usd=snapshot["inventory_usd"] + buy_qty * bid,
    )
    if buy_risk.ok:
        effects.append(_effect(EffectIntent("limit_order", "buy", buy_qty, bid), buy_risk))
    order_decisions = [SizedOrderDecision("buy", buy_qty, bid, buy_risk)]
    available = max(0.0, snapshot["inventory_qty"])
    sell_qty = quantize_quantity(
        min(quantity_for_notional(effective_notional, ask, value.asset), available), value.asset
    )
    if value.asset.min_order_size is not None and sell_qty < float(value.asset.min_order_size):
        sell_qty = 0.0
    if sell_qty:
        sell_risk = risk_check(
            dict(snapshot),
            sell_qty * ask,
            limits,
            api_error_streak=value.api_error_streak,
            decision_latency_ms=value.decision_latency_ms,
            session_open_orders=1 if buy_risk.ok else 0,
            projected_inventory_usd=snapshot["inventory_usd"] - sell_qty * ask,
        )
        if sell_risk.ok:
            effects.append(_effect(EffectIntent("limit_order", "sell", sell_qty, ask), sell_risk))
        order_decisions.append(SizedOrderDecision("sell", sell_qty, ask, sell_risk))
    return TickDecision(
        value.identity, action, action.kind, rung, hard, tuple(order_decisions), tuple(effects), 0
    )
