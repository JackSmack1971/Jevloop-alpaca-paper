"""Alpaca paper execution and market data with session-scoped order ownership.

The packaged runtime is paper-only. Live-money authority is deliberately absent:
behavioral instructions can request safer behavior, but they cannot substitute for
an executable capability boundary.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import requests

from ..assets import AssetSpec, classify_symbol, from_alpaca_asset, require_tradable
from ..state import TradeTick, parse_rfc3339
from ..evidence import PROCESS_RUN_ID

PAPER_TRADING_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_TRADING_BASE_URL = "https://api.alpaca.markets"
CRYPTO_DATA_BASE = "https://data.alpaca.markets/v1beta3/crypto/us"
EQUITY_DATA_BASE = "https://data.alpaca.markets/v2/stocks"
SESSION_CLIENT_PREFIX = "jevloop-"
# Per current Alpaca order-status documentation, these states are final: "no further
# updates will occur for the order". `done_for_day` is deliberately excluded: Alpaca
# documents it as "the order is done executing for the day, and will not receive
# further updates until the next trading day" -- the order can resume/receive updates
# later, so it is a same-day pause, not order closure. Treating it as terminal would
# make the runtime drop ownership tracking and stop polling a resting order early.
TERMINAL_ORDER_STATES = {"filled", "canceled", "expired", "rejected"}
# Non-terminal: the order is not done, just paused until the next session/trading day.
# It stays tracked, stays a cancellation candidate, and keeps being polled.
PAUSED_UNTIL_NEXT_SESSION_STATES = {"done_for_day"}
# A connect timeout of ~3.05s (slightly above the default TCP retransmission window)
# bounds how long a hung connection attempt can block a single call; the read timeout
# stays fully configurable via `timeout_s` so slow-but-live responses are not cut off.
_CONNECT_TIMEOUT_S = 3.05


class AlpacaConfigError(RuntimeError):
    pass


class AlpacaAPIError(RuntimeError):
    def __init__(self, status_code: int, body: str, *, retry_after_s: float | None = None):
        super().__init__(f"HTTP {status_code}: {body[:300]}")
        self.status_code = status_code
        self.body = body
        self.retry_after_s = retry_after_s


class UnknownOrderOutcome(RuntimeError):
    """Raised when a POST may have reached Alpaca but reconciliation is inconclusive."""

    def __init__(self, client_order_id: str, cause: AlpacaAPIError, reconcile_error: Exception | None = None):
        detail = f"order outcome unknown for client_order_id={client_order_id}: {cause}"
        if reconcile_error is not None:
            detail += f"; reconciliation failed: {reconcile_error}"
        super().__init__(detail)
        self.client_order_id = client_order_id
        self.cause = cause
        self.reconcile_error = reconcile_error


class MarketClosedError(RuntimeError):
    pass


class LiveTradingRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketView:
    best_bid: float
    best_ask: float
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    trades: list[TradeTick]
    quote_ts: float
    trade_ts: float | None = None

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def market_data_ts(self) -> float:
        """Timestamp of the executable pricing state, not the freshest auxiliary event."""
        return self.quote_ts


def resolve_trading_base_url(*, live: bool = False, **_: object) -> str:
    """Resolve the only supported trading runtime.

    Kept as a small compatibility seam so callers attempting the old live path fail
    explicitly instead of silently changing behavior.
    """
    if live:
        raise LiveTradingRefused("live-money trading is not a capability of this package")
    return PAPER_TRADING_BASE_URL


class AlpacaClient:
    """Narrow paper-only Alpaca adapter with ambiguous-POST reconciliation."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        symbol: str,
        *,
        base_url: str = PAPER_TRADING_BASE_URL,
        timeout_s: float = 10.0,
    ):
        if base_url.rstrip("/") != PAPER_TRADING_BASE_URL:
            raise AlpacaConfigError("this package only permits Alpaca paper trading")
        self.base_url = PAPER_TRADING_BASE_URL
        self.is_live = False
        self.symbol = classify_symbol(symbol).symbol
        self.asset_hint = classify_symbol(symbol)
        self.equity_feed = os.getenv("ALPACA_EQUITY_FEED", "iex")
        self.timeout_s = timeout_s
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }
        self.session_id = uuid.uuid4().hex[:12]
        self._client_seq = 0
        self._owned_client_order_ids: set[str] = set()
        self._clock_cache: tuple[float, dict] | None = None
        self.run_id = PROCESS_RUN_ID
        self._request_evidence: list[dict] = []

    def _request(self, method: str, url: str, **kwargs):
        started = time.time()
        body = kwargs.get("json") if isinstance(kwargs.get("json"), dict) else {}
        event = {
            "run_id": self.run_id,
            "method": method.upper(),
            "resource": url.split("?", 1)[0].rsplit("/", 2)[-2:],
            "request_started_at": started,
            "client_order_id": body.get("client_order_id") or (kwargs.get("params") or {}).get("client_order_id"),
            "operation": (
                "submission" if method.upper() == "POST" and url.endswith("/v2/orders")
                else "cancellation" if method.upper() == "DELETE"
                else "reconciliation_read" if "/orders" in url
                else "provider_read"
            ),
        }
        if "/orders/" in url:
            event["order_id"] = url.rstrip("/").rsplit("/", 1)[-1]
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers,
                timeout=(min(_CONNECT_TIMEOUT_S, self.timeout_s), self.timeout_s),
                **kwargs,
            )
        except requests.RequestException as exc:
            event.update({"request_completed_at": time.time(), "status_code": 0, "request_id": None})
            self._request_evidence.append(event)
            raise AlpacaAPIError(0, f"transport failure: {exc}") from exc
        request_id = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
        event.update({
            "request_completed_at": time.time(), "status_code": response.status_code,
            "request_id": request_id,
        })
        try:
            payload = response.json() if response.text else {}
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            event["order_id"] = payload.get("id") or event.get("order_id")
            event["broker_state"] = payload.get("status")
            event["client_order_id"] = event["client_order_id"] or payload.get("client_order_id")
        self._request_evidence.append(event)
        if response.status_code >= 400:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after_s = float(retry_after) if retry_after else None
            except ValueError:
                retry_after_s = None
            raise AlpacaAPIError(response.status_code, response.text, retry_after_s=retry_after_s)
        if not response.text:
            return {}
        if payload is None:
            raise AlpacaAPIError(response.status_code, "provider returned invalid JSON")
        return payload

    def drain_request_evidence(self) -> list[dict]:
        """Return and clear sanitized HTTP correlation metadata."""
        events, self._request_evidence = self._request_evidence, []
        return events

    def get_asset(self) -> dict:
        encoded = quote(self.symbol, safe="")
        return self._request("GET", f"{self.base_url}/v2/assets/{encoded}")

    def load_asset_spec(self) -> AssetSpec:
        spec = from_alpaca_asset(self.symbol, self.get_asset())
        require_tradable(spec)
        return spec

    def get_account(self) -> dict:
        return self._request("GET", f"{self.base_url}/v2/account")

    def get_position(self) -> dict | None:
        encoded = quote(self.symbol, safe="")
        try:
            return self._request("GET", f"{self.base_url}/v2/positions/{encoded}")
        except AlpacaAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    def get_clock(self, cache_s: float = 5.0) -> dict:
        now = time.monotonic()
        if self._clock_cache and now - self._clock_cache[0] < cache_s:
            return self._clock_cache[1]
        payload = self._request("GET", f"{self.base_url}/v2/clock")
        self._clock_cache = (now, payload)
        return payload

    def is_market_open(self, spec: AssetSpec) -> bool:
        return True if spec.is_24_7 else bool(self.get_clock().get("is_open"))

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"{self.base_url}/v2/orders/{order_id}")

    def get_order_by_client_order_id(self, client_order_id: str) -> dict:
        return self._request(
            "GET",
            f"{self.base_url}/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
        )

    def get_open_orders(self) -> list[dict]:
        payload = self._request(
            "GET",
            f"{self.base_url}/v2/orders",
            params={"status": "open", "symbols": self.symbol, "limit": 100},
        )
        return payload if isinstance(payload, list) else []

    def _next_client_order_id(self) -> str:
        self._client_seq += 1
        value = f"{SESSION_CLIENT_PREFIX}{self.session_id}-{self._client_seq}"
        self._owned_client_order_ids.add(value)
        return value

    @staticmethod
    def is_owned_order(order: dict, session_id: str | None = None) -> bool:
        value = str(order.get("client_order_id") or "")
        if session_id is None:
            return value.startswith(SESSION_CLIENT_PREFIX)
        return value.startswith(f"{SESSION_CLIENT_PREFIX}{session_id}-")

    def _submit_order(self, body: dict) -> dict:
        """Submit once; reconcile ambiguous transport/5xx outcomes by client order ID.

        Alpaca rejects duplicate client_order_id values. We still do not automatically
        resubmit here: a lookup miss immediately after an ambiguous POST is not proof
        the original request was never accepted.
        """
        client_order_id = str(body["client_order_id"])
        try:
            order = self._request("POST", f"{self.base_url}/v2/orders", json=body)
            if str(order.get("status") or "") in TERMINAL_ORDER_STATES:
                self._owned_client_order_ids.discard(client_order_id)
            return order
        except AlpacaAPIError as exc:
            if exc.status_code != 0 and exc.status_code < 500:
                self._owned_client_order_ids.discard(client_order_id)
                raise
            try:
                order = self.get_order_by_client_order_id(client_order_id)
                if str(order.get("status") or "") in TERMINAL_ORDER_STATES:
                    self._owned_client_order_ids.discard(client_order_id)
                return order
            except AlpacaAPIError as reconcile_exc:
                if reconcile_exc.status_code == 404:
                    raise UnknownOrderOutcome(client_order_id, exc) from exc
                raise UnknownOrderOutcome(client_order_id, exc, reconcile_exc) from exc

    def submit_limit_order(self, *, side: str, qty: float, limit_price: float, spec: AssetSpec) -> dict:
        if not self.is_market_open(spec):
            raise MarketClosedError(f"{spec.symbol} market is closed")
        body = {
            "symbol": spec.symbol,
            "qty": str(qty),
            "side": side,
            "type": "limit",
            "time_in_force": "gtc" if spec.asset_class == "crypto" else "day",
            "limit_price": str(limit_price),
            "client_order_id": self._next_client_order_id(),
        }
        return self._submit_order(body)

    def submit_market_order(self, *, side: str, qty: float, spec: AssetSpec) -> dict:
        if not self.is_market_open(spec):
            raise MarketClosedError(f"{spec.symbol} market is closed")
        body = {
            "symbol": spec.symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "gtc" if spec.asset_class == "crypto" else "day",
            "client_order_id": self._next_client_order_id(),
        }
        return self._submit_order(body)

    def mark_order_terminal(self, order: dict) -> None:
        """Discard local ownership tracking for an order this client submitted.

        Defensive on the order's own reported status rather than trusting the caller:
        the sole current caller (`loop._refresh_order_statuses`) already gates this on
        `TERMINAL_ORDER_STATES`, but this method's name promises the order is finished,
        so it verifies that itself rather than discarding tracking for whatever status
        it is handed. That keeps a non-terminal status such as `done_for_day` from
        being forgotten if this is ever called from a different or future call site.
        """
        status = str(order.get("status") or "")
        if status not in TERMINAL_ORDER_STATES:
            return
        client_order_id = str(order.get("client_order_id") or "")
        if client_order_id:
            self._owned_client_order_ids.discard(client_order_id)

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"{self.base_url}/v2/orders/{order_id}")

    def cancel_session_orders(self) -> list[str]:
        """Cancel only orders belonging to this process, including ambiguous POSTs."""
        candidates: dict[str, dict] = {}
        # First reconcile every client ID generated by this process. This avoids relying
        # solely on a paginated account-wide open-order listing.
        for client_order_id in sorted(self._owned_client_order_ids):
            try:
                order = self.get_order_by_client_order_id(client_order_id)
            except AlpacaAPIError as exc:
                if exc.status_code == 404:
                    continue
                raise
            if str(order.get("status") or "") in TERMINAL_ORDER_STATES:
                self._owned_client_order_ids.discard(client_order_id)
            elif order.get("id"):
                candidates[str(order["id"])] = order

        # Also scan visible open orders as a defensive fallback.
        for order in self.get_open_orders():
            if self.is_owned_order(order, self.session_id) and order.get("id"):
                candidates[str(order["id"])] = order

        cancelled: list[str] = []
        for order_id in sorted(candidates):
            self.cancel_order(order_id)
            cancelled.append(order_id)
        return cancelled

    def get_foreign_session_open_orders(self) -> list[dict]:
        """Open orders that carry this package's client-order prefix but not this
        process's session ID.

        Session ownership (and therefore automatic cancellation) is scoped to the
        current process's random session ID by design: the runtime must never cancel
        an order it cannot positively identify as its own. That means a prior run that
        ended without a clean shutdown (killed, crashed, host restart) leaves its
        resting orders invisible to every later session's automatic reconciliation.
        This is a read-only diagnostic to surface that gap -- it never cancels
        anything -- so an operator can decide whether to reconcile those orders by
        hand before starting a new paper run.
        """
        return [
            order
            for order in self.get_open_orders()
            if self.is_owned_order(order) and not self.is_owned_order(order, self.session_id)
        ]

    def _crypto_market_view(self, recent_limit: int) -> MarketView:
        ob = self._request(
            "GET",
            f"{CRYPTO_DATA_BASE}/latest/orderbooks",
            params={"symbols": self.symbol},
        ).get("orderbooks", {}).get(self.symbol, {})
        bids = [(float(x["p"]), float(x["s"])) for x in ob.get("b", [])]
        asks = [(float(x["p"]), float(x["s"])) for x in ob.get("a", [])]
        if not bids or not asks:
            raise AlpacaAPIError(200, "crypto order book missing bid or ask")
        if not ob.get("t"):
            raise AlpacaAPIError(200, "crypto order book missing timestamp")
        raw = self._request(
            "GET",
            f"{CRYPTO_DATA_BASE}/trades",
            params={"symbols": self.symbol, "limit": recent_limit},
        ).get("trades", {}).get(self.symbol, [])
        trades = [_parse_trade(t, crypto=True) for t in raw]
        quote_ts = parse_rfc3339(ob["t"])
        trade_ts = max((t.ts for t in trades), default=None)
        return MarketView(bids[0][0], asks[0][0], bids, asks, trades, quote_ts, trade_ts)

    def _equity_market_view(self, recent_limit: int) -> MarketView:
        encoded = quote(self.symbol, safe="")
        q = self._request(
            "GET", f"{EQUITY_DATA_BASE}/{encoded}/quotes/latest", params={"feed": self.equity_feed}
        ).get("quote", {})
        best_bid = float(q.get("bp") or 0)
        best_ask = float(q.get("ap") or 0)
        if best_bid <= 0 or best_ask <= 0:
            raise AlpacaAPIError(200, "equity quote missing bid or ask")
        if not q.get("t"):
            raise AlpacaAPIError(200, "equity quote missing timestamp")
        raw = self._request(
            "GET",
            f"{EQUITY_DATA_BASE}/{encoded}/trades",
            params={"limit": recent_limit, "feed": self.equity_feed},
        ).get("trades", [])
        trades = [_parse_trade(t, crypto=False) for t in raw]
        quote_ts = parse_rfc3339(q["t"])
        trade_ts = max((t.ts for t in trades), default=None)
        return MarketView(
            best_bid,
            best_ask,
            [(best_bid, float(q.get("bs") or 0))],
            [(best_ask, float(q.get("as") or 0))],
            trades,
            quote_ts,
            trade_ts,
        )

    def get_market_view(self, spec: AssetSpec, recent_limit: int = 100) -> MarketView:
        if spec.asset_class == "crypto":
            return self._crypto_market_view(recent_limit)
        return self._equity_market_view(recent_limit)


def _parse_trade(payload: dict, *, crypto: bool) -> TradeTick:
    side = None
    if crypto:
        marker = str(payload.get("tks") or "").upper()
        if marker == "B":
            side = "buy"
        elif marker == "S":
            side = "sell"
    return TradeTick(
        ts=parse_rfc3339(payload["t"]),
        price=float(payload["p"]),
        size=float(payload["s"]),
        side=side,
        trade_id=str(payload.get("i")) if payload.get("i") is not None else None,
    )


def client_from_env(*, symbol: str, live: bool = False, **_: object) -> AlpacaClient:
    if live:
        raise LiveTradingRefused("live-money trading is not a capability of this package")
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise AlpacaConfigError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")
    return AlpacaClient(api_key, secret_key, symbol)
