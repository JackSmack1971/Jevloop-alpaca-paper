import pytest

from jevloop.assets import AssetSpec
from jevloop.execution.alpaca import (
    AlpacaAPIError,
    AlpacaClient,
    AlpacaConfigError,
    LIVE_TRADING_BASE_URL,
    PAPER_TRADING_BASE_URL,
    TERMINAL_ORDER_STATES,
    UnknownOrderOutcome,
    resolve_trading_base_url,
)


def test_paper_is_only_supported_runtime():
    assert resolve_trading_base_url(live=False) == PAPER_TRADING_BASE_URL


def test_live_is_unconditionally_refused():
    with pytest.raises(Exception, match="not a capability"):
        resolve_trading_base_url(live=True, allow_live_env="anything", confirmation="anything")


def test_direct_live_constructor_refused():
    with pytest.raises(AlpacaConfigError):
        AlpacaClient("k", "s", "BTC/USD", base_url=LIVE_TRADING_BASE_URL)


def test_arbitrary_base_url_refused():
    with pytest.raises(AlpacaConfigError):
        AlpacaClient("k", "s", "BTC/USD", base_url="https://evil.example")


def test_session_order_ownership():
    c = AlpacaClient("k", "s", "BTC/USD")
    assert c.is_owned_order({"client_order_id": f"jevloop-{c.session_id}-1"}, c.session_id)
    assert not c.is_owned_order({"client_order_id": "manual-order"}, c.session_id)


def test_cancel_session_orders_never_cancels_unowned(monkeypatch):
    c = AlpacaClient("k", "s", "BTC/USD")
    mine = f"jevloop-{c.session_id}-1"
    monkeypatch.setattr(
        c,
        "get_open_orders",
        lambda: [{"id": "a", "client_order_id": mine}, {"id": "b", "client_order_id": "manual"}],
    )
    seen = []
    monkeypatch.setattr(c, "cancel_order", lambda oid: seen.append(oid))
    assert c.cancel_session_orders() == ["a"]
    assert seen == ["a"]


class _Response:
    def __init__(self, status_code, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else ("{}" if payload is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_ambiguous_order_post_is_not_retried_and_is_reconciled_by_client_id(monkeypatch):
    calls = []

    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            return _Response(500, text="server error")
        return _Response(
            200,
            payload={"id": "broker-1", "client_order_id": kwargs["params"]["client_order_id"], "status": "accepted"},
        )

    monkeypatch.setattr("jevloop.execution.alpaca.requests.request", fake)
    c = AlpacaClient("k", "s", "BTC/USD")
    spec = AssetSpec("BTC/USD", "crypto", True, True)
    order = c.submit_limit_order(side="buy", qty=0.1, limit_price=100, spec=spec)
    assert order["id"] == "broker-1"
    assert [method for method, _, _ in calls].count("POST") == 1
    assert [method for method, _, _ in calls].count("GET") == 1


def test_ambiguous_order_post_with_lookup_miss_is_unknown_not_rejection(monkeypatch):
    calls = []

    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            return _Response(500, text="server error")
        return _Response(404, text="not found")

    monkeypatch.setattr("jevloop.execution.alpaca.requests.request", fake)
    c = AlpacaClient("k", "s", "BTC/USD")
    spec = AssetSpec("BTC/USD", "crypto", True, True)
    with pytest.raises(UnknownOrderOutcome) as exc:
        c.submit_limit_order(side="buy", qty=0.1, limit_price=100, spec=spec)
    assert exc.value.client_order_id.startswith(f"jevloop-{c.session_id}-")
    assert [method for method, _, _ in calls].count("POST") == 1


def test_clear_client_error_is_not_reconciled_or_retried(monkeypatch):
    calls = []

    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _Response(422, text="invalid order")

    monkeypatch.setattr("jevloop.execution.alpaca.requests.request", fake)
    c = AlpacaClient("k", "s", "BTC/USD")
    spec = AssetSpec("BTC/USD", "crypto", True, True)
    with pytest.raises(AlpacaAPIError):
        c.submit_limit_order(side="buy", qty=0.1, limit_price=100, spec=spec)
    assert len(calls) == 1


def test_done_for_day_is_not_a_terminal_order_state():
    # Alpaca documents done_for_day as "will not receive further updates until the
    # next trading day" -- a same-day pause, not order closure. Treating it as
    # terminal makes the runtime discard ownership tracking and stop polling a
    # still-resting order one trading day too early.
    assert "done_for_day" not in TERMINAL_ORDER_STATES
    assert {"filled", "canceled", "expired", "rejected"} <= TERMINAL_ORDER_STATES


def test_cancel_session_orders_still_targets_a_done_for_day_order(monkeypatch):
    # A done_for_day order must remain a cancellation candidate: it is not finished,
    # so a KILL/PULL_QUOTES cancellation pass must still be able to reach it.
    c = AlpacaClient("k", "s", "BTC/USD")
    mine = f"jevloop-{c.session_id}-1"
    c._owned_client_order_ids.add(mine)
    monkeypatch.setattr(
        c,
        "get_order_by_client_order_id",
        lambda coid: {"id": "a", "client_order_id": mine, "status": "done_for_day"},
    )
    monkeypatch.setattr(c, "get_open_orders", lambda: [])
    seen = []
    monkeypatch.setattr(c, "cancel_order", lambda oid: seen.append(oid))
    assert c.cancel_session_orders() == ["a"]
    assert seen == ["a"]
    # Still tracked afterward: cancellation was requested, not confirmed terminal.
    assert mine in c._owned_client_order_ids


def test_mark_order_terminal_keeps_tracking_a_done_for_day_order():
    c = AlpacaClient("k", "s", "BTC/USD")
    mine = f"jevloop-{c.session_id}-1"
    c._owned_client_order_ids.add(mine)
    c.mark_order_terminal({"client_order_id": mine, "status": "done_for_day"})
    assert mine in c._owned_client_order_ids
    c.mark_order_terminal({"client_order_id": mine, "status": "filled"})
    assert mine not in c._owned_client_order_ids


def test_request_uses_bounded_connect_timeout_not_bare_scalar(monkeypatch):
    # A single scalar `timeout` applies independently to connect and read phases
    # (Requests docs), so a hung connect attempt could block for close to 2x the
    # configured value. A (connect, read) tuple bounds the connect phase tightly
    # while preserving the full configured read timeout.
    captured = {}

    def fake(method, url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _Response(200, payload={"ok": True})

    monkeypatch.setattr("jevloop.execution.alpaca.requests.request", fake)
    c = AlpacaClient("k", "s", "BTC/USD", timeout_s=10.0)
    c.get_account()
    assert isinstance(captured["timeout"], tuple)
    connect_timeout, read_timeout = captured["timeout"]
    assert connect_timeout < read_timeout
    assert read_timeout == 10.0


def test_foreign_session_open_orders_are_detected_but_not_touched(monkeypatch):
    c = AlpacaClient("k", "s", "BTC/USD")
    other_session = "deadbeef0000"
    mine = f"jevloop-{c.session_id}-1"
    theirs = f"jevloop-{other_session}-1"
    monkeypatch.setattr(
        c,
        "get_open_orders",
        lambda: [
            {"id": "a", "client_order_id": mine},
            {"id": "b", "client_order_id": theirs},
            {"id": "c", "client_order_id": "manual-order"},
        ],
    )
    foreign = c.get_foreign_session_open_orders()
    assert [o["id"] for o in foreign] == ["b"]
