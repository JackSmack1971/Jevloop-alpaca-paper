"""Hard deterministic order gate."""
from __future__ import annotations

from dataclasses import dataclass
from .limits import Limits


@dataclass(frozen=True)
class RiskVerdict:
    ok: bool
    veto: str | None = None
    kill: bool = False


def check(
    snapshot: dict,
    order_notional_usd: float,
    limits: Limits,
    *,
    api_error_streak: int,
    decision_latency_ms: float | None,
    session_open_orders: int,
    projected_inventory_usd: float | None = None,
) -> RiskVerdict:
    if snapshot["drawdown_pct"] > limits.max_drawdown_pct:
        return RiskVerdict(False, "max_drawdown breached", True)
    if abs(snapshot["inventory_usd"]) > limits.max_position_usd:
        return RiskVerdict(False, "max_position_usd breached", True)
    if projected_inventory_usd is not None and abs(projected_inventory_usd) > limits.max_position_usd:
        return RiskVerdict(False, "projected max_position_usd breached")
    if snapshot["daily_loss_usd"] > limits.max_daily_loss_usd:
        return RiskVerdict(False, "max_daily_loss_usd breached", True)
    if order_notional_usd > limits.max_order_notional_usd:
        return RiskVerdict(False, "max_order_notional_usd breached")
    if snapshot["inventory_qty"] and snapshot["position_age_s"] is None:
        return RiskVerdict(False, "inventory age unknown; new exposure refused")
    if snapshot["inventory_qty"] and snapshot["position_age_s"] > limits.max_inventory_age_s:
        return RiskVerdict(False, "max_inventory_age_s breached")
    if snapshot["data_age_s"] > limits.max_stale_data_age_s:
        return RiskVerdict(False, "market data stale")
    if api_error_streak > limits.max_api_error_streak:
        return RiskVerdict(False, "max_api_error_streak breached", True)
    if decision_latency_ms is not None and decision_latency_ms > limits.max_decision_latency_ms:
        return RiskVerdict(False, "decision latency breached")
    if session_open_orders >= limits.max_session_open_orders:
        return RiskVerdict(False, "max_session_open_orders reached")
    return RiskVerdict(True)
