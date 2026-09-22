import json
import time
from decimal import Decimal

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
