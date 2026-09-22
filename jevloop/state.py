"""Deterministic state construction using real provider timestamps."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


def parse_rfc3339(value: str | float | int) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


@dataclass(frozen=True)
class TradeTick:
    ts: float
    price: float
    size: float
    side: str | None = None  # buy/sell when provider exposes aggressor/taker side
    trade_id: str | None = None


@dataclass
class RuntimeState:
    inventory_qty: float = 0.0
    avg_entry_price: float = 0.0
    position_opened_at: float | None = None
    position_state_initialized: bool = False
    account_equity_usd: float = 0.0
    last_equity_usd: float = 0.0
    high_water_mark_usd: float = 0.0
    orders_submitted: int = 0
    orders_rejected: int = 0
    broker_fills_seen: int = 0
    api_error_streak: int = 0
    recent_latencies_ms: list[float] = field(default_factory=list)
    recent_slippage_bps: list[float] = field(default_factory=list)
    observed_vwap_pv: float = 0.0
    observed_vwap_vol: float = 0.0
    seen_trade_ids: set[str] = field(default_factory=set)
    seen_trade_keys: set[tuple[float, float, float]] = field(default_factory=set)
    seen_trade_id_order: deque[str] = field(default_factory=deque)
    seen_trade_key_order: deque[tuple[float, float, float]] = field(default_factory=deque)


def observe_trades(runtime: RuntimeState, trades: list[TradeTick]) -> None:
    """Fold unique observed trades into process-lifetime VWAP accumulators."""
    for t in trades:
        key = t.trade_id if t.trade_id is not None else (t.ts, t.price, t.size)
        seen = runtime.seen_trade_ids if isinstance(key, str) else runtime.seen_trade_keys
        if key in seen or t.price <= 0 or t.size <= 0:
            continue
        seen.add(key)
        if isinstance(key, str):
            runtime.seen_trade_id_order.append(key)
        else:
            runtime.seen_trade_key_order.append(key)
        runtime.observed_vwap_pv += t.price * t.size
        runtime.observed_vwap_vol += t.size
    # Bound memory deterministically by evicting the oldest identities.
    while len(runtime.seen_trade_id_order) > 10_000:
        runtime.seen_trade_ids.discard(runtime.seen_trade_id_order.popleft())
    while len(runtime.seen_trade_key_order) > 10_000:
        runtime.seen_trade_keys.discard(runtime.seen_trade_key_order.popleft())



def record_fill_slippage(runtime: RuntimeState, *, expected_price: float, fill_price: float, side: str) -> None:
    if expected_price <= 0 or fill_price <= 0:
        return
    sign = 1.0 if side == "buy" else -1.0
    bps = sign * (fill_price - expected_price) / expected_price * 10_000
    runtime.recent_slippage_bps.append(round(bps, 4))
    runtime.recent_slippage_bps = runtime.recent_slippage_bps[-10:]

def _window(trades: list[TradeTick], as_of: float, seconds: float) -> list[TradeTick]:
    return sorted((t for t in trades if as_of - seconds <= t.ts <= as_of), key=lambda t: t.ts)


def _return(trades: list[TradeTick], as_of: float, seconds: float) -> float | None:
    pts = _window(trades, as_of, seconds)
    if len(pts) < 2 or pts[0].price <= 0:
        return None
    return pts[-1].price / pts[0].price - 1.0


def _realized_vol(trades: list[TradeTick], as_of: float, seconds: float) -> float | None:
    pts = _window(trades, as_of, seconds)
    if len(pts) < 3:
        return None
    rets = [math.log(b.price / a.price) for a, b in zip(pts, pts[1:]) if a.price > 0 and b.price > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))


def build_snapshot(
    *,
    as_of: float,
    mid: float,
    best_bid: float | None,
    best_ask: float | None,
    bid_depth: list[tuple[float, float]],
    ask_depth: list[tuple[float, float]],
    trades: list[TradeTick],
    runtime: RuntimeState,
    market_data_ts: float,
    max_position_usd: float,
    trade_data_ts: float | None = None,
    has_depth: bool,
) -> dict:
    if mid <= 0:
        raise ValueError("mid must be positive")
    if market_data_ts > as_of + 1.0:
        raise ValueError("pricing-data timestamp is materially in the future")
    if trade_data_ts is not None and trade_data_ts > as_of + 1.0:
        raise ValueError("trade-data timestamp is materially in the future")
    if any(t.ts > as_of + 1e-6 for t in trades):
        raise ValueError("trade tape contains a future observation")

    bid_sz = sum(sz for _, sz in bid_depth[:3])
    ask_sz = sum(sz for _, sz in ask_depth[:3])
    imbalance = None if bid_sz + ask_sz <= 0 else (bid_sz - ask_sz) / (bid_sz + ask_sz)
    spread_bps = None
    if best_bid and best_ask and best_ask >= best_bid:
        spread_bps = (best_ask - best_bid) / mid * 10_000

    flow = [t for t in _window(trades, as_of, 30.0) if t.side in {"buy", "sell"}]
    aggressive_buy_ratio = None
    if flow:
        aggressive_buy_ratio = sum(t.side == "buy" for t in flow) / len(flow)

    inventory_usd = runtime.inventory_qty * mid
    position_age_s = None
    if runtime.inventory_qty == 0:
        position_age_s = 0.0
    elif runtime.position_opened_at is not None:
        position_age_s = max(0.0, as_of - runtime.position_opened_at)
    high_water = max(runtime.high_water_mark_usd, runtime.account_equity_usd)
    runtime.high_water_mark_usd = high_water
    drawdown_pct = 0.0 if high_water <= 0 else max(0.0, (high_water - runtime.account_equity_usd) / high_water)
    daily_pnl = runtime.account_equity_usd - runtime.last_equity_usd if runtime.last_equity_usd else 0.0
    observed_vwap = (
        runtime.observed_vwap_pv / runtime.observed_vwap_vol if runtime.observed_vwap_vol > 0 else None
    )

    return {
        "as_of": as_of,
        "mid": mid,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "observed_vwap": round(observed_vwap, 8) if observed_vwap is not None else None,
        "return_1m": _return(trades, as_of, 60.0),
        "return_5m": _return(trades, as_of, 300.0),
        "return_30m": _return(trades, as_of, 1800.0),
        "realized_vol_5m": _realized_vol(trades, as_of, 300.0),
        "spread_bps": round(spread_bps, 4) if spread_bps is not None else None,
        "has_depth": has_depth,
        "bid_depth_3": [[p, s] for p, s in bid_depth[:3]] if has_depth else [],
        "ask_depth_3": [[p, s] for p, s in ask_depth[:3]] if has_depth else [],
        "imbalance": round(imbalance, 4) if imbalance is not None else None,
        "aggressive_buy_ratio": round(aggressive_buy_ratio, 4) if aggressive_buy_ratio is not None else None,
        "trade_intensity_30s": round(len(_window(trades, as_of, 30.0)) / 30.0, 4),
        "inventory_qty": runtime.inventory_qty,
        "inventory_usd": round(inventory_usd, 4),
        "inventory_utilization": round(abs(inventory_usd) / max_position_usd, 4) if max_position_usd > 0 else None,
        "avg_entry_price": runtime.avg_entry_price or None,
        "position_age_s": round(position_age_s, 3) if position_age_s is not None else None,
        "account_equity_usd": round(runtime.account_equity_usd, 2),
        "daily_pnl_usd": round(daily_pnl, 2),
        "daily_loss_usd": round(max(0.0, -daily_pnl), 2),
        "drawdown_pct": round(drawdown_pct, 6),
        "orders_submitted": runtime.orders_submitted,
        "orders_rejected": runtime.orders_rejected,
        "broker_fills_seen": runtime.broker_fills_seen,
        "last_10_latencies_ms": runtime.recent_latencies_ms[-10:],
        "last_10_slippage_bps": runtime.recent_slippage_bps[-10:],
        "data_age_s": round(max(0.0, as_of - market_data_ts), 3),
        "quote_provider_ts": market_data_ts,
        "trade_provider_ts": trade_data_ts,
        "trade_data_age_s": (
            round(max(0.0, as_of - trade_data_ts), 3) if trade_data_ts is not None else None
        ),
    }
