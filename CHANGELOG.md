# Changelog

## 0.3.2 — 2026-09-22

Release-completeness and executable-invariant pass across the paper runtime, evidence,
evaluation, dependency, and automation surfaces.

- Made one canonical paper preflight result the authority for paper activation, with typed readiness failures and foreign-session-order blocking shared by `doctor` and `run --paper`.
- Added typed provider-schema failures so malformed or structurally invalid decision responses fail closed without being conflated with transport errors.
- Reconciled session-owned orders on abnormal loop exits before propagating the original failure, while preserving evidence about any reconciliation failure.
- Introduced evidence schema v3 with validated serialization, process/run correlation, request and broker identifiers, configuration provenance, and explicit cohort-exclusion reasons for older or malformed records.
- Isolated calibration cohorts by schema, package/Git/config/provider provenance and required symbol-scoped moving-block bootstrap intervals instead of pooling incomparable observations.
- Enforced total decision-response deadlines, including late successful responses, and recorded monotonic timing evidence for the complete provider call.
- Enabled implicit skill routing while keeping paper side effects behind independent user-intent, `--paper`, canonical-preflight, and environment-policy gates.
- Added an opt-in executable paired Codex evaluation runner with isolated worktrees, repeated with-skill/without-skill trials, deterministic graders, raw counts, and redacted audit output. External evaluation remains separate from offline verification and is not claimed unless actually run.
- Extracted tick decision logic into a pure reducer shared by runtime and simulation, with parity coverage for state transitions, actions, and evidence fields.
- Committed `uv.lock` and documented locked synchronization and freshness checking for reproducible Python 3.10+ dependency resolution.
- Added least-privilege GitHub Actions CI gates on Python 3.10 for locked sync, lock freshness, package validation, the full test suite, Ruff, and byte-compilation.

## 0.3.1 — 2026-09-22

Research-verified correctness, protocol, and evidence-durability pass. Every module was re-audited against current upstream documentation (Alpaca order-status/asset-schema docs, Requests timeout docs, OpenAI Codex/Astra skill guidance) rather than prose review alone; see `references/source-audit.md` for the full file-by-file record.

- **Fixed a spread-doubling math defect**: `avellaneda_stoikov_quotes` applied the Avellaneda & Stoikov (2008) *total* spread term as a half-spread on each side, quoting roughly 2x the intended bid-ask width. The helper is still research-only and not wired into default pricing.
- **Fixed a protocol defect**: `done_for_day` was incorrectly treated as a terminal Alpaca order status. Current Alpaca documentation describes it as a same-day pause ("will not receive further updates until the next trading day"), not closure; treating it as terminal dropped ownership tracking, cancellation candidacy, and status polling for a still-live order one trading day early. `TERMINAL_ORDER_STATES` is now the single source of truth (`loop.py` imports it instead of redefining it twice), and `mark_order_terminal` independently verifies terminality rather than trusting its caller.
- **Hardened HTTP timeouts**: the Alpaca adapter and the Jev decision client now pass an explicit `(connect, read)` timeout tuple instead of one scalar, which Requests documents as applying independently to both phases (so a single call could previously block for close to 2x the configured value). The connect timeout is bounded near the default TCP retransmission window; the read timeout keeps its full configured/remaining-deadline value.
- **Added prior-session order visibility**: order ownership is deliberately scoped to the current process's session ID so the runtime never auto-cancels an order it cannot positively identify as its own; the cost is that a session that ends uncleanly (killed, crashed, host restart) leaves its orders invisible to later sessions' automatic reconciliation. `doctor` and `run` now surface (read-only, never auto-cancel) any open order that carries this package's client-order prefix but belongs to a different session.
- **Added evidence durability**: decision-log writes (`~/.jev-loop/log.jsonl`) are now flushed and `fsync`'d, not just closed, so a host-level crash is less likely to lose the most recent evidence.
- **Restructured `SKILL.md` as a minimal router**, per OpenAI's "Rethinking skills and prompts for GPT-6 Astra" guidance: invariants, then a task→reference table, then one evidence/entry-point paragraph. Removed the enumerated command list and numbered workflow recipe, which duplicated `README.md`/`--help`; kept every safety invariant and every validator-required string.
- Removed several genuinely unused imports/locals in `jevloop/assets.py`, `jevloop/__main__.py`, `jevloop/serve.py`, and `jevloop/simulate.py`, surfaced once `ruff` could run in this environment for the first time; no behavior change. Pinned an explicit `[tool.ruff.lint]` rule selection in `pyproject.toml` so lint scope no longer depends on whichever ruff version happens to be installed.
- Confirmed (no change needed): the crypto-only scoping of `min_order_size`/`min_trade_increment`/`price_increment`, and the `GET /v2/orders:by_client_order_id` retail Trading API endpoint shape, both match current Alpaca documentation exactly.
- Added regression tests for all of the above; full suite and `scripts/validate_package.py` re-run clean. See `VALIDATION_REPORT.md`.

## 0.3.0 — 2026-09-22

Research- and contract-driven hardening pass.

- Removed the executable live-money path entirely; the Alpaca adapter is paper-only and rejects the live base URL regardless of prompt/configuration.
- Changed `jev-loop run` to dry/no-order by default; paper submission now requires explicit `--paper`, and mock judgments cannot drive paper orders.
- Added `HOLD_BLOCKED` so non-kill hard-risk verdicts are enforced as a no-new-order state rather than merely surfacing inside later per-order checks.
- Split pricing-state freshness from trade-flow freshness so a recent trade cannot mask stale quote/order-book inputs.
- Added client-order-ID reconciliation for ambiguous Alpaca POST transport/5xx outcomes; ambiguous outcomes are not blindly retried or misreported as rejections.
- Strengthened cancellation semantics: requoting and KILL flattening require broker verification that session-owned resting orders are clear.
- Expanded calibration diagnostics with sample-climatology references, adaptive classwise ECE, block-length sensitivity, support warnings, and an always-descriptive status instead of an arbitrary readiness threshold.
- Tightened the Codex skill for progressive disclosure, explicitly disabled implicit invocation, and clarified that Skill instructions are behavioral workflow rather than runtime authority.
- Updated provider contracts for current TypeSafe/Vercel Jev surfaces, Alpaca client-order lookup/streaming guidance, Requests timeout/retry semantics, and uv lockfile practice.
- Added/updated regression tests for the new runtime invariants.

## 0.2.0 — 2026-09-22

Full Codex-oriented rebuild of the original Claude onboarding bundle. See `references/source-audit.md` for the 0.2.0 source-by-source migration record.
