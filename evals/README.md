# Skill evaluation protocol

These JSONL files are test inputs, not proof of skill quality by themselves.

For a meaningful Codex evaluation:

1. pin the Codex/model version, repository state, environment, credentials/capabilities, and task inputs;
2. run repeated paired trials **with** the skill and from the same starting state **without** the skill;
3. keep routing evaluation separate from task-success evaluation;
4. score mechanically observable outcomes where possible (commands, exit codes, diffs, broker-mode selection, forbidden side effects, evidence reporting);
5. include failure cases such as stale pricing data, ambiguous POST outcome, provider timeout, cancellation uncertainty, and live-trading requests;
6. report raw counts plus precision/recall for activation, task success, unsafe-side-effect rate, recovery rate, and token/latency cost;
7. inspect negative/no-uplift cases instead of averaging them away;
8. do not treat evaluator agreement or agent confidence as execution evidence.

The strongest safety assertions are also runtime-tested rather than left solely to routing behavior: live URL refusal, dry-by-default execution, broker-owned cancellation, ambiguous-order reconciliation, and stale-pricing-state handling (0.3.0), plus `done_for_day` non-terminality, `mark_order_terminal`'s own status check, bounded connect timeouts, and prior-session-order detection (0.3.1).

## Opt-in paired runner

`run.py` executes every case repeatedly in both conditions: an unchanged checkout
(`with_skill`) and the same Git commit with only `SKILL.md` removed
(`without_skill`). Each trial gets a fresh detached worktree, which is destroyed after
capture, so edits and generated files cannot reach a later trial. The deterministic
graders consume Codex JSON events, the final answer, Git diff, and Git status. Routing
success and task success remain separate in `report.json`; the report also contains
raw pass/total counts, unsafe-side-effect and recovery rates, latency, token totals,
and an explicit statement when confidence intervals are not justified.

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

`scripts/validate_package.py` only checks the configuration, case schema, and grader
contract statically. It does **not** invoke Codex, authenticate, execute trials, or
claim evaluation results. External evaluation is intentionally absent from the
ordinary offline test suite.
