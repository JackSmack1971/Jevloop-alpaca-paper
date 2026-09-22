"""Paper-first decision loop with broker-authoritative state reconciliation.

This is intentionally a seconds-scale REST scaffold, not an exchange-grade HFT
engine. It never infers fills from order submission and never cancels orders it
does not own.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from .assets import AssetSpec, quantize_quantity, quantity_for_notional
from .battery import run_battery
from .client import DecisionClientError, DecisionSchemaError
from .execution.alpaca import (
    TERMINAL_ORDER_STATES,
    AlpacaAPIError,
    MarketClosedError,
    UnknownOrderOutcome,
)
from .ladder import Rung, select_rung
from .limits import Limits
from .policy import KILL, PULL_QUOTES, QUOTE_BOTH_SIDES, QUOTE_WIDE, STAND_DOWN, WIDEN, compose_action, fallback_action
from .pricing import bounded_quote_prices
from .preflight import FOREIGN_SESSION_ORDERS, paper_preflight
from .risk import check as risk_check
from .state import RuntimeState, TradeTick, build_snapshot, observe_trades, record_fill_slippage

LOG_DIR = Path(os.getenv("JEV_LOOP_HOME", str(Path.home() / ".jev-loop")))
LOG_FILE = LOG_DIR / "log.jsonl"
LATEST_FILE = LOG_DIR / "latest.json"
LATEST_WINDOW = 120


class _StopRequested(Exception):
    pass


def _handle_sigterm(signum, frame):  # noqa: ARG001
    raise _StopRequested()


def _merge_trades(history: list[TradeTick], new: list[TradeTick], as_of: float) -> list[TradeTick]:
    merged: dict[object, TradeTick] = {}
    for t in history + new:
        if t.ts > as_of + 1e-6 or t.ts < as_of - 3600.0:
            continue
        key: object = ("id", t.trade_id) if t.trade_id else (t.ts, t.price, t.size)
        merged[key] = t
    return sorted(merged.values(), key=lambda t: t.ts)


def _sync_account(alpaca, runtime: RuntimeState, now: float) -> None:
    account = alpaca.get_account()
    equity = float(account.get("equity") or 0.0)
    previous_session_equity = float(account.get("last_equity") or equity)
    runtime.account_equity_usd = equity
    runtime.last_equity_usd = previous_session_equity
    runtime.high_water_mark_usd = max(runtime.high_water_mark_usd, equity)

    previous_qty = runtime.inventory_qty
    was_initialized = runtime.position_state_initialized
    position = alpaca.get_position()
    if position:
        qty = float(position.get("qty") or 0.0)
        side = str(position.get("side") or "long").lower()
        if side == "short" and qty > 0:
            qty = -qty
        runtime.inventory_qty = qty
        runtime.avg_entry_price = float(position.get("avg_entry_price") or 0.0)
    else:
        runtime.inventory_qty = 0.0
        runtime.avg_entry_price = 0.0

    if runtime.inventory_qty == 0:
        runtime.position_opened_at = None
    elif was_initialized and previous_qty == 0:
        runtime.position_opened_at = now
    elif not was_initialized:
        # A pre-existing position's true age is not inferable from /v2/positions.
        runtime.position_opened_at = None
    runtime.position_state_initialized = True


def _owned_open_orders(alpaca) -> list[dict]:
    return [o for o in alpaca.get_open_orders() if alpaca.is_owned_order(o, alpaca.session_id)]


@dataclass(frozen=True)
class ReconciliationResult:
    """Evidence from one ownership-scoped cancellation and broker verification."""

    cancelled: int
    cancel_error: str | None
    verification_error: str | None
    unresolved_orders: tuple[dict[str, str | None], ...]

    @property
    def verified(self) -> bool:
        return self.verification_error is None and not self.unresolved_orders

    def as_dict(self) -> dict:
        return {
            "cancelled": self.cancelled,
            "cancel_error": self.cancel_error,
            "verification_error": self.verification_error,
            "verified": self.verified,
            "unresolved_orders": list(self.unresolved_orders),
        }


def _reconcile_session_orders(alpaca, *, dry: bool, verify_attempts: int = 3) -> ReconciliationResult:
    """Cancel only current-session orders, then independently verify broker state."""
    if dry:
        return ReconciliationResult(0, None, None, ())

    cancelled = 0
    cancel_error = None
    try:
        cancelled = len(alpaca.cancel_session_orders())
    except BaseException as exc:  # Cleanup must never replace the failure being handled.
        cancel_error = f"{type(exc).__name__}: {exc}"

    remaining: list[dict] = []
    verification_error = None
    for attempt in range(max(1, verify_attempts)):
        try:
            remaining = _owned_open_orders(alpaca)
        except BaseException as exc:  # Verification failure is distinct from cancellation failure.
            verification_error = f"{type(exc).__name__}: {exc}"
            break
        if not remaining:
            break
        if attempt + 1 < verify_attempts:
            time.sleep(0.1)

    identities = tuple(
        {
            "id": str(order.get("id")) if order.get("id") is not None else None,
            "client_order_id": (
                str(order.get("client_order_id")) if order.get("client_order_id") is not None else None
            ),
        }
        for order in remaining
    )
    return ReconciliationResult(cancelled, cancel_error, verification_error, identities)


def _cancel_owned(alpaca, *, dry: bool, verify_attempts: int = 3) -> tuple[int, str | None]:
    """Cancel this session's orders and verify no owned open order remains.

    A cancellation request is not treated as a completed cancellation. Requoting and
    emergency flattening are blocked when broker reconciliation is incomplete.
    """
    result = _reconcile_session_orders(alpaca, dry=dry, verify_attempts=verify_attempts)
    errors = []
    if result.cancel_error:
        errors.append(f"cancellation failed: {result.cancel_error}")
    if result.verification_error:
        errors.append(f"verification failed: {result.verification_error}")
    if result.unresolved_orders:
        errors.append(f"owned orders still open after cancellation: {list(result.unresolved_orders)}")
    return result.cancelled, "; ".join(errors) or None


def _refresh_order_statuses(alpaca, runtime: RuntimeState, pending_orders: dict[str, dict]) -> None:
    """Convert broker-observed terminal states into execution-health evidence.

    `done_for_day` is intentionally not in `TERMINAL_ORDER_STATES`: Alpaca documents it
    as a same-day pause, not order closure, so such orders stay in `pending_orders` and
    keep being polled rather than being forgotten until the next trading session.
    """
    for order_id in list(pending_orders):
        order = alpaca.get_order(order_id)
        status = str(order.get("status") or "")
        if status not in TERMINAL_ORDER_STATES:
            continue
        meta = pending_orders.pop(order_id)
        if hasattr(alpaca, "mark_order_terminal"):
            alpaca.mark_order_terminal(order)
        if status == "filled":
            runtime.broker_fills_seen += 1
            fill_price = float(order.get("filled_avg_price") or 0.0)
            if fill_price > 0:
                record_fill_slippage(
                    runtime, expected_price=meta["expected_price"], fill_price=fill_price, side=meta["side"]
                )
        elif status == "rejected":
            runtime.orders_rejected += 1


def _wait_terminal(alpaca, order_id: str, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last = {}
    while time.monotonic() < deadline:
        last = alpaca.get_order(order_id)
        if str(last.get("status")) in TERMINAL_ORDER_STATES:
            return last
        time.sleep(0.25)
    return last


def _flatten_if_needed(alpaca, spec: AssetSpec, runtime: RuntimeState, *, dry: bool) -> str:
    if runtime.inventory_qty == 0:
        return "already flat"
    if dry:
        return "dry mode: flatten required but not submitted"
    if runtime.inventory_qty < 0:
        return "short inventory detected: automatic flatten refused; manual broker intervention required"
    qty = quantize_quantity(runtime.inventory_qty, spec, up=False)
    if qty <= 0:
        return "inventory below executable increment; manual reconciliation required"
    order = alpaca.submit_market_order(side="sell", qty=qty, spec=spec)
    status = _wait_terminal(alpaca, str(order["id"]))
    _sync_account(alpaca, runtime, time.time())
    if runtime.inventory_qty == 0:
        return f"flatten verified by broker (order {order['id']}, status {status.get('status')})"
    return f"flatten not verified; broker position remains {runtime.inventory_qty}"


def _submit_quote_side(
    alpaca,
    *,
    spec: AssetSpec,
    side: str,
    qty: float,
    price: float,
    snapshot: dict,
    limits: Limits,
    decision_latency_ms: float | None,
    session_open_orders: int,
    runtime: RuntimeState,
    pending_orders: dict[str, dict],
    dry: bool,
) -> tuple[str, dict | None]:
    notional = qty * price
    signed = notional if side == "buy" else -notional
    projected = snapshot["inventory_usd"] + signed
    verdict = risk_check(
        snapshot,
        notional,
        limits,
        api_error_streak=runtime.api_error_streak,
        decision_latency_ms=decision_latency_ms,
        session_open_orders=session_open_orders,
        projected_inventory_usd=projected,
    )
    if not verdict.ok:
        return f"{side} vetoed: {verdict.veto}", None
    if dry:
        return f"dry {side} {qty:g} @ {price:g}", None
    try:
        order = alpaca.submit_limit_order(side=side, qty=qty, limit_price=price, spec=spec)
        runtime.orders_submitted += 1
        if order.get("id"):
            pending_orders[str(order["id"])] = {"side": side, "expected_price": price}
        return f"submitted {side} {qty:g} @ {price:g} id={order.get('id')}", order
    except (AlpacaAPIError, MarketClosedError) as exc:
        runtime.orders_rejected += 1
        return f"{side} submission failed: {exc}", None


def _execute_action(
    *,
    alpaca,
    spec: AssetSpec,
    action,
    snapshot: dict,
    limits: Limits,
    decision_latency_ms: float | None,
    rest_counter: int,
    runtime: RuntimeState,
    pending_orders: dict[str, dict],
    dry: bool,
) -> tuple[str, int]:
    if action.kind in {PULL_QUOTES, STAND_DOWN}:
        count, error = _cancel_owned(alpaca, dry=dry)
        return f"{action.kind}; canceled={count}" + (f"; cancel_error={error}" if error else ""), 0

    if action.kind not in {QUOTE_BOTH_SIDES, QUOTE_WIDE, WIDEN}:
        return action.kind, rest_counter

    rest_counter += 1
    owned = [] if dry else _owned_open_orders(alpaca)
    if owned and rest_counter < limits.quote_rest_ticks:
        return f"{action.kind}; resting {len(owned)} owned order(s)", rest_counter

    canceled, cancel_error = _cancel_owned(alpaca, dry=dry)
    if cancel_error:
        return f"{action.kind}; requote blocked: {cancel_error}; canceled={canceled}", 0
    wide = action.kind in {QUOTE_WIDE, WIDEN}
    bid_px, ask_px = bounded_quote_prices(
        mid=snapshot["mid"],
        best_bid=snapshot["best_bid"],
        best_ask=snapshot["best_ask"],
        inventory_utilization=snapshot["inventory_utilization"] or 0.0,
        limits=limits,
        spec=spec,
        wide=wide,
    )
    quote_notional = limits.quote_notional_usd
    buy_qty = quantity_for_notional(quote_notional, bid_px, spec)
    messages: list[str] = []
    msg, buy_order = _submit_quote_side(
        alpaca,
        spec=spec,
        side="buy",
        qty=buy_qty,
        price=bid_px,
        snapshot=snapshot,
        limits=limits,
        decision_latency_ms=decision_latency_ms,
        session_open_orders=0,
        runtime=runtime,
        pending_orders=pending_orders,
        dry=dry,
    )
    messages.append(msg)
    open_count = 1 if buy_order else 0

    # Cash/spot invariant: never create a naked sell. Only quote inventory already held.
    available_sell = max(0.0, snapshot["inventory_qty"])
    sell_qty = quantize_quantity(min(quantity_for_notional(quote_notional, ask_px, spec), available_sell), spec)
    if spec.min_order_size is not None and sell_qty < float(spec.min_order_size):
        sell_qty = 0.0
    if sell_qty > 0:
        msg, _ = _submit_quote_side(
            alpaca,
            spec=spec,
            side="sell",
            qty=sell_qty,
            price=ask_px,
            snapshot=snapshot,
            limits=limits,
            decision_latency_ms=decision_latency_ms,
            session_open_orders=open_count,
            runtime=runtime,
            pending_orders=pending_orders,
            dry=dry,
        )
        messages.append(msg)
    else:
        messages.append("sell skipped: no broker-reconciled inventory")
    return "; ".join(messages), 0


def run(
    *,
    symbol: str,
    ticks: int | None,
    mock: bool,
    dry_execution: bool,
    limits: Limits,
) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    preflight = paper_preflight(symbol=symbol, mock=mock)
    foreign_reasons = preflight.reasons_with_code(FOREIGN_SESSION_ORDERS)
    if not dry_execution and not preflight.ready:
        for reason in preflight.reasons:
            print(f"PREFLIGHT_BLOCKED code={reason.code} message={reason.message}")
        return 2

    # Dry mode remains an inspection path. It may continue where the existing runtime
    # dependencies are usable, and reports foreign ownership as machine-readable data.
    if foreign_reasons:
        for reason in foreign_reasons:
            print(json.dumps({"event": "paper_preflight_warning", "code": reason.code, "details": reason.details}))
    alpaca = preflight.alpaca
    spec = preflight.asset
    decision_client = preflight.decision_client
    if alpaca is None or spec is None or decision_client is None:
        for reason in preflight.reasons:
            print(f"cannot start: [{reason.code}] {reason.message}")
        return 2

    if decision_client.name == "MOCK" and not dry_execution:
        print("cannot start: mock judgments may not drive broker orders; add --dry-execution or use a real Jev provider")
        return 2

    print(f"asset verified by Alpaca: {spec.symbol} ({spec.asset_class}, status={spec.status}, tradable={spec.tradable})")
    print(f"decision client: {decision_client.name} / {decision_client.model}")
    print("execution: " + ("DRY (no orders)" if dry_execution else "PAPER (explicit --paper)"))
    runtime = RuntimeState()
    trade_history: list[TradeTick] = []
    recent_records: list[dict] = []
    started_at = time.time()
    pending_orders: dict[str, dict] = {}
    rest_counter = 0
    block = 0
    n = 0
    previous_sigterm = None
    try:
        previous_sigterm = signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, AttributeError, OSError):
        pass

    # PAPER AUTHORITY ACTIVATION POINT: after preflight and dependency validation,
    # immediately before entering the abnormal-exit boundary. From this statement
    # onward the loop may mutate the paper account, and every abnormal exit is
    # ownership-scoped, broker-verified, and durably evidenced.
    paper_authority_active = not dry_execution
    try:
        while ticks is None or n < ticks:
            tick_start = time.monotonic()
            now = time.time()
            block += 1
            n += 1

            if not spec.is_24_7 and not alpaca.is_market_open(spec):
                _cancel_owned(alpaca, dry=dry_execution)
                record = _record(block, now, spec, None, None, None, None, Rung.HOLD_LATE, "MARKET_CLOSED", runtime)
                _persist(record, recent_records, spec.symbol, started_at)
                print(f"tick {block}: market closed; HOLD")
                _sleep_remaining(tick_start, limits.tick_seconds)
                continue

            try:
                _refresh_order_statuses(alpaca, runtime, pending_orders)
                _sync_account(alpaca, runtime, now)
                view = alpaca.get_market_view(spec)
                runtime.api_error_streak = 0
            except AlpacaAPIError as exc:
                runtime.api_error_streak += 1
                print(f"tick {block}: Alpaca read error: {exc}")
                _cancel_owned(alpaca, dry=dry_execution)
                if runtime.api_error_streak > limits.max_api_error_streak:
                    print("hard API-error threshold exceeded; stopping")
                    return 3
                _sleep_remaining(tick_start, limits.tick_seconds)
                continue

            trade_history = _merge_trades(trade_history, view.trades, now)
            observe_trades(runtime, view.trades)
            try:
                snapshot = build_snapshot(
                    as_of=now,
                    mid=view.mid,
                    best_bid=view.best_bid,
                    best_ask=view.best_ask,
                    bid_depth=view.bids,
                    ask_depth=view.asks,
                    trades=trade_history,
                    runtime=runtime,
                    market_data_ts=view.market_data_ts,
                    max_position_usd=limits.max_position_usd,
                    trade_data_ts=view.trade_ts,
                    has_depth=spec.has_depth,
                )
            except ValueError as exc:
                count, error = _cancel_owned(alpaca, dry=dry_execution)
                execution = f"invalid market state: {exc}; canceled={count}" + (f"; cancel_error={error}" if error else "")
                record = _record(
                    block, now, spec, None, None, None, None, Rung.HOLD_LATE, execution, runtime, action_name="DATA_INVALID"
                )
                _persist(record, recent_records, spec.symbol, started_at)
                print(f"tick {block}: DATA_INVALID; {execution}")
                _sleep_remaining(tick_start, limits.tick_seconds)
                continue

            if snapshot["data_age_s"] > limits.max_stale_data_age_s:
                count, error = _cancel_owned(alpaca, dry=dry_execution)
                execution = f"stale market data age={snapshot['data_age_s']}s; canceled={count}" + (f"; cancel_error={error}" if error else "")
                record = _record(
                    block, now, spec, snapshot, None, None, None, Rung.HOLD_LATE, execution, runtime, action_name="STALE_DATA"
                )
                _persist(record, recent_records, spec.symbol, started_at)
                print(f"tick {block}: STALE_DATA; {execution}")
                _sleep_remaining(tick_start, limits.tick_seconds)
                continue

            pre_hard = risk_check(
                snapshot,
                0.0,
                limits,
                api_error_streak=runtime.api_error_streak,
                decision_latency_ms=None,
                session_open_orders=0,
            )
            if not pre_hard.ok:
                canceled, cancel_error = _cancel_owned(alpaca, dry=dry_execution)
                if pre_hard.kill:
                    if cancel_error:
                        execution = (
                            f"KILL; flatten blocked because cancellation could not be verified; "
                            f"canceled={canceled}; cancel_error={cancel_error}; manual broker reconciliation required"
                        )
                    else:
                        try:
                            execution = _flatten_if_needed(alpaca, spec, runtime, dry=dry_execution)
                        except (AlpacaAPIError, MarketClosedError, UnknownOrderOutcome) as exc:
                            execution = f"flatten attempt failed or became ambiguous: {exc}"
                    record = _record(
                        block, now, spec, snapshot, None, None, None, Rung.KILL, execution, runtime, action_name="KILL"
                    )
                    _persist(record, recent_records, spec.symbol, started_at)
                    print(f"tick {block}: pre-decision KILL: {pre_hard.veto}; {execution}")
                    return 3 if runtime.inventory_qty != 0 and not dry_execution else 0
                execution = f"risk block: {pre_hard.veto}; canceled={canceled}" + (f"; cancel_error={cancel_error}" if cancel_error else "")
                record = _record(
                    block, now, spec, snapshot, None, None, None, Rung.HOLD_BLOCKED, execution, runtime, action_name="RISK_BLOCK"
                )
                _persist(record, recent_records, spec.symbol, started_at)
                print(f"tick {block}: RISK_BLOCK; {execution}")
                _sleep_remaining(tick_start, limits.tick_seconds)
                continue

            elapsed = time.monotonic() - tick_start
            timeout = max(0.05, limits.tick_seconds - elapsed - 0.15)
            answers = None
            meta = {"route": None, "model": None, "latency_ms": None}
            decision_late = False
            jev_down = False
            try:
                answers, meta = run_battery(decision_client, snapshot, timeout=timeout)
                runtime.recent_latencies_ms.append(float(meta["latency_ms"]))
                runtime.recent_latencies_ms = runtime.recent_latencies_ms[-10:]
            except (DecisionClientError, DecisionSchemaError) as exc:
                decision_late = "deadline" in str(exc).lower()
                jev_down = not decision_late
                print(f"tick {block}: decision unavailable: {exc}")

            action = None if decision_late else (fallback_action(snapshot, limits) if jev_down or answers is None else compose_action(answers, snapshot, limits))
            hard = risk_check(
                snapshot,
                0.0,
                limits,
                api_error_streak=runtime.api_error_streak,
                decision_latency_ms=meta.get("latency_ms"),
                session_open_orders=0,
            )
            conf = answers.get("quote_environment", {}).get("confidence") if answers else None
            execution_health = answers.get("execution_health", {}).get("score") if answers else None
            rung = select_rung(
                risk_kill=hard.kill or (action is not None and action.kind == KILL),
                decision_late=decision_late,
                jev_down=jev_down,
                decision_confidence=conf,
                low_confidence_threshold=limits.low_confidence_threshold,
                execution_health_score=execution_health,
                execution_health_floor=limits.execution_health_floor,
                risk_ok=hard.ok,
            )

            if rung == Rung.KILL:
                canceled, cancel_error = _cancel_owned(alpaca, dry=dry_execution)
                if cancel_error:
                    execution = (
                        f"KILL; flatten blocked because cancellation could not be verified; "
                        f"canceled={canceled}; cancel_error={cancel_error}; manual broker reconciliation required"
                    )
                else:
                    try:
                        execution = _flatten_if_needed(alpaca, spec, runtime, dry=dry_execution)
                    except (AlpacaAPIError, MarketClosedError, UnknownOrderOutcome) as exc:
                        execution = f"flatten attempt failed or became ambiguous: {exc}"
                action_name = "KILL"
            elif rung in {Rung.HOLD_LATE, Rung.HOLD_BLOCKED, Rung.RULES_ONLY} or action is None:
                count, error = _cancel_owned(alpaca, dry=dry_execution)
                execution = f"no new orders; canceled={count}" + (f"; cancel_error={error}" if error else "")
                if rung == Rung.HOLD_LATE:
                    action_name = "HOLD_LATE"
                elif rung == Rung.HOLD_BLOCKED:
                    action_name = "RISK_BLOCK"
                    execution = f"risk veto: {hard.veto}; " + execution
                else:
                    action_name = action.kind if action else "STAND_DOWN"
            else:
                effective_limits = limits
                if rung == Rung.REDUCE:
                    from dataclasses import replace
                    effective_limits = replace(limits, quote_notional_usd=limits.quote_notional_usd * limits.reduce_size_factor)
                try:
                    execution, rest_counter = _execute_action(
                        alpaca=alpaca,
                        spec=spec,
                        action=action,
                        snapshot=snapshot,
                        limits=effective_limits,
                        decision_latency_ms=meta.get("latency_ms"),
                        rest_counter=rest_counter,
                        runtime=runtime,
                        pending_orders=pending_orders,
                        dry=dry_execution,
                    )
                    action_name = action.kind
                except UnknownOrderOutcome as exc:
                    count, error = _cancel_owned(alpaca, dry=dry_execution)
                    try:
                        _sync_account(alpaca, runtime, time.time())
                    except AlpacaAPIError as sync_exc:
                        error = f"{error}; account_sync={sync_exc}" if error else f"account_sync={sync_exc}"
                    rung = Rung.HOLD_BLOCKED
                    action_name = "UNKNOWN_ORDER_OUTCOME"
                    execution = f"{exc}; canceled={count}" + (f"; reconciliation_error={error}" if error else "")
                    record = _record(block, now, spec, snapshot, answers, meta, action, rung, execution, runtime, action_name=action_name)
                    _persist(record, recent_records, spec.symbol, started_at)
                    print(f"tick {block}: {action_name} | rung={rung.value} | {execution}")
                    return 3

            record = _record(block, now, spec, snapshot, answers, meta, action, rung, execution, runtime, action_name=action_name)
            _persist(record, recent_records, spec.symbol, started_at)
            print(f"tick {block}: {action_name} | rung={rung.value} | {execution}")
            if rung == Rung.KILL:
                print("KILL rung reached; stopping after broker reconciliation attempt")
                return 3 if runtime.inventory_qty != 0 and not dry_execution else 0
            _sleep_remaining(tick_start, limits.tick_seconds)
        return 0
    except (KeyboardInterrupt, _StopRequested) as exc:
        reconciliation = _reconcile_session_orders(alpaca, dry=dry_execution)
        _append_reconciliation_record(
            kind="stop",
            original_failure=exc,
            failure_traceback=traceback.format_exc(),
            reconciliation=reconciliation,
            paper_authority_active=paper_authority_active,
            symbol=spec.symbol,
        )
        print(
            f"stopping; canceled {reconciliation.cancelled} session-owned order(s); "
            f"verified={reconciliation.verified}"
        )
        return 0 if reconciliation.verified else 3
    except Exception as exc:
        # Bare `raise` below retains the original exception object and traceback.
        # Cleanup and evidence failures are attached as notes rather than replacing it.
        original_traceback = traceback.format_exc()
        reconciliation = _reconcile_session_orders(alpaca, dry=dry_execution)
        try:
            _append_reconciliation_record(
                kind="unexpected_failure",
                original_failure=exc,
                failure_traceback=original_traceback,
                reconciliation=reconciliation,
                paper_authority_active=paper_authority_active,
                symbol=spec.symbol,
            )
        except BaseException as evidence_exc:
            if hasattr(exc, "add_note"):
                exc.add_note(f"failed to durably record reconciliation evidence: {evidence_exc!r}")
        raise
    finally:
        if previous_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, previous_sigterm)
            except (ValueError, AttributeError, OSError):
                pass


def _record(block, now, spec, snapshot, answers, meta, action, rung, execution, runtime, action_name=None):
    direction_probs = answers.get("direction", {}).get("probabilities") if answers else None
    return {
        "schema_version": 2,
        "tick": block,
        "ts": now,
        "symbol": spec.symbol,
        "asset_class": spec.asset_class,
        "mid": snapshot.get("mid") if snapshot else None,
        "spread_bps": snapshot.get("spread_bps") if snapshot else None,
        "observed_vwap": snapshot.get("observed_vwap") if snapshot else None,
        "inventory_qty": runtime.inventory_qty,
        "inventory_usd": snapshot.get("inventory_usd") if snapshot else None,
        "drawdown_pct": snapshot.get("drawdown_pct") if snapshot else None,
        "regime": answers.get("regime", {}).get("choice") if answers else None,
        "direction": answers.get("direction", {}).get("choice") if answers else None,
        "direction_probabilities": direction_probs,
        "toxic_flow": answers.get("toxic_flow", {}).get("noul") if answers else None,
        "liquidity_stressed": answers.get("liquidity_stressed", {}).get("noul") if answers else None,
        "quote_environment": answers.get("quote_environment", {}).get("score") if answers else None,
        "quote_environment_conf": answers.get("quote_environment", {}).get("confidence") if answers else None,
        "execution_health": answers.get("execution_health", {}).get("score") if answers else None,
        "action": action_name or (action.kind if action else "HOLD"),
        "action_reason": action.reason if action else None,
        "rung": rung.value,
        "execution": execution,
        "latency_ms": meta.get("latency_ms") if meta else None,
        "route": meta.get("route") if meta else None,
        "model": meta.get("model") if meta else None,
    }


def _append_reconciliation_record(
    *,
    kind: str,
    original_failure: BaseException,
    failure_traceback: str,
    reconciliation: ReconciliationResult,
    paper_authority_active: bool,
    symbol: str,
) -> None:
    """Durably append abnormal-exit evidence without using replaceable latest state."""
    _append_log(
        {
            "schema_version": 2,
            "event": "paper_reconciliation",
            "kind": kind,
            "ts": time.time(),
            "symbol": symbol,
            "paper_authority_active": paper_authority_active,
            "original_failure": {
                "type": type(original_failure).__name__,
                "message": str(original_failure),
                "traceback": failure_traceback,
            },
            "cleanup": reconciliation.as_dict(),
        }
    )


def _append_log(record: dict) -> None:
    """Append one decision record and force it to durable storage.

    This log is the evidence trail `calibrate` and human review depend on ("report
    executed evidence exactly"). A Python-level crash between calls is already safe
    because each call opens/closes its own handle, but without an explicit flush+fsync
    a host-level crash (power loss, OOM kill of the whole container) can lose the most
    recent lines even though `write()` returned. `calibrate.load_ticks` already skips
    unparseable trailing lines, so a torn last write degrades gracefully either way.
    """
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _persist(record: dict, recent: list[dict], symbol: str, started_at: float) -> None:
    _append_log(record)
    recent.append(record)
    del recent[:-LATEST_WINDOW]
    latencies = [x["latency_ms"] for x in recent if x.get("latency_ms") is not None]
    payload = {
        "schema_version": 2,
        "generated_at": time.time(),
        "symbol": symbol,
        "ticks": recent,
        "stats": {
            "calls": len(latencies),
            "avg_ms": sum(latencies) / len(latencies) if latencies else None,
            "late_count": sum(x.get("rung") == Rung.HOLD_LATE.value for x in recent),
            "blocked_count": sum(x.get("rung") == Rung.HOLD_BLOCKED.value for x in recent),
            "uptime_s": time.time() - started_at,
        },
    }
    tmp = LATEST_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp.replace(LATEST_FILE)


def _sleep_remaining(start: float, seconds: float) -> None:
    remaining = seconds - (time.monotonic() - start)
    if remaining > 0:
        time.sleep(remaining)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev-loop run")
    parser.add_argument("--symbol", default=os.getenv("DEFAULT_SYMBOL", "BTC/USD"))
    parser.add_argument("--ticks", type=int, default=30, help="0 means continuous")
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--mock", action="store_true", help="offline mock judgments; no broker orders")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--paper", action="store_true", help="explicitly allow Alpaca paper-order submission")
    mode.add_argument("--dry-execution", action="store_true", help="explicit no-order mode (also the default)")
    args = parser.parse_args(argv)
    if args.mock and args.paper:
        parser.error("mock judgments may not drive broker orders")
    ticks = None if args.forever or args.ticks == 0 else args.ticks
    return run(
        symbol=args.symbol,
        ticks=ticks,
        mock=args.mock,
        dry_execution=not args.paper,
        limits=Limits(),
    )


if __name__ == "__main__":
    sys.exit(main())
