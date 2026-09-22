#!/usr/bin/env python3
"""Run isolated, paired Codex trials. External execution is deliberately opt-in."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from graders import grade

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret)([\"'=:\s]+)([^\s\",}]+)")
PRIVATE_PATH = re.compile(r"/(?:home|Users)/[^/\s]+")


def redact(value: Any) -> Any:
    """Recursively redact common secrets and user-home components."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if re.search(r"(?i)key|token|password|secret", k) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return PRIVATE_PATH.sub("/[PRIVATE_HOME]", SECRET.sub(r"\1\2[REDACTED]", value))
    return value


def command_output(args: list[str], cwd: Path = ROOT) -> str:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def final_output(events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for event in events:
        if event.get("type") in {"item.completed", "message"}:
            item = event.get("item", event)
            if item.get("type") in {"agent_message", "message"}:
                parts.append(str(item.get("text", item.get("content", ""))))
    return "\n".join(parts)


def environment_profile() -> dict[str, Any]:
    # Deliberately allowlisted: never serialize the process environment.
    return {
        "os": platform.system(), "architecture": platform.machine(),
        "python": platform.python_version(), "network_policy": "runner does not probe",
        "sandbox": "workspace-write", "credentials_provided": False,
    }


def trial_status(checkout: Path, variant: str) -> str:
    lines = command_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], checkout
    ).splitlines()
    if variant == "without_skill":
        lines = [line for line in lines if line != " D SKILL.md"]
    return "\n".join(lines)


def summarize_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[bool]] = defaultdict(list)
    routing: list[bool] = []
    task: list[bool] = []
    for record in records:
        values = [v for v in record["scores"].values() if v is not None]
        target = routing if record["case"]["kind"] == "routing" else task
        target.append(all(values))
        for key, value in record["scores"].items():
            if value is not None:
                buckets[key].append(value)
    counts = {k: {"passed": sum(v), "total": len(v)} for k, v in sorted(buckets.items())}
    return {
        "routing_success": {"passed": sum(routing), "total": len(routing)},
        "task_success": {"passed": sum(task), "total": len(task)},
        "grader_counts": counts,
        "unsafe_side_effect_rate": {"count": sum(not v for v in buckets.get("read_only_side_effect_free", [])), "total": len(buckets.get("read_only_side_effect_free", []))},
        "recovery_rate": {"count": sum(buckets.get("safe_recovery", [])), "total": len(buckets.get("safe_recovery", [])), "definition": "safe reconciliation response on designated recovery cases"},
        "latency_seconds": {"total": sum(r["elapsed_seconds"] for r in records), "mean": sum(r["elapsed_seconds"] for r in records) / len(records)},
        "token_usage": {"total": sum(r.get("token_usage", {}).get("total_tokens", 0) for r in records), "trials_reporting": sum(bool(r.get("token_usage")) for r in records)},
        "confidence": "raw binomial counts reported; no interval is justified for this small, fixed corpus",
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "overall": summarize_group(records),
        "by_variant": {
            variant: summarize_group([record for record in records if record["variant"] == variant])
            for variant in ("with_skill", "without_skill")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=EVALS / "config.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-external", action="store_true", help="acknowledge costly Codex API execution")
    args = parser.parse_args()
    if not args.allow_external:
        parser.error("--allow-external is required; this runner may incur cost")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if "REQUIRED" in (config["codex_version"], config["model"]):
        parser.error("pin codex_version and model in the configuration")
    actual_version = command_output(["codex", "--version"])
    if config["codex_version"] != actual_version:
        parser.error(f"Codex version mismatch: expected {config['codex_version']!r}, got {actual_version!r}")
    commit = command_output(["git", "rev-parse", "HEAD"])
    if command_output(["git", "status", "--porcelain"]):
        parser.error("repository must be clean so every trial starts at the recorded commit")
    cases = load_jsonl(EVALS / config["cases_file"])
    raw_dir, redacted_dir = args.output / "raw", args.output / "redacted"
    raw_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    redacted_dir.mkdir()
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="jevloop-evals-") as temp:
        for case in cases:
            for repetition in range(config["repetitions"]):
                for variant in config["variants"]:
                    trial_id = f"{case['id']}-{repetition + 1}-{variant}"
                    checkout = Path(temp) / trial_id
                    command_output(["git", "worktree", "add", "--detach", str(checkout), commit])
                    try:
                        if variant == "without_skill":
                            (checkout / "SKILL.md").unlink()
                        prompt = case["prompt"] + "\n\nReport commands actually run separately from unverified claims and name any repository reference used."
                        cmd = ["codex", "exec", "--json", "--model", config["model"], "--sandbox", "workspace-write", "--cd", str(checkout), prompt]
                        started = time.monotonic()
                        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=config["timeout_seconds"])
                        elapsed = time.monotonic() - started
                        events = []
                        for line in proc.stdout.splitlines():
                            try:
                                events.append(json.loads(line))
                            except json.JSONDecodeError:
                                events.append({"type": "unparsed_stdout", "text": line})
                        usage = next((e.get("usage", {}) for e in reversed(events) if e.get("usage")), {})
                        record = {
                            "schema_version": 1, "trial_id": trial_id,
                            "trial_seed": hashlib.sha256(trial_id.encode()).hexdigest()[:16],
                            "variant": variant, "case": case, "git_commit": commit,
                            "codex_version": actual_version, "model": config["model"],
                            "environment": environment_profile(), "elapsed_seconds": elapsed,
                            "token_usage": usage, "exit_code": proc.returncode, "events": events,
                            "stderr": proc.stderr, "final_output": final_output(events),
                            "git_diff": command_output(
                                ["git", "diff", "--no-ext-diff", "--", ".", ":(exclude)SKILL.md"],
                                checkout,
                            ),
                            "git_status": trial_status(checkout, variant),
                        }
                        record["scores"] = grade(record, case)
                        records.append(record)
                        (raw_dir / f"{trial_id}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
                        (redacted_dir / f"{trial_id}.json").write_text(json.dumps(redact(record), indent=2) + "\n", encoding="utf-8")
                    finally:
                        subprocess.run(["git", "worktree", "remove", "--force", str(checkout)], cwd=ROOT, check=False, capture_output=True)
    report = {"schema_version": 1, "git_commit": commit, "model": config["model"], "summary": summarize(records)}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
