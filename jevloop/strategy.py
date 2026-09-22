"""Strategy-owned thresholds and optional policy hook.

Defaults are deliberately conservative and directional market orders are off.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyThresholds:
    toxic_flow_pull_threshold: float = 0.60
    liquidity_stressed_widen_threshold: float = 0.70
    quote_env_full_score: float = 2.0
    quote_env_full_confidence: float = 0.80
    quote_env_wide_score: float = 1.0
    direction_confidence_threshold: float = 0.65
    enable_directional_leg: bool = False


THRESHOLDS = StrategyThresholds()


def apply_strategy(action, answers: dict, snapshot: dict, limits):
    """Override/veto hook. Hard risk gates still run after this hook."""
    return action
