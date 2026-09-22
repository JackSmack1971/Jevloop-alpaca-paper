"""Compose typed judgments into code-owned actions."""
from __future__ import annotations

from dataclasses import dataclass
from .limits import Limits
from .strategy import THRESHOLDS, apply_strategy

KILL = "KILL"
PULL_QUOTES = "PULL_QUOTES"
WIDEN = "WIDEN"
QUOTE_BOTH_SIDES = "QUOTE_BOTH_SIDES"
QUOTE_WIDE = "QUOTE_WIDE"
STAND_DOWN = "STAND_DOWN"


@dataclass(frozen=True)
class Action:
    kind: str
    reason: str
    direction_leg: str | None = None


def compose_action(answers: dict, snapshot: dict, limits: Limits) -> Action:
    if snapshot["drawdown_pct"] > limits.max_drawdown_pct:
        return Action(KILL, "drawdown limit breached")
    if answers["toxic_flow"]["noul"] > THRESHOLDS.toxic_flow_pull_threshold:
        action = Action(PULL_QUOTES, "toxic-flow gate")
    elif answers["liquidity_stressed"]["noul"] > THRESHOLDS.liquidity_stressed_widen_threshold:
        action = Action(WIDEN, "liquidity-stress gate")
    else:
        q = answers["quote_environment"]
        if q["score"] >= THRESHOLDS.quote_env_full_score and q["confidence"] >= THRESHOLDS.quote_env_full_confidence:
            action = Action(QUOTE_BOTH_SIDES, "quote environment strong")
        elif q["score"] >= THRESHOLDS.quote_env_wide_score:
            action = Action(QUOTE_WIDE, "quote environment marginal")
        else:
            action = Action(STAND_DOWN, "quote environment below floor")

    if action.kind in {QUOTE_BOTH_SIDES, QUOTE_WIDE} and THRESHOLDS.enable_directional_leg:
        d = answers["direction"]
        if d["choice"] != "neutral" and d["confidence"] >= THRESHOLDS.direction_confidence_threshold:
            action = Action(action.kind, action.reason, d["choice"])
    return apply_strategy(action, answers, snapshot, limits)


def fallback_action(snapshot: dict, limits: Limits) -> Action:
    """Fail closed when Jev is unavailable/invalid: observe, cancel, submit nothing."""
    if snapshot["drawdown_pct"] > limits.max_drawdown_pct:
        return Action(KILL, "drawdown breach while provider unavailable")
    return Action(STAND_DOWN, "provider unavailable: rules-only observation")
