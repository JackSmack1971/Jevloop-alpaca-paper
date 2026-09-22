import pytest

from jevloop.assets import AssetSpec
from jevloop.execution.alpaca import (
    AlpacaAPIError,
    AlpacaClient,
    AlpacaConfigError,
    LIVE_TRADING_BASE_URL,
    IncompleteOrderEnumeration,
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
            payload={
                "id": "broker-1",
                "client_order_id": kwargs["params"]["client_order_id"],
                "status": "accepted",
            },
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


def _order(number, submitted_at):
    return {"id": f"order-{number}", "submitted_at": submitted_at, "client_order_id": "manual"}


def test_open_orders_normalizes_crypto_symbol_and_enumerates_more_than_500(monkeypatch):
    calls = []
    first = [_order(i, f"2026-01-01T00:{59 - i // 10:02d}:{59 - i % 10:02d}Z") for i in range(500)]
    second = [_order(500 + i, "2025-12-31T23:00:00Z") for i in range(37)]

    def request(method, url, **kwargs):
        calls.append(kwargs["params"])
        return first if len(calls) == 1 else second

    c = AlpacaClient("k", "s", "BTC/USD")
    monkeypatch.setattr(c, "_request", request)
    assert len(c.get_open_orders()) == 537
    assert calls[0] == {"status": "open", "symbols": "BTCUSD", "limit": 500, "direction": "desc"}
    assert calls[1]["until"] == first[-1]["submitted_at"]


@pytest.mark.parametrize("payload", [{"orders": []}, [None], [{"submitted_at": "now"}]])
def test_open_orders_rejects_malformed_pages(monkeypatch, payload):
    c = AlpacaClient("k", "s", "AAPL")
    monkeypatch.setattr(c, "_request", lambda *args, **kwargs: payload)
    with pytest.raises(IncompleteOrderEnumeration, match="MALFORMED_PAGE"):
        c.get_open_orders()


def test_open_orders_wraps_transport_failure_as_incomplete(monkeypatch):
    c = AlpacaClient("k", "s", "AAPL")
    monkeypatch.setattr(
        c, "_request", lambda *a, **k: (_ for _ in ()).throw(AlpacaAPIError(0, "down"))
    )
    with pytest.raises(IncompleteOrderEnumeration, match="TRANSPORT_OR_API_FAILURE"):
        c.get_open_orders()


def test_open_orders_repeated_page_is_incomplete_not_an_infinite_loop(monkeypatch):
    page = [_order(i, "2026-01-01T00:00:00Z") for i in range(500)]
    calls = 0

    def request(*args, **kwargs):
        nonlocal calls
        calls += 1
        return page

    c = AlpacaClient("k", "s", "AAPL")
    monkeypatch.setattr(c, "_request", request)
    with pytest.raises(IncompleteOrderEnumeration, match="REPEATED_PAGE"):
        c.get_open_orders()
    assert calls == 2


def test_open_orders_rejects_duplicate_ids_across_different_pages(monkeypatch):
    first = [_order(i, "2026-01-02T00:00:00Z") for i in range(500)]
    second = [_order(499, "2026-01-01T00:00:00Z")]
    pages = iter((first, second))
    c = AlpacaClient("k", "s", "AAPL")
    monkeypatch.setattr(c, "_request", lambda *args, **kwargs: next(pages))
    with pytest.raises(IncompleteOrderEnumeration, match="DUPLICATE_ORDER_ID"):
        c.get_open_orders()
