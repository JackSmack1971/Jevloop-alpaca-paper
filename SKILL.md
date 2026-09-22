---
name: jev-loop
description: Operate, inspect, calibrate, or modify the jev-loop Jev + Alpaca paper-trading harness. Use for its typed decision battery, deterministic market state, risk/policy gates, dry or explicit paper execution, broker reconciliation, probability diagnostics, and local dashboard. Not for general investing advice, unrelated backtesting, or live-money trading.
---

# jev-loop

The packaged **paper-only decision harness**. This file is a router, not a runbook:
it states what must never be violated, then points at references — load only the one
the task needs, not all of them. Procedure detail, command syntax, and evidence
already live in scripts, `--help`, and the references below; this file does not repeat
them. A skill describes behavior; it does not grant broker, filesystem, network, or
credential authority — those boundaries are enforced in the runtime, not in this text.

## Non-negotiable invariants

- Code owns timestamps, features, policy composition, sizing, risk, order ownership, and broker reconciliation. Jev answers only the seven bounded questions in `jevloop/battery.py`.
- Runtime default is **dry execution**. Paper order submission requires the user's explicit request and `--paper`; mock judgments can never drive orders.
- Live-money execution is not a capability of this package. Do not add a live endpoint, credential gate, or hidden override as part of ordinary operation.
- `run --paper` enforces the canonical broker, asset, provider, and order-ownership preflight before paper authority is activated. `doctor` is an independent inspection command that displays the same readiness result; running it is useful operationally but does not grant authorization.
- Use provider timestamps and broker state. Never infer a fill from submission, fabricate state, or let fresh trade events mask stale pricing quotes/order books.
- On stale/invalid state, late/invalid Jev output, unresolved risk veto, provider failure, cancellation uncertainty, or ambiguous order outcome: fail closed, place no new orders, and reconcile session-owned orders.
- Never use account-wide cancellation. A KILL flatten may proceed only after session-owned resting-order cancellation is broker-verifiably clear. Order ownership is scoped to the current process session; `doctor` and `run` surface — but never auto-cancel — orders left open by a prior, uncleanly-ended session.

## Load only what the task needs

- providers/API/setup/timeouts → `references/provider-contracts.md`
- state/risk/execution/loop/order-identity internals → `references/architecture-and-safety.md`
- calibration or a performance/edge claim → `references/evaluation-methodology.md`
- a live-money request → `references/live-trading.md`
- why a design or research choice was made → `references/research-notes.md`

## Working on this package

Get evidence before changing behavior, and choose the narrowest mode that answers the
request — offline `simulate`, real-data `run` with no orders, or explicit `run --paper`
only when the user asked for paper side effects. For code changes: run focused tests,
then `uv run pytest -q` and `uv run python scripts/validate_package.py`. Report
executed evidence exactly; anything not actually exercised here (credentials, network,
live provider/broker behavior) is `UNVERIFIED_RUNTIME`, and simulation or mocks never
substitute for it.

Entry point: `uv run jev-loop <doctor|run|simulate|calibrate|serve|validate-symbol|explain-split>`; add `--help` to any subcommand for its flags.
