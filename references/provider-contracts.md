# Provider contracts — verified 2026-09-22

Load this reference for setup, API, provider, symbol, rate-limit, or execution-contract work. Current official provider documentation outranks this snapshot if it diverges.

## TypeSafe / Jev

- TypeSafe's current OpenAPI (`https://api.typesafe.ai/docs`) exposes `POST /v1/systemone` and `GET /v1/models` with bearer auth and typed Noul/Choice/Score questions. Direct mode therefore requires an explicit `TYPESAFE_MODEL`; do not silently invent or float a model name.
- Vercel's 2026-09-21 Jev integration documents both a TypeSafe-compatible base URL (`https://ai-gateway.vercel.sh/typesafe`, preserving `/v1/systemone`) and a native AI Gateway evaluation surface at `POST https://ai-gateway.vercel.sh/v1/evaluate`.
- Vercel identifies the Gateway model as `typesafe-ai/jev`. This package intentionally uses the TypeSafe-compatible shape so direct TypeSafe and Gateway routes share one battery contract.
- Provider probabilities/confidence are observations to validate on this task distribution, not guaranteed calibration claims.
- Every returned choice/score probability distribution must use exactly the requested option keys, contain finite numeric values in `[0, 1]`, and sum to `1` within an absolute tolerance of `0.02`.

## Alpaca Trading API

- This package supports only the paper base `https://paper-api.alpaca.markets`. The live base `https://api.alpaca.markets` is documented by Alpaca but explicitly rejected by this runtime.
- `GET /v2/assets/{symbol_or_asset_id}` is authoritative for current asset status/tradability. Alpaca's 2026-06-24 schema update added `min_order_size`, `min_trade_increment`, and `price_increment`; current Alpaca asset-schema documentation confirms these three fields are populated for `crypto` assets only (not `us_equity`). `assets.require_tradable` requires them only when `asset_class == "crypto"`; equities fall back to conservative hard-coded price/quantity increments ($0.01 tick at/above $1, else $0.0001; a very small fractional-quantity increment) because Alpaca does not expose equity-side increments through this endpoint.
- Each order has a caller-provided `client_order_id`; current Alpaca documentation states duplicate client IDs are rejected and supports lookup via `GET /v2/orders:by_client_order_id?client_order_id=...` on the retail Trading API (`paper-api.alpaca.markets`/`api.alpaca.markets`; the differently-shaped `/v1/trading/accounts/{account_id}/orders:by_client_order_id` belongs to the separate Broker API and is not used here). The runtime uses that identity to reconcile ambiguous transport/5xx submission outcomes without blindly repeating POSTs.
- Limit-order acceptance is not a fill. Query broker order/position state or consume the `trade_updates` websocket stream to establish fills, partial fills, cancellations, and rejections.
- Order status `done_for_day` is **not terminal**. Current Alpaca order-status documentation lists it separately from the terminal group (`filled`, `canceled`, `expired`, `rejected`, and `replaced`, each documented as "no further updates will occur for the order"); `done_for_day` instead means "the order is done executing for the day, and will not receive further updates until the next trading day." Code that treats it as terminal will stop tracking/polling/cancelling a still-live order one trading day early. `jevloop.execution.alpaca.TERMINAL_ORDER_STATES` excludes it for exactly this reason.
- Crypto spot currently supports `gtc`/`ioc` for documented order types; this package uses `gtc` for crypto and `day` for equity orders. Fractional equity rules are not assumed to equal crypto rules.
- US-equity market data defaults to `iex` unless the operator selects a feed their subscription supports.
- Market REST endpoints expose provider timestamps per quote/order book/trade. Pricing freshness must follow quote/order-book time, not `max(quote_ts, trade_ts)`.
- Alpaca documents real-time websocket market streams; these are preferable for event-driven/low-latency state but require reconnect/order/backfill validation before replacing the REST scaffold.
- Do not hard-code a universal request/minute limit. On `429`, honor current provider headers/documentation and reduce cadence rather than embedding a stale subscription-specific number.
- Plain limit orders do not imply an exchange-guaranteed post-only instruction; the quote builder expresses passive **intent**, not a venue guarantee.

## Python HTTP/runtime tooling

- Requests does not retry failed connections automatically. This package only retries the Jev read-like decision request under a bounded deadline; broker order POSTs use identity-based reconciliation instead of generic retry.
- Requests has no default timeout; every external call here supplies one. A *single* scalar timeout value is documented by Requests to apply independently to both the connect and the read phase, so worst case one call can block for close to twice that scalar before raising -- against a 5-second default tick cadence, a 10-second scalar timeout could stall a tick for ~20s on a hung connection. Both the Alpaca adapter (`jevloop/execution/alpaca.py`) and the Jev decision client (`jevloop/client.py`) instead pass an explicit `(connect, read)` tuple: a ~3.05s connect timeout (Requests' own documented rationale: slightly above the default TCP retransmission window) bounds a hung connection attempt, while the read timeout keeps its full configured/remaining-deadline value so a slow-but-live response is not cut short. Decision latency is still measured independently (`meta["latency_ms"]`) and risk-gated via `max_decision_latency_ms` regardless of the timeout value chosen.
- `requests.Session` can provide keep-alive/connection pooling; introducing it is an optimization, not a change to authorization or retry semantics.
- `uv.lock` is the reproducible exact-resolution artifact for uv projects and should be committed once generated in a networked development environment. CI should prefer `uv sync --locked` after that lock exists.
