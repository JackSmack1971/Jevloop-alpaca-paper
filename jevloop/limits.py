"""Hard safety/operational limits.

Strategy code may become more conservative than these values, never less.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    # Hard risk gates
    max_position_usd: float = 50.0
    max_daily_loss_usd: float = 25.0
    max_drawdown_pct: float = 0.05
    max_order_notional_usd: float = 25.0
    max_inventory_age_s: float = 900.0
    max_stale_data_age_s: float = 5.0
    max_api_error_streak: int = 5
    max_decision_latency_ms: float = 2_000.0
    max_session_open_orders: int = 2

    # Ladder / sizing
    low_confidence_threshold: float = 0.50
    execution_health_floor: float = 1.0
    reduce_size_factor: float = 0.50

    # Default quote construction: unit-transparent bps, not uncalibrated A-S.
    min_half_spread_bps: float = 3.0
    quote_buffer_bps: float = 1.0
    wide_multiplier: float = 2.0
    max_inventory_skew_bps: float = 8.0

    # Operational cadence / account reconciliation
    tick_seconds: float = 5.0
    quote_rest_ticks: int = 3
    quote_notional_usd: float = 15.0
    directional_notional_usd: float = 10.0
