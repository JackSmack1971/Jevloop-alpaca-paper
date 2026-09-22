# jev-loop 0.3.2 validation report

**Date:** 2026-09-22
**Scope:** the current 0.3.2 working tree after the canonical-preflight, provider-schema,
abnormal-exit, evidence-v3, calibration-isolation, deadline, routing-policy, paired-eval,
pure-reducer, lockfile, and CI changes. This report supersedes historical validation
counts; in particular, it does not reuse the earlier 88-test result as evidence for this
tree.

## Environment

- Linux artifact container; CPython 3.14.4 selected by uv.
- uv resolved 17 packages and installed/audited the 13-package development environment.
- The committed lock declares the project's supported `requires-python = ">=3.10"` range.
- GitHub Actions separately defines the locked CI contract for Python 3.10, but hosted CI
  was not invoked from this local validation run.
- No Alpaca, TypeSafe, Vercel, or Codex credentials were supplied. Authenticated provider,
  broker, and market-data behavior therefore remains **UNVERIFIED_RUNTIME**.

## Exact verification record

The lock was first refreshed for the 0.3.2 project metadata:

| Exact command | Actual result |
|---|---|
| `uv lock` | **PASS** (exit 0): resolved 17 packages; updated the editable root package from 0.3.1 to 0.3.2 in `uv.lock`. |

The full locked verification sequence was then executed in the order committed to the
README and CI workflow:

| Exact command | Actual result |
|---|---|
| `uv sync --locked --dev` | **PASS** (exit 0): resolved 17 packages and installed 13 packages in a newly created `.venv`. |
| `uv lock --check` | **PASS** (exit 0): resolved 17 packages; no stale-lock error. |
| `uv run python scripts/validate_package.py` | **PASS** (exit 0): `PACKAGE_VALIDATION_OK files=158 python_files=39`. |
| `uv run python -m pytest -q` | **PASS** (exit 0): **172 passed, 0 failed** in 0.62 seconds. |
| `uv run ruff check .` | **PASS** (exit 0): `All checks passed!` (0 reported violations). |
| `uv run python -m compileall -q jevloop scripts evals tests` | **PASS** (exit 0): quiet completion, 0 reported compilation errors. |

**Sequence total:** 6 passed, 0 failed. The preparatory `uv lock` also passed.

The package validator now treats the committed lockfile, CI workflow, pure reducer,
paired-evaluation configuration/cases/runner/graders, schema-v3 evidence implementation,
and the existing release documentation as required artifacts. It additionally verifies
that the project and runtime package versions match and that the current version has a
changelog section.

## External paired Codex evaluation

**NOT EXECUTED.** `python evals/run.py ... --allow-external` was not run. The checked-in
configuration intentionally retains `REQUIRED` sentinels for the Codex CLI and model,
and this environment did not provide an approved pinned configuration, authenticated
external runner, or authorization to incur evaluation cost. The ordinary package
validation checked only the runner's static configuration, case schema, coverage flags,
and deterministic-grader contract. Therefore this report makes **no measured claim**
about with-skill versus without-skill uplift.

## Evidence boundary

This run verifies the deterministic offline package, test, lint, compilation, lockfile,
and release-artifact contracts on the environment above. It does not validate hosted CI
execution, Python 3.10 runtime behavior in this container, authenticated external
providers, an Alpaca paper account, real orders/fills/cancellations, subscription-specific
feeds, network latency/rate limits, skill uplift, economic edge, or live-money suitability.
