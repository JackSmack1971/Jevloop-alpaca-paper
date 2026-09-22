# Architecture and safety

Load this for code changes to state, battery, policy, risk, pricing, execution, or the loop.

## Ownership boundary

**Deterministic code:** timestamp parsing, returns/volatility, observed VWAP, spread/depth, normalized inventory/account state, policy composition, sizing, hard risk vetoes, price/quantity quantization, order identity, cancellation, and broker reconciliation.

**Probabilistic Jev:** seven bounded judgments: regime, direction, toxic flow, liquidity stress, quote environment, inventory pressure, execution health.

A Skill describes procedure; it is not a permission grant. Broker/network/filesystem effects remain runtime/policy concerns.

## Fail-closed ladder

Priority is `KILL > HOLD_LATE > HOLD_BLOCKED > RULES_ONLY > REDUCE > RUN`.

- `KILL`: cancel session-owned orders, verify no owned resting order remains, then attempt broker-confirmed flattening. If cancellation cannot be verified, flattening is blocked and manual broker reconciliation is required.
- `HOLD_LATE`: decision exceeded its time budget; cancel session-owned orders and submit nothing new.
- `HOLD_BLOCKED`: a non-kill hard-risk or execution/reconciliation condition is unresolved; no new orders.
- `RULES_ONLY`: Jev unavailable/invalid; deterministic fallback is `STAND_DOWN`, not autonomous quoting.
- `REDUCE`: valid response but low confidence/execution-health gate; reduce target size while all hard checks still apply.
- `RUN`: normal operation subject to per-side projected-position and order-count checks.

## Broker/execution invariants

- Runtime is Alpaca **paper-only** and CLI execution is dry by default; `--paper` is explicit.
- Never cancel account-wide orders.
- Every attempted order receives a unique session `client_order_id`; Alpaca's duplicate-client-ID behavior is used as an identity/reconciliation aid, not as permission to blind-retry POSTs.
- On transport/5xx ambiguity, query `GET /v2/orders:by_client_order_id`. If the outcome remains unknown, stop fail-closed.
- Before requoting, cancellation is followed by a broker read verifying no session-owned open order remains.
- A KILL flatten cannot run while owned resting-order cancellation remains uncertain.
- An accepted/submitted order is not a fill. Position/account truth comes from broker reads; terminal order state supplies fill/rejection evidence.
- Sell quantity cannot exceed broker-reconciled long inventory; this package does not open shorts.
- Pre-existing inventory with unknown holding age is not assigned an invented age; it blocks new exposure until independently reconciled.
- `TERMINAL_ORDER_STATES` in `jevloop/execution/alpaca.py` is `{filled, canceled, expired, rejected}` only. `done_for_day` is deliberately excluded: current Alpaca order-status documentation and support guidance describe it as "the order is done executing for the day, and will not receive further updates until the next trading day" -- a same-day pause, not closure. An earlier revision treated `done_for_day` as terminal, which discarded ownership tracking, dropped the order from cancellation candidates, and stopped polling it one trading day too early. `loop.py`'s `_refresh_order_statuses` and `_wait_terminal` import the same constant rather than redefining it, so the three call sites cannot drift out of sync again.
- Order ownership (`_owned_client_order_ids`, and `client_order_id` prefix matching) is scoped to the current process's random session ID, by design: the runtime must never auto-cancel an order it cannot positively identify as its own. The cost of that design is that a session which ends without a clean shutdown (killed, crashed, host restart) leaves its resting orders invisible to every later session's automatic reconciliation. `AlpacaClient.get_foreign_session_open_orders()` is a read-only check (surfaced by `doctor` and at `run` startup) that lists jevloop-prefixed open orders that do not belong to the current session, so an operator is told about the gap instead of it being silent. It never cancels those orders; that stays a manual/operator decision.

## Market-state freshness

Pricing and flow timestamps are distinct:

- quote/order-book timestamp controls `data_age_s` because those inputs determine executable pricing state;
- trade timestamp is logged separately as `trade_data_age_s`;
- a fresh trade cannot mask a stale quote/order book;
- future provider observations are rejected rather than clamped into plausibility.

The package currently polls REST endpoints. Alpaca's websocket market-data and `trade_updates` streams are a better future path for event-driven state/reconciliation, but adopting them requires explicit ordering/reconnect/backfill tests rather than assuming streaming is automatically correct.

## Pricing

Default pricing is unit-transparent: remain passive relative to observed top of book, add a bounded buffer/widening factor, and apply inventory-utilization skew. The Avellaneda–Stoikov helper remains research-only because its venue-specific parameters and units require empirical estimation rather than arbitrary defaults.

`avellaneda_stoikov_quotes` implements Avellaneda & Stoikov (2008): reservation price `r = s - q*gamma*sigma^2*(T-t)`, and *total* optimal spread `delta = gamma*sigma^2*(T-t) + (2/gamma)*ln(1+gamma/kappa)`, with each quote at `r +/- delta/2`. An earlier revision applied the full `delta` on each side (`reservation - delta`, `reservation + delta`), which doubled the intended bid-ask width; this is now `reservation - delta/2`, `reservation + delta/2`. The helper is still not wired into `bounded_quote_prices` or the default pricing path -- it remains an explicit research/backtesting entry point pending empirically estimated `gamma`/`kappa` for a specific venue and asset.
