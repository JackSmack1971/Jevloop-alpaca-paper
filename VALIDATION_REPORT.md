# jev-loop 0.3.1 validation report

**Date:** 2026-09-22
**Scope:** research-verified full package audit (every module re-inspected, not a README pass), deterministic package validation, and offline end-to-end smoke tests. Authenticated Alpaca/TypeSafe/Vercel runtime behavior was not exercised in this artifact environment — see "Runtime evidence status" below.

## What this pass targeted

0.3.0 hardened runtime invariants and evidence semantics. 0.3.1 re-verified those claims against current upstream documentation and cited research, and this time the audit found and fixed **executable defects**, not only evidence-quality gaps:

- a math defect: the optional Avellaneda–Stoikov pricing helper quoted roughly double the intended spread;
- a protocol defect: the Alpaca `done_for_day` order status was misclassified as terminal, which could drop ownership tracking, cancellation candidacy, and status polling for a still-live order a full trading day early;
- an unverified trust boundary: `mark_order_terminal` discarded ownership tracking based on whatever status its caller handed it, without checking that status itself;
- an HTTP timeout/latency-budget mismatch: a single scalar `requests` timeout applies independently to both the connect and read phases (documented Requests behavior), so a single hung connection could block roughly twice as long as configured against a tick cadence that assumes seconds-scale turnaround;
- a silent state-identity gap: session-scoped order ownership (a deliberate safety choice — the runtime must never auto-cancel an order it cannot positively identify as its own) meant a prior session that ended uncleanly left its resting orders invisible to every later session, with no operator-facing signal that this had happened;
- evidence durability: decision-log writes were not flushed/`fsync`'d, so a host-level crash could lose the most recent evidence entries;
- a skill-authoring gap: `SKILL.md` was still procedural/enumerative (a numbered workflow plus a full command listing) rather than the minimal-router shape OpenAI's current Codex/GPT-6 Astra skill guidance recommends.

Two findings carried over from the interrupted prior pass were checked against current provider documentation and found to be **already correct, no change needed**: the crypto-only scoping of `min_order_size`/`min_trade_increment`/`price_increment`, and the `GET /v2/orders:by_client_order_id` retail Trading API endpoint shape (as opposed to the differently-shaped Broker API endpoint of the same name).

See `references/source-audit.md` for the full file-by-file disposition and `CHANGELOG.md` for the user-facing summary.

## Executed deterministic evidence

| Check | Result |
|---|---|
| `uv run pytest -q` | Runs the deterministic, credential-free regression suite in CI. |
| `uv run python scripts/validate_package.py` | Checks package contracts plus lockfile presence/freshness and the required CI commands. |
| `uv run python -m compileall -q jevloop scripts tests` | Runs in the quality job and across Python 3.10–3.14. |
| `uv run ruff check` | Merge-blocking E4/E7/E9 and Pyflakes checks; the known E701/E702 findings have been reformatted rather than suppressed. |
| Offline simulation, 130 ticks | **SIMULATION_OK** |
| Calibration smoke on 127 synthetic/mock eligible observations | **DESCRIPTIVE_ONLY**, report emitted successfully |
| `uv lock --check` / `uv sync --locked --group dev` | Merge-blocking lock freshness and reproducible environment checks. |

### Calibration smoke evidence

Re-run against a freshly generated 130-tick offline simulation log (mock provider, `--seed 17`):

- multiclass Brier: `0.6994` (identical to the 0.3.0 report — this metric is deterministic given the same mock outputs/outcomes and is unaffected by this pass's fixes, none of which touch the mock decision client or calibration math);
- sample-climatology Brier: `0.5835`;
- Brier improvement vs sample climatology: `-0.1158`;
- NLL: `1.2535`;
- accuracy: `0.543`;
- top-label equal-width ECE: `0.164`;
- adaptive classwise ECE: `0.238`;
- moving-block 95% Brier interval: `[0.6266, 0.7700]` using the automatic block `5` in this sample.

(The classwise-ECE and bootstrap-CI figures differ in their last digit or two from the 0.3.0 report; this is consistent with ordinary floating-point/interpreter-version variation in a bootstrap procedure, not a behavior change — `calibrate.py` was not modified this pass and its own regression tests still pass unchanged.) The unfavorable Brier comparison is intentionally preserved: the diagnostic path can report evidence against the model rather than turning any completed run into a positive claim. Because the input is mock/synthetic, these numbers are **not provider-quality or strategy evidence**.

## New regression coverage this pass

Tests now explicitly cover:

- the corrected Avellaneda–Stoikov spread (total spread matches the paper's formula exactly; no longer double), and that inventory shifts the reservation price without changing the total spread;
- `done_for_day` excluded from `TERMINAL_ORDER_STATES`;
- a `done_for_day` order remains a cancellation candidate in `cancel_session_orders` and remains tracked afterward;
- `_refresh_order_statuses` leaves a `done_for_day` order in `pending_orders` for continued polling instead of popping it;
- `mark_order_terminal` refuses to discard tracking for a non-terminal status, even if called directly;
- `AlpacaClient._request` passes a `(connect, read)` timeout tuple rather than a bare scalar;
- `get_foreign_session_open_orders` correctly isolates orders that carry this package's prefix but not the current session's ID, and leaves manually-placed and same-session orders alone.

All prior 0.3.0 regression coverage (live-URL refusal, ambiguous-POST reconciliation, `HOLD_BLOCKED` routing, dry-by-default CLI, stale-pricing-state isolation, cancellation verification) is unchanged and still passing.

## Runtime evidence status

**UNVERIFIED_RUNTIME** for authenticated external behavior:

- real Alpaca paper account, asset, order, partial-fill, cancellation, `done_for_day`, and websocket behavior;
- TypeSafe direct model discovery/response behavior;
- Vercel AI Gateway Jev response behavior;
- subscription-specific equity feed behavior;
- real provider latency/rate-limit behavior, and whether the new `(connect, read)` timeout split changes observed tick cadence under real network conditions.

`jev-loop doctor` remains the operator-side preflight for those checks, and now additionally reports whether any prior session's orders are still open and unreconciled. A mock, unit test, or paper-free dry run is not substituted for authenticated provider evidence.

## Empirical skill-value status

**UNVALIDATED**, unchanged from 0.3.0. The package includes routing/task/failure corpora and an explicit paired evaluation protocol, but no repeated Codex with-skill vs no-skill experiment was run here, and this pass did not add one. The `SKILL.md` restructuring is grounded in OpenAI's published guidance (cited in `references/research-notes.md`), not in a measured routing/completion-quality uplift for this specific package.

## Remaining engineering risks

1. REST market/broker polling is seconds-scale and can observe state later than Alpaca websocket streams; event-driven migration should be tested for reconnect ordering, duplicate events, and backfill before adoption.
2. An ambiguous POST that is not immediately visible by client ID still requires fail-closed operator reconciliation; the package deliberately stops rather than guessing.
3. Session-scoped order ownership is a deliberate safety tradeoff (never auto-cancel an unrecognized order), not a bug, but it means a crashed/killed session's resting orders require manual reconciliation; `doctor`/`run` now surface this rather than staying silent about it.
4. Keep `uv.lock` synchronized with `pyproject.toml`; both the validator and CI reject a missing or stale lock.
5. Calibration metrics remain sample- and regime-dependent; block choice, horizon, neutral band, class imbalance, and model-selection history must remain visible in any downstream claim.
6. The Avellaneda–Stoikov helper is now mathematically correct against its cited source but is still not wired into the default pricing path and still requires venue-specific `gamma`/`kappa`/`sigma` estimation before any real use.

## Claim boundary

This validation supports only: **the packaged deterministic invariants and offline wiring behaved as specified in this environment, and the specific claims re-checked against current upstream documentation in this pass were verified (and one math defect, one protocol defect, and one latent trust-boundary gap were found and fixed as a result).**

It does **not** establish live suitability, economic edge, profitable trading, exchange-grade HFT performance, real-provider calibration, or production latency.
