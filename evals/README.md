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
