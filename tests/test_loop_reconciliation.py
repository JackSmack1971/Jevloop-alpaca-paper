import json
import time
from decimal import Decimal

import pytest

from jevloop.assets import AssetSpec
from jevloop.execution.alpaca import MarketView
from jevloop.limits import Limits
from jevloop.loop import _refresh_order_statuses, _sync_account, run
from jevloop.preflight import PaperPreflightResult
from jevloop.risk import check
from jevloop.state import RuntimeState


class FakeBroker:
    def __init__(self):
        self.order = {"status": "filled", "filled_avg_price": "101"}
    def get_order(self, order_id):
        return self.order
    def get_account(self):
        return {"equity": "1000", "last_equity": "1000"}
    def get_position(self):
        return {"qty": "0.2", "side": "long", "avg_entry_price": "100"}


def test_preexisting_position_age_stays_unknown_on_first_sync():
    runtime = RuntimeState()
    _sync_account(FakeBroker(), runtime, now=1000)
    assert runtime.inventory_qty == 0.2
    assert runtime.position_opened_at is None
    assert runtime.position_state_initialized is True


def test_unknown_inventory_age_vetoes_new_exposure():
    snapshot = {
        "drawdown_pct": 0.0, "inventory_usd": 20.0, "inventory_qty": 0.2,
        "daily_loss_usd": 0.0, "position_age_s": None, "data_age_s": 0.0,
    }
    verdict = check(snapshot, 1.0, Limits(), api_error_streak=0, decision_latency_ms=10, session_open_orders=0)
    assert not verdict.ok and "age unknown" in verdict.veto


def test_terminal_fill_is_broker_observed_and_updates_slippage():
    runtime = RuntimeState()
    pending = {"order-1": {"side": "buy", "expected_price": 100.0}}
    _refresh_order_statuses(FakeBroker(), runtime, pending)
    assert runtime.broker_fills_seen == 1
    assert runtime.recent_slippage_bps == [100.0]
    assert pending == {}


class _DoneForDayBroker:
    def __init__(self):
        self.order = {"status": "done_for_day", "client_order_id": "jevloop-x-1"}

    def get_order(self, order_id):
        return self.order

    def mark_order_terminal(self, order):  # pragma: no cover - must not be called
        raise AssertionError("done_for_day must not be marked terminal")


def test_done_for_day_order_stays_pending_and_polled():
    runtime = RuntimeState()
    pending = {"order-1": {"side": "buy", "expected_price": 100.0}}
    _refresh_order_statuses(_DoneForDayBroker(), runtime, pending)
    # Not terminal: still pending for the next tick's poll, no fill/reject recorded.
    assert pending == {"order-1": {"side": "buy", "expected_price": 100.0}}
    assert runtime.broker_fills_seen == 0
    assert runtime.orders_rejected == 0


def test_cancel_verification_reports_remaining_owned_orders(monkeypatch):
    from jevloop.loop import _cancel_owned

    class Broker:
        session_id = "session"
        def cancel_session_orders(self):
            return ["order-1"]
        def get_open_orders(self):
            return [{"id": "order-1", "client_order_id": "jevloop-session-1"}]
        @staticmethod
        def is_owned_order(order, session_id):
            return order["client_order_id"].startswith(f"jevloop-{session_id}-")

    monkeypatch.setattr("jevloop.loop.time.sleep", lambda _: None)
    count, error = _cancel_owned(Broker(), dry=False, verify_attempts=2)
    assert count == 1
    assert "still open" in error


def test_malformed_decision_cancels_and_verifies_without_submitting(monkeypatch, tmp_path):
    class MalformedProvider:
        name = "malformed-provider"
        model = "test-model"

        def ask(self, **kwargs):
            return {"direction": "not-an-answer-mapping"}, {"latency_ms": 1.0}

    class Broker:
        session_id = "current"

        def __init__(self):
            self.cancel_calls = 0
            self.open_orders = [{"id": "owned-1", "client_order_id": "jevloop-current-1"}]
            self.submit_calls = 0

        def get_account(self):
            return {"equity": "10000", "last_equity": "10000"}

        def get_position(self):
            return None

        def get_market_view(self, spec):
            now = time.time()
            return MarketView(99.99, 100.01, [(99.99, 1)], [(100.01, 1)], [], now)

        def cancel_session_orders(self):
            self.cancel_calls += 1
            cancelled = [order["id"] for order in self.open_orders]
            self.open_orders = []
            return cancelled

        def get_open_orders(self):
            return list(self.open_orders)

        @staticmethod
        def is_owned_order(order, session_id):
            return order["client_order_id"].startswith(f"jevloop-{session_id}-")

        def submit_limit_order(self, **kwargs):
            self.submit_calls += 1
            raise AssertionError("malformed decisions must never submit an order")

    broker = Broker()
    spec = AssetSpec(
        "BTC/USD", "crypto", True, True, status="active", tradable=True,
        min_order_size=Decimal("0.0001"), min_trade_increment=Decimal("0.0001"),
        price_increment=Decimal("0.01"), metadata_source="alpaca:/v2/assets",
    )
    result = PaperPreflightResult((), broker, spec, broker.get_account(), MalformedProvider())
    monkeypatch.setattr("jevloop.loop.paper_preflight", lambda **kwargs: result)
    monkeypatch.setattr("jevloop.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("jevloop.loop.LOG_FILE", tmp_path / "log.jsonl")
    monkeypatch.setattr("jevloop.loop.LATEST_FILE", tmp_path / "latest.json")

    assert run(
        symbol="BTC/USD", ticks=1, mock=False, dry_execution=False,
        limits=Limits(tick_seconds=0),
    ) == 0

    record = json.loads((tmp_path / "log.jsonl").read_text().strip())
    assert record["rung"] == "rules_only"
    assert record["action"] == "STAND_DOWN"
    assert "no new orders" in record["execution"]
    assert broker.cancel_calls == 1
    assert broker.open_orders == []
    assert broker.submit_calls == 0


class _NeverDecision:
    name = "test-provider"
    model = "test-model"

    def ask(self, **kwargs):  # pragma: no cover - failure happens first
        raise AssertionError("decision client should not be reached")


def _paper_result(broker):
    spec = AssetSpec(
        "BTC/USD", "crypto", True, True, status="active", tradable=True,
        min_order_size=Decimal("0.0001"), min_trade_increment=Decimal("0.0001"),
        price_increment=Decimal("0.01"), metadata_source="alpaca:/v2/assets",
    )
    return PaperPreflightResult((), broker, spec, {"equity": "10000"}, _NeverDecision())


def _set_log_paths(monkeypatch, tmp_path):
    monkeypatch.setattr("jevloop.loop.LOG_DIR", tmp_path)
    monkeypatch.setattr("jevloop.loop.LOG_FILE", tmp_path / "log.jsonl")
    monkeypatch.setattr("jevloop.loop.LATEST_FILE", tmp_path / "latest.json")


class _FailingPaperBroker:
    session_id = "current"

    def __init__(self, *, cancel_failure=False, verification_failure=False, fail_during_submit=False):
        self.cancel_failure = cancel_failure
        self.verification_failure = verification_failure
        self.fail_during_submit = fail_during_submit
        self.cancel_calls = 0
        self.open_calls = 0
        self.open_orders = [
            {"id": "owned-1", "client_order_id": "jevloop-current-1"},
            {"id": "foreign-1", "client_order_id": "jevloop-other-1"},
        ]

    def get_account(self):
        return {"equity": "10000", "last_equity": "10000"}

    def get_position(self):
        return None

    def get_market_view(self, spec):
        if not self.fail_during_submit:
            raise RuntimeError("market view exploded")
        now = time.time()
        return MarketView(99.99, 100.01, [(99.99, 1)], [(100.01, 1)], [], now)

    def cancel_session_orders(self):
        self.cancel_calls += 1
        if self.cancel_failure:
            raise RuntimeError("cancel exploded")
        self.open_orders = [order for order in self.open_orders if not self.is_owned_order(order, self.session_id)]
        return ["owned-1"]

    def get_open_orders(self):
        self.open_calls += 1
        if self.verification_failure:
            raise RuntimeError("verification exploded")
        return list(self.open_orders)

    @staticmethod
    def is_owned_order(order, session_id):
        return order["client_order_id"].startswith(f"jevloop-{session_id}-")

    def cancel_all_orders(self):  # pragma: no cover - forbidden operation
        raise AssertionError("account-wide cancellation must never be called")


def test_exception_before_paper_activation_does_not_touch_broker(monkeypatch, tmp_path):
    broker = _FailingPaperBroker()
    _set_log_paths(monkeypatch, tmp_path)

    def failed_preflight(**kwargs):
        raise RuntimeError("preflight exploded")

    monkeypatch.setattr("jevloop.loop.paper_preflight", failed_preflight)
    with pytest.raises(RuntimeError, match="preflight exploded"):
        run(symbol="BTC/USD", ticks=1, mock=False, dry_execution=False, limits=Limits(tick_seconds=0))

    assert broker.cancel_calls == 0
    assert broker.open_calls == 0
    assert not (tmp_path / "log.jsonl").exists()


@pytest.mark.parametrize(
    ("cancel_failure", "verification_failure", "expected_field"),
    [(False, False, None), (True, False, "cancel_error"), (False, True, "verification_error")],
)
def test_unexpected_failure_reconciles_and_durably_records(
    monkeypatch, tmp_path, cancel_failure, verification_failure, expected_field
):
    broker = _FailingPaperBroker(cancel_failure=cancel_failure, verification_failure=verification_failure)
    _set_log_paths(monkeypatch, tmp_path)
    monkeypatch.setattr("jevloop.loop.paper_preflight", lambda **kwargs: _paper_result(broker))
    monkeypatch.setattr("jevloop.loop.time.sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="market view exploded") as raised:
        run(symbol="BTC/USD", ticks=1, mock=False, dry_execution=False, limits=Limits(tick_seconds=0))

    assert "get_market_view" in "".join(__import__("traceback").format_tb(raised.value.__traceback__))
    assert broker.cancel_calls == 1
    assert broker.open_calls >= 1
    record = json.loads((tmp_path / "log.jsonl").read_text().strip())
    assert record["event"] == "paper_reconciliation"
    assert record["paper_authority_active"] is True
    assert record["original_failure"]["type"] == "RuntimeError"
    assert "market view exploded" in record["original_failure"]["traceback"]
    assert record["cleanup"]["verified"] is (not cancel_failure and not verification_failure)
    if cancel_failure:
        assert record["cleanup"]["unresolved_orders"] == [
            {"id": "owned-1", "client_order_id": "jevloop-current-1"}
        ]
    if expected_field:
        assert record["cleanup"][expected_field]


def test_unexpected_order_handling_failure_uses_same_reconciliation(monkeypatch, tmp_path):
    broker = _FailingPaperBroker(fail_during_submit=True)
    _set_log_paths(monkeypatch, tmp_path)
    monkeypatch.setattr("jevloop.loop.paper_preflight", lambda **kwargs: _paper_result(broker))
    monkeypatch.setattr("jevloop.loop.run_battery", lambda *args, **kwargs: ({
        "direction": {"choice": "flat", "probabilities": {}},
        "regime": {"choice": "range"}, "toxic_flow": {"noul": False},
        "liquidity_stressed": {"noul": False},
        "quote_environment": {"score": 1.0, "confidence": 1.0},
        "execution_health": {"score": 1.0},
    }, {"latency_ms": 1.0, "route": "test", "model": "test"}))
    monkeypatch.setattr("jevloop.loop.compose_action", lambda *args: (_ for _ in ()).throw(RuntimeError("order handling exploded")))

    with pytest.raises(RuntimeError, match="order handling exploded"):
        run(symbol="BTC/USD", ticks=1, mock=False, dry_execution=False, limits=Limits(tick_seconds=0))

    record = json.loads((tmp_path / "log.jsonl").read_text().strip())
    assert record["cleanup"]["verified"] is True
    assert broker.cancel_calls == 1
