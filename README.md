# jev-loop

`jev-loop` is a **paper-only** market-decision research harness that separates deterministic computation from bounded probabilistic judgment:

- code owns timestamps, market-state features, account/inventory state, policy composition, sizing, hard risk gates, order ownership, and broker reconciliation;
- Jev answers seven typed judgment questions in one request;
- a fail-closed ladder decides whether any new paper-side effect is allowed;
- execution defaults to **dry/no-order mode** and requires explicit `--paper` to submit Alpaca paper orders;
- order submission is never treated as a fill.

It is a seconds-scale engineering/research scaffold, not exchange-grade HFT and not evidence of a profitable strategy.

## Evidence log schema v3

Both `run` and `simulate` write the same validated schema-v3 JSONL envelope. Each
process has one immutable `run_id`; records identify package/Git versions, runtime
mode and source, provider route/model, configuration digests, provider timestamps,
data ages, wall-clock decision bounds, monotonic latency, and sanitized broker
request correlation. Broker evidence connects Alpaca `X-Request-ID` values to the
run, client/broker order identifiers, submissions, reconciliation reads,
cancellation requests, and observed states. Authorization and credential fields are
rejected by the serializer and are never copied from HTTP request headers.

Older or malformed rows remain readable, but are marked `cohort_eligible=false`
with a `cohort_exclusion_reason`; calibration excludes them from strict cohorts
rather than silently mixing incomparable schemas. Existing v2 files do not need an
in-place migration.

## Install as a Codex skill

Place this directory at `$HOME/.agents/skills/jev-loop` or `<repo>/.agents/skills/jev-loop`. The bundled `agents/openai.yaml` disables implicit invocation because operational use can create paper-broker side effects; invoke it explicitly with `$jev-loop` when you want the workflow.

Standalone setup:

```bash
cd jev-loop
cp .env.example .env
uv sync --group dev
uv run pytest -q
uv run python scripts/validate_package.py
```

`uv.lock` is intentionally not fabricated in this artifact: dependency resolution was unavailable in the build environment. On a networked development machine, run `uv lock`, review the result, commit it, and use `uv sync --locked` in CI for reproducible installs.

## Safe first run

```bash
uv run jev-loop explain-split
uv run jev-loop simulate --ticks 30 --symbol BTC/USD
```

Simulation is offline. With Alpaca **paper** credentials and a real Jev provider configured:

```bash
uv run jev-loop doctor --symbol BTC/USD
uv run jev-loop run --ticks 30 --symbol BTC/USD
```

`run` is dry by default: it may read real provider/broker data but sends no orders. Paper orders require an explicit side-effect request:

```bash
uv run jev-loop run --paper --ticks 30 --symbol BTC/USD
```

`run --paper` also enforces the canonical preflight before activating paper authority or
allowing an order submission. This checks the paper endpoint, account and asset status,
provider configuration, and foreign-session `jevloop-` orders. `doctor` is an independent,
read-only inspection command which displays the same readiness decision; it is not an
authorization step. Mock judgments are always no-order and cannot be combined with `--paper`.

## Runtime safety properties

- The Alpaca adapter accepts only `https://paper-api.alpaca.markets`; the live trading base URL is rejected unconditionally.
- Syntactically valid symbols are not treated as proof of tradability; broker asset metadata and increments are authoritative.
- Pricing-state freshness comes from the quote/order-book timestamp. A newer trade cannot make stale executable pricing inputs look fresh.
- Non-kill risk vetoes enter `HOLD_BLOCKED`; they are not merely advisory and cannot leak through to quote submission.
- Before requoting or emergency flattening, session-owned cancellation must be reconciled as clear; an unresolved cancellation blocks the next external write.
- Each order uses a unique session `client_order_id`. Ambiguous transport/5xx POST outcomes are looked up by client order ID and are **not** blindly retried.
- If that lookup is still inconclusive, the run stops fail-closed rather than misclassifying the event as a rejection.
- Account-wide cancellation is never used. Accepted/submitted orders are never counted as fills.
- Foreign-session `jevloop-` orders block paper preflight and are reported as structured warnings in dry execution; they are never canceled automatically.
- Directional market orders remain disabled by default; spot sell quantity cannot exceed broker-reconciled long inventory.

## Probability diagnostics

`jev-loop calibrate` pairs the logged **direction probability vector** with chronological future outcomes and reports:

- multiclass Brier score;
- sample-climatology and uniform references;
- Brier improvement relative to those references;
- negative log loss and top-class accuracy;
- legacy top-label/equal-width ECE as a diagnostic;
- adaptive classwise ECE with equal-count bins;
- moving-block-bootstrap Brier uncertainty plus block-length sensitivity;
- class-support warnings.

Moving-block confidence intervals are a single-series operation: `--symbol` is
required whenever `--bootstrap-draws` is greater than zero (the default). Use
`--bootstrap-draws 0` to retain multi-symbol descriptive metrics; that output
explicitly marks the aggregate interval as omitted rather than pooling symbols.
The interval metadata records the selected symbol, cohort, block size and
sensitivity values, and observation count.

The command always labels its result `DESCRIPTIVE_ONLY`. No fixed sample size or single calibration metric becomes a deployment-readiness certificate. Strategy-performance claims require separate temporal/purged out-of-sample analysis, costs/slippage, and selection/multiple-testing controls. See `references/evaluation-methodology.md`.

## Why this is not called HFT

The default market path is REST polling on a seconds-scale cadence. Alpaca documents websocket market/order streams, which are the appropriate direction for lower-latency/event-driven reconciliation, but this package does not claim exchange-grade latency, colocation, or post-only semantics.

## Dashboard

```bash
uv run jev-loop serve
```

Open `http://127.0.0.1:8765/`. The server exposes only `latest.json` plus bundled dashboard assets; it does not map arbitrary filesystem paths or expose the complete log.

## Package map

- `SKILL.md` — concise Codex workflow and invariants.
- `agents/openai.yaml` — interface metadata and implicit-routing policy; runtime gates authorize paper effects separately.
- `jevloop/` — runtime implementation.
- `tests/` — deterministic offline regression tests.
- `references/` — progressive provider, architecture/safety, evaluation, research, and live-boundary detail.
- `scripts/validate_package.py` — deterministic package guard.
- `evals/` — routing/task/failure corpora and evaluation protocol.
- `assets/dashboard/` — loopback-only dashboard assets.
