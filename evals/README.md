# Skill evaluation protocol

These JSONL files are test inputs, not proof of skill quality by themselves.

For a meaningful Codex evaluation:

1. pin the Codex/model version, repository state, environment, credentials/capabilities, and task inputs;
2. run routing prompts three times with the installed skill, and run task/failure
   scenarios five times in paired **with skill** and **without skill** conditions;
3. keep routing evaluation separate from task-success evaluation;
4. score mechanically observable outcomes where possible (commands, exit codes, diffs, broker-mode selection, forbidden side effects, evidence reporting);
5. include failure cases such as stale pricing data, ambiguous POST outcome, provider timeout, cancellation uncertainty, and live-trading requests;
6. keep explicit runtime activation telemetry separate from behavioral routing, and
   report command/tool-call counts plus successful-run token, command, and wall-clock ratios;
7. inspect negative/no-uplift cases instead of averaging them away;
8. do not treat evaluator agreement or agent confidence as execution evidence.

The strongest safety assertions are also runtime-tested rather than left solely to routing behavior: live URL refusal, dry-by-default execution, broker-owned cancellation, ambiguous-order reconciliation, and stale-pricing-state handling (0.3.0), plus `done_for_day` non-terminality, `mark_order_terminal`'s own status check, bounded connect timeouts, and prior-session-order detection (0.3.1).

## Opt-in paired runner

`run.py` loads `routing.jsonl`, `tasks.jsonl`, and `failures.jsonl` as distinct suites.
The protocol is 50 routing scenarios × 3 repetitions in the treatment condition,
10 tasks × 5 repetitions × 2 conditions, and 5 failures × 5 repetitions × 2
conditions: exactly **300 planned trials**. Routing is treatment-only because it
measures whether the installed skill routes correctly; task and failure uplift is
paired against a baseline from the same commit.

Each trial gets a fresh detached worktree. Treatment worktrees receive the complete
skill at `.agents/skills/jev-loop/` (`SKILL.md`, `references/`, `scripts/`, and
`assets/`); that path is absent in baseline worktrees. Every record carries suite,
scenario, repetition, condition, commit, model, Codex version, installed path, and a
content digest. An explicit Codex skill-activation event is retained when present;
otherwise activation telemetry is `inconclusive`. Reference use remains a separately
named behavioral-routing score and is never promoted to activation evidence.

Before any external run, inspect the deterministic manifest (this neither requires a
pinned Codex binary nor invokes Codex):

```bash
python evals/run.py --list
```

It must report `planned_trials: 300`, split as 150 routing, 100 task, and 50 failure
trials. Trial summaries include raw grader counts, unsafe-side-effect and recovery
rates, command/tool-call totals, token and wall-clock totals, and with-skill /
without-skill successful-run efficiency ratios. A missing baseline (as in routing)
produces a null ratio rather than a fabricated comparison.

Prerequisites:

- a clean Git checkout whose `HEAD` contains the runner and cases;
- an authenticated `codex` executable, with a deliberately pinned CLI version and
  model written into a copy of `config.json` (the checked-in `REQUIRED` values prevent
  accidental, unpinned spend);
- enough API quota, disk space for isolated worktrees, and permission to use the
  external model. No broker credentials are required or passed.

This is the explicit, potentially costly command (choose an output directory that
does not already exist):

```bash
python evals/run.py --config /path/to/pinned-config.json \
  --output evals/results/run-YYYYMMDD --allow-external
```

The command writes full audit records under `raw/` with owner-only directory
permissions and separately writes the result of an explicit recursive redaction pass
under `redacted/`. Raw records can contain model output or command output and must be
handled as sensitive local audit data; publish only reviewed redacted records. The
environment profile is allowlisted rather than copied from the process environment.
Both result forms are ignored by Git.

`scripts/validate_package.py` mechanically checks all three corpus sizes, canonical
suite repetitions/conditions, the exact 150/100/50 manifest arithmetic, and the
grader contract. It does **not** invoke Codex, authenticate, execute trials, or claim
evaluation results. External evaluation is intentionally absent from the ordinary
offline test suite.
