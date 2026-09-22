# Source-file audit — 0.2.0 to 0.3.0 hardening pass

Every package file was re-inspected in the 0.3.0 pass. Unchanged files were retained because their responsibilities remained consistent; material changes are recorded here so the package does not rely on a prose changelog as proof of implementation.

| Area/file | 0.3.0 disposition | Material reason |
|---|---|---|
| `SKILL.md` | Tightened | Shorter progressive-disclosure workflow; explicit Skill≠authority boundary; dry-by-default/paper-explicit invariants; new `HOLD_BLOCKED`; no live runtime. |
| `agents/openai.yaml` | Hardened | `allow_implicit_invocation=false` because operational activation can lead to paper broker side effects. |
| `README.md` | Rewritten | Matches actual paper-only runtime, dry default, reconciliation behavior, updated calibration semantics, uv lock guidance. |
| `.env.example` | Tightened | Paper credentials only; no live enablement concept. |
| `pyproject.toml` / `jevloop.__version__` | Updated | Version 0.3.0; dev tooling moved to PEP 735-style dependency group used by current uv. |
| `jevloop/execution/alpaca.py` | Hardened | Live base rejected unconditionally; `client_order_id` reconciliation; no blind ambiguous POST retry; tracked client IDs included in cancellation; quote/trade timestamps separated. |
| `jevloop/loop.py` | Hardened | Dry execution default; explicit `--paper`; cancellation verification before requote/flatten; non-kill hard risk becomes `HOLD_BLOCKED`; unknown POST outcome stops fail-closed. |
| `jevloop/ladder.py` | Extended | Adds `HOLD_BLOCKED` and accepts hard-risk `risk_ok` separately from kill semantics. |
| `jevloop/state.py` | Extended | `data_age_s` remains executable pricing-state age; separate `trade_data_age_s` added for diagnostics. |
| `jevloop/calibrate.py` | Rewritten | Sample-climatology reference, Brier difference improvements, adaptive classwise ECE, bootstrap block sensitivity, class-support warnings, `DESCRIPTIVE_ONLY` status. |
| `references/*` | Reverified/rewritten | Current OpenAI/Codex, TypeSafe/Vercel, Alpaca, Requests, uv, and academic evidence distinguished from package claims. |
| `evals/README.md` | Added | Defines paired/repeated evaluation protocol; corpora alone are not claimed as empirical skill uplift. |
| `evals/tasks.jsonl` | Updated | Live task now expects absence of capability, not a manual magic-phrase handoff. |
| `tests/test_alpaca_execution.py` | Rewritten | Tests hard live refusal, single POST behavior, client-ID reconciliation, and unknown-outcome classification. |
| `tests/test_calibrate.py` | Extended | Tests descriptive status, sample climatology, and adaptive classwise ECE. |
| `tests/test_policy_risk_ladder_pricing.py` | Extended | Tests non-kill hard risk routes to `HOLD_BLOCKED`. |
| `tests/test_state.py` | Extended | Regression test ensures fresh trades cannot mask stale pricing state. |
| `tests/test_cli_offline.py` | Extended | Verifies `run` is dry by default and only `--paper` enables paper order submission. |
| dashboard assets | Updated | `HOLD_BLOCKED` receives fail-closed visual treatment; no new external dependency. |
| remaining runtime/tests/assets | Re-inspected, retained | No material contract drift identified; existing deterministic ownership/battery/policy/dashboard behavior remained compatible. |

## Earlier 0.2.0 rebuild

The 0.2.0 release had already replaced the original monolithic Claude-oriented bundle with a Codex-routable Skill, deterministic provider/broker state handling, session-owned cancellation, strict typed battery validation, paper execution, simulation/doctor/calibration workflows, local dashboard hardening, tests, and eval corpora. The 0.3.0 pass focuses on converting remaining behavioral expectations into executable invariants and strengthening statistical/evidence semantics rather than expanding trading scope.

## 0.3.1 — research-verified correctness/protocol/evidence pass

Every module was re-inspected against current upstream documentation (Alpaca order-status and asset-schema docs, the Requests timeout docs, OpenAI's Codex skills and GPT-6 Astra guidance) and the cited academic sources were spot-checked. Unlike 0.3.0, this pass found and fixed executable defects rather than only strengthening evidence semantics.

| Area/file | 0.3.1 disposition | Material reason |
|---|---|---|
| `jevloop/pricing.py` | Fixed (math defect) | `avellaneda_stoikov_quotes` applied the *full* Avellaneda & Stoikov (2008) spread term on each side of the reservation price, quoting a bid-ask width roughly double the paper's formula. Now divides by two before offsetting each side. Not wired into the default pricing path either before or after this fix. |
| `jevloop/execution/alpaca.py` | Fixed (protocol defect) + hardened | `TERMINAL_ORDER_STATES` incorrectly included `done_for_day`, which current Alpaca documentation describes as a same-day pause ("will not receive further updates until the next trading day"), not order closure; this caused ownership tracking, cancellation candidacy, and status polling to stop one trading day early. `mark_order_terminal` now independently verifies terminality instead of trusting every caller. `_request` now passes a bounded `(connect, read)` timeout tuple instead of one scalar applied to both phases. Added `get_foreign_session_open_orders` (read-only) so a prior session's un-cancelled orders are surfaced, not silently forgotten. |
| `jevloop/loop.py` | Fixed (DRY/protocol) + hardened | `_refresh_order_statuses` and `_wait_terminal` each redefined their own terminal-state set (including the same `done_for_day` bug); both now import the single corrected `TERMINAL_ORDER_STATES` constant so the three call sites cannot drift again. `_append_log` now flushes and `fsync`s each decision record. `run()` now prints a non-blocking startup notice when a prior session's orders are still open. |
| `jevloop/client.py` | Hardened | Same bounded `(connect, read)` timeout tuple applied to the Jev decision POST, consistent with the Alpaca adapter fix and the same cited Requests-docs rationale. |
| `jevloop/doctor.py` | Extended | Preflight now reports prior-session open orders (or their absence) using the same read-only check as `run()`'s startup notice. |
| `SKILL.md` | Rewritten | Restructured from a numbered workflow + full command listing into an explicit minimal router (invariants, then a reference-selection table, then one evidence/entry-point paragraph), per OpenAI's "Rethinking skills and prompts for GPT-6 Astra" guidance. All required validator strings and referenced files are preserved; procedural/command detail that duplicated `README.md`/`--help` was removed rather than kept as redundant context. |
| `jevloop/assets.py`, `jevloop/__main__.py`, `jevloop/serve.py`, `jevloop/simulate.py` | Cleaned | Removed genuinely unused imports/locals (`math`, `dataclasses.replace`, `os`, `json`, `ladder.Rung`, a vestigial `spec = hint` alias) surfaced once `ruff` could actually run in this environment for the first time. No behavior change; confirmed by the full test suite before/after. |
| `pyproject.toml` | Extended | Added an explicit `[tool.ruff.lint] select = ["E4","E7","E9","F"]`. Previously unset, so a `ruff check` run's effective rule set depended on whatever a given installed ruff version defaults to; ruff had also never actually been executed against this package before (blocked by dependency resolution in the build environment), so that drift was latent rather than observed. Pinning it makes lint scope reproducible. |
| `references/*`, `CHANGELOG.md`, `VALIDATION_REPORT.md`, `jevloop.__version__` | Updated | Document the above fixes with their upstream sources; version bumped to 0.3.1. |
| `tests/test_policy_risk_ladder_pricing.py`, `tests/test_alpaca_execution.py`, `tests/test_loop_reconciliation.py` | Extended | New regression coverage for the corrected A-S spread math, `done_for_day` non-terminality (adapter and loop level), `mark_order_terminal`'s own status check, foreign-session-order detection, and the bounded connect timeout. |
| remaining runtime/tests/assets | Re-inspected, retained | No further material contract drift identified against current provider/platform documentation. |
