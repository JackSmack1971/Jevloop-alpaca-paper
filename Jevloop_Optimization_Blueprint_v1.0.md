# Jevloop Alpaca Paper — Optimization Blueprint v1.0

**Date:** 2026-09-22  
**Audited package:** `Jevloop-alpaca-paper-main.zip` / package version `0.3.2`  
**Objective:** improve the Codex skill and runtime by reducing uncertainty at consequential decision points, minimizing unnecessary probabilistic surface area, and establishing defensible evidence that the skill materially outperforms the no-skill baseline.

## 1. Executive assessment

The package is already structurally strong. The root `SKILL.md` is compact and router-like; branch-specific material is progressively disclosed; paper execution is an explicit authority boundary; live-money execution is technically absent; the Jev response is strictly typed; broker state is reconciled; unknown submission outcomes fail closed; and the package has a meaningful offline test/validation layer.

The next release should **not** add more general instruction prose. The largest remaining gains are below the prompt layer:

1. make the behavioral-evaluation harness actually test a discoverable Codex skill and the full declared corpus;
2. close paper-preflight authorization gaps around account capability and complete foreign-order enumeration;
3. make reconciliation uncertainty a persistent execution latch rather than a branch-local condition;
4. empirically justify every Jev question in the hot path and remove or defer questions that do not change control decisions;
5. move order/data freshness toward event-driven streams while preserving REST reconciliation as recovery authority;
6. strengthen evidence identity, calibration semantics, and paper-vs-economic-evidence boundaries;
7. replace brittle source-string validation with semantic/behavioral checks where possible.

**Current static result:** **Design Readiness 47/50 — UNVALIDATED**. G1–G4 pass on static inspection; G5 remains **UNVALIDATED** because the package contains eval scaffolding but no valid repeated no-skill baseline result.

## 2. Verified current strengths

- **Routing architecture:** focused description, explicit neighbors/non-goals, minimal root router, reference loading table, implicit invocation metadata.
- **Technical authority boundary:** dry by default; paper requires explicit `--paper`; canonical paper endpoint enforced; no live endpoint/override path.
- **Probabilistic boundary:** code computes deterministic state; Jev receives seven bounded typed questions; schema validation rejects malformed answers.
- **Execution safety:** session-owned client order IDs, ambiguous POST reconciliation by client order ID, no blind resubmit, cancellation verification before requote/flatten in core intention handling.
- **Risk separation:** deterministic hard risk is distinct from Jev judgments; fail-closed rungs include `HOLD_LATE`, `HOLD_BLOCKED`, `RULES_ONLY`, and `KILL`.
- **Evidence:** versioned evidence schema, config/code digests, provider request IDs, fsync on records, real-vs-simulation cohort separation.
- **Statistical restraint:** calibration output is descriptive only; time-series bootstrap logic and multiple-testing cautions are documented.
- **Offline verification:** `scripts/validate_package.py` passed; `pytest` independently passed **172/172**; Python compile verification passed in the audit environment. Locked dependency synchronization could not be independently repeated because the sandbox lacked package-network resolution; this is an environment limitation, not evidence of a repository defect.

## 3. Highest-value findings

| Priority | Finding | Why it matters | Required correction |
|---|---|---|---|
| P0 | The paired Codex runner does not install the skill in a recognized repository skill location; it only leaves/removes root `SKILL.md`. | “with_skill” is not proven to mean “skill loaded,” so baseline uplift would be uninterpretable. | Install/symlink the audited skill at `.agents/skills/jev-loop/` for the treatment arm only, or use another documented local-skill path. Verify activation with an observed runtime signal before scoring routing. |
| P0 | Full eval corpora exist (50 routing, 10 task, 5 failure), but `evals/run.py` executes only 7 `cases.jsonl` rows at 3 repetitions. | G5 cannot be established; failure corpus is not actually exercised and task repetitions are below the project standard. | Split routing and paired execution runners; run 50 routing prompts ×3 and 15 task/failure scenarios ×5 per treatment/baseline arm. |
| P0 | Eval semantics are internally stale/inconsistent. `T02` still requires “runs doctor first,” although current architecture makes canonical preflight part of `run --paper`; `live-boundary` expects no skill selection but still expects the skill’s live reference. | The harness can penalize correct current behavior or reward contradictory behavior. | Regenerate eval cases from current authority. Distinguish generic live-trading requests from requests to modify/audit jev-loop’s live boundary. |
| P0 | `no_live_endpoint_addition` regex matches `api.alpaca.markets` inside `paper-api.alpaca.markets`. | A safe paper-endpoint change can be falsely graded as a live-endpoint violation. | Parse added URL hosts and reject exact live hosts; add positive and negative grader regression tests. |
| P0 | Paper preflight checks `status` and `trading_blocked`, but not `crypto_status`, `trade_suspended_by_user`, or `account_blocked`. | Preflight can declare READY even though the account is not currently authorized for the requested asset class or user/account trading is suspended. | Build a typed asset-class-aware account capability check and fail closed on relevant blocked/suspended state. |
| P0 | `get_open_orders()` fetches only 100 rows and uses `self.symbol` directly. Current Alpaca docs allow 500/page plus order-ID pagination and specify crypto order-list symbols like `BTCUSD`. | Foreign Jev-loop orders can be missed, especially on larger order histories or crypto symbol-format mismatch, undermining the “no prior-session orders” authorization check. | Add complete paginated enumeration and explicit trading-vs-query symbol normalization; prove completeness before paper READY. |
| P1 | Market-closed and Alpaca-read-error branches call `_cancel_owned()` but discard cancellation verification errors. | The runtime can lose evidence that broker reconciliation is unresolved. | Persist cancellation result and set a reconciliation-required latch that blocks any later paper submission until broker state is re-proven clean. |
| P1 | Two hot-path Jev questions are currently non-decision-bearing: `regime` is logged but not used by policy/rung; `inventory_pressure` is not consumed by control logic. Direction is also inert unless its optional leg is enabled. | They add contract complexity and probabilistic surface without demonstrated action value. | Run an ablation study. Keep a question in the real-time battery only if it changes a justified control/evidence decision or demonstrates measurable predictive/calibration value. Move diagnostics to an optional battery otherwise. |
| P1 | Package validation relies partly on source-string presence checks. | Strings can remain while semantics regress, or refactors can fail validation despite preserving behavior. | Keep structural/package checks in the validator; move executable invariants to behavioral tests/AST checks. |
| P2 | Runtime relies primarily on polling for market/order state. | Alpaca recommends streaming for order state; streaming market data reduces state age and polling load. | Add WebSocket streams behind a reconnect/reconciliation state machine; REST remains bootstrap/recovery authority. |
| P2 | Evidence `run_id` is process-global rather than invocation-local. | Multiple runs in one interpreter can share an identity even when their execution contexts differ. | Generate an invocation ID per `EvidenceContext.create()` and retain a separate process ID if useful. Bump evidence schema deliberately. |
| P2 | Paper behavior can be misread as economic validation. | Alpaca paper simulation omits market impact, information leakage, latency slippage, and limit-order queue position. | Make “paper = integration evidence, not edge evidence” an explicit evaluation invariant; require separate executable-cost/queue-aware out-of-sample methodology for strategy claims. |

## 4. Target routing boundary

Use this as the design truth before editing the description or eval labels:

> **This skill exists to operate, inspect, calibrate, evaluate, or modify the jev-loop paper-trading harness when the task depends on its Jev decision contract, deterministic risk/execution boundaries, evidence model, or Alpaca paper integration.**

### Must activate

- jev-loop operation, simulation, doctor/preflight, calibration, dashboard, evidence inspection;
- changes to its decision battery, policy/risk gates, broker reconciliation, pricing, provider contracts, or paper execution path;
- audits or modification requests that touch the **absence of live-money capability** in this package.

### Must not activate

- general investing/trading advice;
- unrelated backtesting or market research;
- generic requests to operate some other live trading system;
- unrelated Alpaca SDK questions where no jev-loop behavior is implicated.

### Important boundary correction

“Turn on live trading in my brokerage account” should be a negative.  
“Modify jev-loop to add a live endpoint” should be a positive **maintenance** route so the package’s no-live invariant can be enforced. Activation is not authorization.

## 5. Optimization program

### Phase 0A — Make evaluation truthful

**Goal:** ensure every reported metric corresponds to the skill actually being evaluated.

Actions:

1. Install the treatment skill at `.agents/skills/jev-loop/SKILL.md` (with references/scripts intact) in the isolated worktree; do not install it in the baseline arm.
2. Empirically determine whether Codex JSON events expose skill activation. If a reliable signal exists, record it. If no reliable signal exists, do **not** infer activation merely from final prose; score behavioral routing separately and mark activation telemetry inconclusive.
3. Replace `cases.jsonl` as the sole corpus source with explicit routing/task/failure corpus loading.
4. Routing protocol: 20 positive + 20 negative + 10 neighboring prompts, 3 repetitions each.
5. Task/failure protocol: 10 tasks + 5 failures, 5 repetitions per condition, paired skill/no-skill from the same commit/state.
6. Fix stale `T02` and the live-boundary contradiction.
7. Replace the live-host regex grader with exact host parsing on added diff lines; add regression tests for both `paper-api.alpaca.markets` and `api.alpaca.markets`.
8. Add command/tool-call counts and successful-run efficiency ratios to existing token/time measurements.
9. Pre-register pass/fail rubrics before running the corpus.

**Completion evidence:** the runner proves the treatment skill is available in a documented discovery location; corpus counts meet the required protocol; grader unit tests cover known false-positive/false-negative cases; generated report includes routing precision/recall/neighbor rate, TSR uplift, relative error reduction, recovery uplift, critical violations, completion calibration, and efficiency.

### Phase 0B — Harden paper authorization

**Goal:** make `paper_preflight().ready` mean that all known broker-side prerequisites needed to authorize a paper write were actually established.

Actions:

1. Introduce a typed `AccountCapability`/`TradingAuthority` check.
2. For equities, require equities `status == ACTIVE`; for crypto, require `crypto_status == ACTIVE` in addition to account-wide prerequisites.
3. Reject `trading_blocked`, `account_blocked`, or `trade_suspended_by_user`.
4. Treat missing required capability fields as inconclusive/blocked rather than silently permissive.
5. Normalize order-list query symbols (`BTC/USD` execution symbol vs. Alpaca’s documented `BTCUSD` query representation).
6. Enumerate open orders completely: max page size, deterministic pagination by order ID, duplicate-loop protection, and explicit stop criteria.
7. Only declare foreign-session ownership clean after enumeration completes successfully.
8. Add tests for >500 orders, pagination repeats, malformed pages, crypto query symbols, blocked account flags, and asset-class status differences.

**Completion evidence:** property/fault tests show preflight cannot return READY when any relevant account restriction is active or when open-order enumeration is incomplete.

### Phase 1 — Reconciliation latch and execution state machine

**Goal:** make unresolved broker state a persistent authority state rather than a transient log message.

Introduce explicit runtime states such as:

`NO_AUTHORITY → PAPER_READY → PAPER_ACTIVE → RECONCILIATION_REQUIRED → HALTED`

Rules:

- any ambiguous submit, cancellation verification failure, unreadable broker state, or stream gap enters `RECONCILIATION_REQUIRED`;
- no new order can be submitted from that state;
- exit requires a fresh broker-authoritative read proving owned-order and position state;
- market-closed/read-error branches must record cancel result, not discard it;
- `KILL` may flatten only after owned-order cancellation is verified, preserving the existing invariant.

**Completion evidence:** state-transition tests plus injected failures demonstrate zero path from unresolved reconciliation to new order submission.

### Phase 2 — Reduce the Jev hot-path battery by evidence, not intuition

**Goal:** preserve only judgments that materially improve decisions.

Current decision use:

- `toxic_flow` → policy gate;
- `liquidity_stressed` → policy gate;
- `quote_environment` → policy + confidence/rung;
- `execution_health` → rung;
- `direction` → only when optional directional leg is enabled;
- `regime` → evidence/diagnostic only;
- `inventory_pressure` → currently unused by policy/rung.

Run a preregistered ablation using identical captured states:

- seven-question battery;
- control-only battery;
- control-only plus one candidate diagnostic at a time.

Measure: schema failure rate, provider latency p50/p95, decision stability, action agreement, safety-gate outcomes, token/request cost, calibration where labels exist, and downstream paper/replay metrics. Do not retain a question because it “sounds useful.”

If the battery changes, version the battery schema and evidence schema; do not silently mix old/new cohorts.

Also move hard execution-degradation limits (e.g. severe reject/slippage/latency conditions) into deterministic code first; use Jev only for borderline multivariate interpretation.

### Phase 3 — Event-driven market/order state with fail-closed recovery

**Goal:** reduce stale-state exposure and REST polling without increasing authority ambiguity.

1. Add Alpaca market-data WebSocket ingestion with provider timestamps and explicit freshness watermarks.
2. Add `trade_updates` for fills, partial fills, cancels, rejects, and suspended states.
3. REST snapshot at startup establishes baseline state.
4. Any disconnect/gap/re-auth failure enters `RECONCILIATION_REQUIRED`.
5. On reconnect, REST reconciliation must complete before paper writes resume.
6. Keep a polling fallback only if it satisfies the same freshness and ownership guarantees.

**Measure:** median/p95 quote age, order-state latency, REST request count, disconnect recovery rate, and duplicate/unknown-order outcomes. Alpaca explicitly recommends streaming for maintaining order state and states that stock streams provide better accuracy/performance than polling latest endpoints.

### Phase 4 — Evidence and statistical semantics

1. Separate `invocation_id` from `process_id`.
2. Add a structured startup/preflight record and terminal run-summary record with final authority/reconciliation state.
3. Persist decision/battery schema version, feed identity, asset class, and preflight contract version.
4. Use structured failure codes rather than depending on free-text tracebacks for analysis.
5. Add rolling/prequential calibration views for drift detection, but keep them descriptive until sufficient evidence exists.
6. Explicitly label Alpaca paper results as integration/behavior evidence, not economic-edge evidence.
7. Require temporal out-of-sample evaluation with realistic costs and conservative queue/fill assumptions before any strategy-performance claim.

### Phase 5 — Validator, CI, and package hygiene

1. Replace source-string “mechanism exists” assertions with behavior/AST checks where feasible.
2. Make the validator assert that eval corpus wiring and repetition counts satisfy the intended protocol.
3. Test every declared supported Python minor, or narrow `requires-python` to the versions actually maintained.
4. Add boundary/property tests for decimal quantization, non-finite values, timestamp edges, malformed provider output, pagination, and runtime state transitions.
5. Keep `jevloop-skill-audit.txt` out of the distributable root unless it is intentionally authoritative; it is historical audit context and can compete with current references. Move it under an archival docs path or omit it from the installable skill.
6. Add a final archive inspection that excludes caches, temp results, local paths, credentials, and stale generated artifacts.

## 6. Behavioral evaluation plan

### Primary value hypothesis

> The skill should materially reduce incorrect jev-loop operational/modification decisions and unsafe/inconclusive completion relative to the same Codex model without the skill, while keeping median successful-run execution overhead within 15% unless the added verification produces a demonstrated reliability gain.

### Required corpus

- Routing: 20 positive, 20 negative, 10 neighbor; 3 repeats each = **150 routing trials**.
- Tasks: 10 scenarios × 5 repeats × 2 conditions = **100 paired-condition trials**.
- Failures: 5 scenarios × 5 repeats × 2 conditions = **50 paired-condition trials**.
- Total default campaign: **300 Codex runs**.

### Acceptance gates

- routing precision ≥ 0.90;
- routing recall ≥ 0.90;
- neighbor false activation ≤ 0.15;
- zero critical safety/authority violations;
- task success material uplift: ≥10 pp absolute **or** ≥25% relative error reduction (or another preregistered material metric);
- failure recovery uplift target ≥15 pp if task-success is near ceiling;
- premature completion ≤5% (target ≤2%);
- unnecessary continuation ≤10% (target ≤5%);
- track at least tokens, tool/command count, and wall-clock time on successful runs.

Do not report G5 PASS from one successful run or from static inspection.

## 7. Recommended implementation order

1. **Eval harness validity and stale corpus/grader repair.** Without this, later “improvement” cannot be measured honestly.
2. **Preflight account capability + complete order pagination/symbol normalization.** These affect paper-write authorization.
3. **Persistent reconciliation latch/state machine.** This closes ambiguity after runtime failures.
4. **Battery ablation and contract versioning.** Reduce probabilistic surface only after measurement.
5. **Streaming market/order state with REST recovery.** Improve freshness/performance after authority semantics are solid.
6. **Evidence schema v4 + statistical/paper-evidence clarifications.** Make results easier to compare without overstating them.
7. **Validator/CI cleanup and final archive hygiene.** Convert the optimized design into a stable package.
8. **Run the complete 300-run behavioral campaign.** Only then score G5 and Validated Performance.

## 8. Static scorecard

| Gate | Current result | Evidence / caveat |
|---|---|---|
| G1 Routing boundary | PASS | Clear core workflow and neighbors, but live-maintenance vs generic-live routing needs one correction. |
| G2 Material decision value | PASS | Repository/runtime-specific authority, reconciliation, typed-provider, evidence, and validation rules materially alter decisions. |
| G3 Observable definition of done | PASS | Tests, validator, evidence records, canonical preflight, reconciliation, and explicit UNVERIFIED_RUNTIME status exist. |
| G4 Failure/autonomy controls | PASS | Paper effects require explicit authorization and live capability is absent; account/pagination/reconciliation gaps should still be closed. |
| G5 Empirical value | **UNVALIDATED** | External paired Codex evaluation not validly executed yet. |

### Design Readiness

| Dimension | Max | Current |
|---|---:|---:|
| Workflow ownership & routing | 8 | 7 |
| Decision-changing information | 8 | 8 |
| Loading & scope architecture | 5 | 5 |
| Decision envelope | 7 | 7 |
| Deterministic mechanisms | 5 | 5 |
| Evidence & completion | 7 | 6 |
| Failure handling & autonomy | 6 | 5 |
| Context efficiency | 4 | 4 |
| **Total** | **50** | **47** |

**Design Readiness: 47/50 — UNVALIDATED**  
**Validated Performance:** NOT YET TESTED  
**Overall /100:** not reportable until G5 has runtime evidence.

## 9. Definition of done for the optimization program

The optimized release is complete only when:

- treatment vs baseline actually differs by skill availability, not by an assumed root file;
- all routing/task/failure corpora are executable and internally consistent;
- no known grader false-positive remains for the paper endpoint;
- paper READY proves asset-class account authorization and complete foreign-order enumeration;
- unresolved reconciliation mechanically blocks subsequent paper submissions;
- every hot-path Jev question has a demonstrated control/evidence purpose or is removed/deferred;
- stream disconnect/recovery is fault-tested if streaming is introduced;
- evidence identity is invocation-safe and paper outcomes cannot be mistaken for economic validation;
- supported Python versions and package invariants are tested mechanically;
- final archive is clean;
- repeated paired trials demonstrate material skill value with zero critical violations.

## 10. Sources used for optimization decisions

- OpenAI Codex skills/customization guidance: https://developers.openai.com/docs/customization/overview and current local-skill documentation.
- OpenAI, *Harness engineering: leveraging Codex in an agent-first world* (2026-02-11): https://openai.com/index/harness-engineering/
- Alpaca, *Get All Orders*: https://docs.alpaca.markets/us/reference/getallorders-1
- Alpaca, *Account statuses / Account object*: https://docs.alpaca.markets/us/docs/accounts-statuses and https://docs.alpaca.markets/us/docs/account-plans
- Alpaca, *Placing Orders*: https://docs.alpaca.markets/us/docs/orders-at-alpaca
- Alpaca, *Websocket Streaming*: https://docs.alpaca.markets/us/docs/websocket-streaming
- Alpaca, *Real-time Stock Data*: https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data
- Alpaca, *Paper Trading*: https://docs.alpaca.markets/us/v1.4.2/docs/paper-trading
- Package’s own cited calibration/backtest literature in `references/research-notes.md`, including Murphy (1973, 1974), Nixon et al. (2019), Arian et al. (2024), Bailey & López de Prado, and Avellaneda & Stoikov (2008).
