#!/usr/bin/env python3
"""Deterministic static package guard for jev-loop."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "SKILL.md", "README.md", "CHANGELOG.md", "VALIDATION_REPORT.md", "pyproject.toml", "uv.lock", ".env.example",
    "jevloop/__main__.py", "jevloop/loop.py", "jevloop/evidence.py", "jevloop/execution/alpaca.py",
    "jevloop/calibrate.py", "jevloop/doctor.py", "jevloop/simulate.py",
    "references/provider-contracts.md", "references/architecture-and-safety.md",
    "references/evaluation-methodology.md", "references/live-trading.md",
    "references/research-notes.md", "references/source-audit.md", "agents/openai.yaml", "assets/dashboard/index.html",
    "scripts/validate_package.py", "evals/README.md", "evals/routing.jsonl", "evals/tasks.jsonl",
    "evals/failures.jsonl", "evals/config.json", "evals/cases.jsonl", "evals/run.py",
    "evals/graders.py",
}
FORBIDDEN_ARTIFACTS = {".env", ".DS_Store"}
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key)\s*=\s*['\"]?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
]


def fail(message: str, errors: list[str]) -> None:
    errors.append(message)


def main() -> int:
    errors: list[str] = []
    ignored_parts = {"__pycache__", ".pytest_cache", ".venv", ".ruff_cache"}
    files = [p for p in ROOT.rglob("*") if p.is_file() and not any(part in ignored_parts for part in p.parts)]
    rels = {p.relative_to(ROOT).as_posix() for p in files}

    missing = sorted(REQUIRED - rels)
    if missing:
        fail(f"missing required files: {missing}", errors)

    # Keep this package validator dependency-independent while rejecting missing,
    # malformed, or obviously partial lockfiles. CI/release validation should also
    # run `uv lock --check`, which is authoritative for pyproject/lock freshness.
    lock_path = ROOT / "uv.lock"
    if lock_path.exists():
        lock = lock_path.read_text(encoding="utf-8")
        if not lock.startswith('version = 1\nrevision = 2\nrequires-python = ">=3.10"\n'):
            fail("uv.lock header or Python compatibility is unexpected", errors)
        package_names = set(re.findall(r'^name = "([^"]+)"$', lock, re.MULTILINE))
        required_packages = {
            "jev-loop", "python-dotenv", "requests", "pytest", "ruff",
            "certifi", "charset-normalizer", "idna", "urllib3",
        }
        absent_packages = sorted(required_packages - package_names)
        if absent_packages:
            fail(f"uv.lock dependency graph is incomplete: {absent_packages}", errors)
        if 'source = { editable = "." }' not in lock:
            fail("uv.lock does not contain the editable project root", errors)
        if not re.search(r'^\s*\{ url = ".+", hash = "sha256:[0-9a-f]{64}"', lock, re.MULTILINE):
            fail("uv.lock does not contain hashed distribution artifacts", errors)

    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        if p.name in FORBIDDEN_ARTIFACTS or p.suffix == ".pyc":
            fail(f"forbidden generated/private artifact: {rel}", errors)
        if p.is_symlink():
            fail(f"symlink not allowed in skill package: {rel}", errors)
        if p.stat().st_size > 2_000_000:
            fail(f"unexpectedly large file: {rel}", errors)

    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    if not skill.startswith("---\nname: jev-loop\n"):
        fail("SKILL.md front matter/name malformed", errors)
    description_match = re.search(r"^description:\s*(.+)$", skill, re.MULTILINE)
    if not description_match:
        fail("SKILL.md missing description", errors)
    else:
        desc = description_match.group(1)
        for concept in ("Operate, inspect, calibrate, or modify", "paper", "live-money trading"):
            if concept.lower() not in desc.lower():
                fail(f"routing description missing boundary concept: {concept}", errors)
    if "Live-money execution is not a capability of this package" not in skill:
        fail("SKILL.md missing hard live-money capability boundary", errors)
    if "UNVERIFIED_RUNTIME" not in skill:
        fail("SKILL.md missing inconclusive runtime status", errors)

    openai_meta = (ROOT / "agents/openai.yaml").read_text(encoding="utf-8")
    if not re.search(r"^\s*allow_implicit_invocation:\s*true\s*$", openai_meta, re.MULTILINE):
        fail("agents/openai.yaml must allow implicit routing", errors)
    # Skill activation selects this workflow; it is not paper-effect authorization.
    # Keep each independent authorization gate visible in the package contract.
    authorization_gates = {
        "explicit user intent": "user's explicit intent",
        "paper CLI flag": "`--paper`",
        "canonical paper preflight": "ready canonical paper preflight",
        "environment policy": "environment policy",
    }
    for gate, marker in authorization_gates.items():
        if marker not in skill:
            fail(f"SKILL.md missing paper authorization gate: {gate}", errors)

    # Every branch-specific reference named by SKILL must exist.
    for ref in re.findall(r"`(references/[^`]+\.md)`", skill):
        if not (ROOT / ref).is_file():
            fail(f"SKILL.md references missing file: {ref}", errors)

    py_files = [p for p in files if p.suffix == ".py" and ".venv" not in p.parts]
    for p in py_files:
        rel = p.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError as exc:
            fail(f"syntax error in {rel}: {exc}", errors)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                if name == "cancel_all_orders":
                    fail(f"unsafe account-wide cancellation call in {rel}:{node.lineno}", errors)

    # Scan user-facing/package text for accidental real secrets or local-private paths.
    for p in files:
        if p.relative_to(ROOT).as_posix() == "scripts/validate_package.py":
            continue
        if p.suffix.lower() not in {".py", ".md", ".toml", ".html", ".jsonl", ".example", ""}:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if re.search(r"[A-Za-z]:\\Users\\[^\\\s]+", text) or re.search(r"/home/[^/$\s]+/", text):
            fail(f"accidental local user path in {rel}", errors)
        if p.name != ".env.example":
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    fail(f"possible embedded secret in {rel}", errors)
        if re.search(r"\b(TODO|FIXME|PLACEHOLDER)\b", text):
            fail(f"unfinished placeholder marker in {rel}", errors)

    # Evals are package contracts even when not executed in this environment.
    expected_counts = {"routing.jsonl": 50, "tasks.jsonl": 10, "failures.jsonl": 5}
    for filename, minimum in expected_counts.items():
        path = ROOT / "evals" / filename
        if not path.exists():
            continue
        rows = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                fail(f"invalid JSONL {filename}:{lineno}: {exc}", errors)
        if len(rows) < minimum:
            fail(f"{filename} has {len(rows)} rows; expected >= {minimum}", errors)
    routing = ROOT / "evals" / "routing.jsonl"
    if routing.exists():
        routing_rows = [json.loads(x) for x in routing.read_text(encoding="utf-8").splitlines() if x.strip()]
        labels = [row["expected"] for row in routing_rows]
        if labels.count("activate") < 20 or labels.count("do_not_activate") < 20 or labels.count("neighbor") < 10:
            fail("routing corpus does not meet 20/20/10 class minimums", errors)
        required_cases = {
            "implicit-positive": "activate",
            "explicit-positive": "activate",
            "general-investing-negative": "do_not_activate",
            "live-money-negative": "do_not_activate",
            "neighbor-task": "neighbor",
        }
        for case, expected in required_cases.items():
            if not any(row.get("case") == case and row.get("expected") == expected for row in routing_rows):
                fail(f"routing corpus missing {case} case labeled {expected}", errors)

    # Validate the external runner's declarative contract only. This intentionally
    # does not execute Codex or imply that an evaluation trial has run.
    eval_config_path = ROOT / "evals/config.json"
    eval_cases_path = ROOT / "evals/cases.jsonl"
    if eval_config_path.exists():
        try:
            config = json.loads(eval_config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"invalid eval runner config: {exc}", errors)
            config = {}
        if config.get("schema_version") != 1:
            fail("eval runner config schema_version must be 1", errors)
        if config.get("variants") != ["with_skill", "without_skill"]:
            fail("eval runner must define paired with_skill/without_skill variants", errors)
        if not isinstance(config.get("repetitions"), int) or config.get("repetitions", 0) < 2:
            fail("eval runner repetitions must be an integer >= 2", errors)
        if config.get("cases_file") != "cases.jsonl":
            fail("eval runner cases_file must be cases.jsonl", errors)
        for pin in ("codex_version", "model"):
            if not isinstance(config.get(pin), str) or not config.get(pin):
                fail(f"eval runner {pin} must be a nonempty pin or REQUIRED sentinel", errors)
    if eval_cases_path.exists():
        required_case_keys = {"id", "kind", "prompt", "expected_skill"}
        ids: set[str] = set()
        eval_cases = []
        for lineno, line in enumerate(eval_cases_path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                fail(f"invalid JSONL cases.jsonl:{lineno}: {exc}", errors)
                continue
            eval_cases.append(row)
            missing_keys = required_case_keys - row.keys()
            if missing_keys:
                fail(f"cases.jsonl:{lineno} missing keys: {sorted(missing_keys)}", errors)
            if row.get("id") in ids:
                fail(f"duplicate eval case id: {row.get('id')}", errors)
            ids.add(row.get("id"))
            if row.get("kind") not in {"routing", "task"}:
                fail(f"cases.jsonl:{lineno} kind must be routing or task", errors)
        required_flags = {
            "live_request", "requires_preflight", "mock_mode", "code_change", "recovery_case",
        }
        for flag in required_flags:
            if not any(row.get(flag) is True for row in eval_cases):
                fail(f"eval cases missing coverage flag: {flag}", errors)
        if not any(row.get("id") == "route-neighbor" and row.get("expected_skill") is False for row in eval_cases):
            fail("eval cases missing negative neighbor routing case", errors)

    graders_path = ROOT / "evals/graders.py"
    if graders_path.exists():
        grader_source = graders_path.read_text(encoding="utf-8")
        required_graders = {
            "skill_selection", "neighbor_rejection", "live_boundary_routing",
            "no_live_endpoint_addition", "read_only_side_effect_free",
            "mandatory_paper_preflight", "mock_never_submits", "reference_selection",
            "relevant_checks", "evidence_accuracy", "safe_recovery",
        }
        for grader in required_graders:
            if f'"{grader}"' not in grader_source:
                fail(f"deterministic grader missing: {grader}", errors)

    # The broker implementation must preserve the main safety invariants as source contracts.
    alpaca = (ROOT / "jevloop/execution/alpaca.py").read_text(encoding="utf-8")
    for required_text in ("SESSION_CLIENT_PREFIX", "cancel_session_orders", "UnknownOrderOutcome", "get_order_by_client_order_id", "only permits Alpaca paper trading"):
        if required_text not in alpaca:
            fail(f"Alpaca adapter missing safety mechanism: {required_text}", errors)
    if "--live" in alpaca or "LIVE_ALLOW_ENV" in alpaca:
        fail("Alpaca adapter unexpectedly contains a live enablement path", errors)
    loop = (ROOT / "jevloop/loop.py").read_text(encoding="utf-8")
    if 'parser.add_argument("--live"' in loop:
        fail("CLI unexpectedly exposes --live", errors)
    for required_text in ("mock judgments may not drive broker orders", "broker-authoritative state reconciliation", "flatten verified by broker", "HOLD_BLOCKED"):
        if required_text not in loop:
            fail(f"loop missing evidence/safety mechanism: {required_text}", errors)
    evidence = (ROOT / "jevloop/evidence.py").read_text(encoding="utf-8")
    for required_text in ("SCHEMA_VERSION = 3", "PROCESS_RUN_ID", "validate_record", "serialize_record", "cohort_exclusion_reason"):
        if required_text not in evidence:
            fail(f"schema v3 evidence contract missing: {required_text}", errors)

    if errors:
        print("PACKAGE_VALIDATION_FAILED")
        for item in errors:
            print(f"- {item}")
        return 1
    print(f"PACKAGE_VALIDATION_OK files={len(files)} python_files={len(py_files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
