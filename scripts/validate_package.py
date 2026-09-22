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
    "SKILL.md", "README.md", "CHANGELOG.md", "VALIDATION_REPORT.md", "pyproject.toml", ".env.example",
    "jevloop/__main__.py", "jevloop/loop.py", "jevloop/execution/alpaca.py",
    "jevloop/calibrate.py", "jevloop/doctor.py", "jevloop/simulate.py",
    "references/provider-contracts.md", "references/architecture-and-safety.md",
    "references/evaluation-methodology.md", "references/live-trading.md",
    "references/research-notes.md", "references/source-audit.md", "agents/openai.yaml", "assets/dashboard/index.html",
    "scripts/validate_package.py", "evals/README.md", "evals/routing.jsonl", "evals/tasks.jsonl",
    "evals/failures.jsonl",
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
    if "allow_implicit_invocation: false" not in openai_meta:
        fail("agents/openai.yaml must require explicit invocation for operational side effects", errors)

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
        labels = [json.loads(x)["expected"] for x in routing.read_text(encoding="utf-8").splitlines() if x.strip()]
        if labels.count("activate") < 20 or labels.count("do_not_activate") < 20 or labels.count("neighbor") < 10:
            fail("routing corpus does not meet 20/20/10 class minimums", errors)

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
    for required_text in ("mock judgments may not drive broker orders", "broker-reconciled inventory", "flatten verified by broker", "HOLD_BLOCKED"):
        if required_text not in loop:
            fail(f"loop missing evidence/safety mechanism: {required_text}", errors)

    if errors:
        print("PACKAGE_VALIDATION_FAILED")
        for item in errors:
            print(f"- {item}")
        return 1
    print(f"PACKAGE_VALIDATION_OK files={len(files)} python_files={len(py_files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
