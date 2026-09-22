#!/usr/bin/env python3
"""Run isolated Codex evaluation suites. External execution is opt-in."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from graders import grade

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
SKILL_RELATIVE_PATH = Path(".agents/skills/jev-loop")
SKILL_PARTS = ("SKILL.md", "references", "scripts", "assets")
CONDITIONS = ("with_skill", "without_skill")
SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret)([\"'=:\s]+)([^\s\",}]+)")
PRIVATE_PATH = re.compile(r"/(?:home|Users)/[^/\s]+")


def redact(value: Any) -> Any:
    """Recursively redact common secrets and user-home components."""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if re.search(r"(?i)key|token|password|secret", k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return PRIVATE_PATH.sub("/[PRIVATE_HOME]", SECRET.sub(r"\1\2[REDACTED]", value))
    return value


def command_output(args: list[str], cwd: Path = ROOT) -> str:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_suites(config: dict[str, Any], evals_dir: Path = EVALS) -> dict[str, list[dict[str, Any]]]:
    """Load and normalize the three independently versioned evaluation corpora."""
    suites: dict[str, list[dict[str, Any]]] = {}
    for suite, settings in config["suites"].items():
        rows = load_jsonl(evals_dir / settings["file"])
        normalized = []
        for index, row in enumerate(rows, 1):
            scenario = dict(row)
            scenario["id"] = row.get("id", f"R{index:03d}")
            scenario["suite"] = suite
            if suite == "routing":
                scenario["expected_skill"] = row["expected"] == "activate"
                scenario["read_only"] = True
            elif suite == "failure":
                scenario["prompt"] = (
                    "Explain and, only if safe and possible offline, verify jev-loop's response "
                    f"to this failure: {row['condition']}"
                )
                scenario["success"] = [row["safe_resolution"]]
                scenario["expected_skill"] = True
                scenario["recovery_case"] = True
                scenario["read_only"] = True
            else:
                scenario["expected_skill"] = True
            normalized.append(scenario)
        suites[suite] = normalized
    return suites


def build_manifest(config: dict[str, Any], evals_dir: Path = EVALS) -> list[dict[str, Any]]:
    manifest = []
    for suite, scenarios in load_suites(config, evals_dir).items():
        settings = config["suites"][suite]
        conditions = settings.get("conditions", CONDITIONS)
        for scenario in scenarios:
            for repetition in range(1, settings["repetitions"] + 1):
                for condition in conditions:
                    trial_id = f"{suite}--{scenario['id']}--r{repetition:02d}--{condition}"
                    manifest.append(
                        {
                            "trial_id": trial_id,
                            "suite": suite,
                            "scenario_id": scenario["id"],
                            "repetition": repetition,
                            "condition": condition,
                            "scenario": scenario,
                        }
                    )
    return manifest


def _skill_files(root: Path = ROOT) -> Iterable[Path]:
    for part in SKILL_PARTS:
        path = root / part
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(item for item in path.rglob("*") if item.is_file())


def skill_digest(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for path in _skill_files(root):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def install_skill(checkout: Path) -> Path:
    destination = checkout / SKILL_RELATIVE_PATH
    destination.mkdir(parents=True)
    for part in SKILL_PARTS:
        source = ROOT / part
        target = destination / part
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    return destination


def final_output(events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for event in events:
        if event.get("type") in {"item.completed", "message"}:
            item = event.get("item", event)
            if item.get("type") in {"agent_message", "message"}:
                parts.append(str(item.get("text", item.get("content", ""))))
    return "\n".join(parts)


def activation_telemetry(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Capture only explicit Codex activation events, never behavioral inference."""
    explicit_types = {"skill.activation", "skill.activated", "skill_activation", "skill_activated"}
    for event in events:
        item = event.get("item", {}) if isinstance(event.get("item"), dict) else {}
        if event.get("type") in explicit_types or item.get("type") in explicit_types:
            return {"status": "explicit", "event": event}
    return {"status": "inconclusive", "event": None}


def event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    command_types = {"command_execution", "command", "shell_command"}
    tool_types = command_types | {"mcp_tool_call", "tool_call", "function_call"}
    commands = tools = 0
    for event in events:
        item = event.get("item", event)
        item_type = item.get("type")
        commands += item_type in command_types
        tools += item_type in tool_types
    return {"commands": commands, "tool_calls": tools}


def environment_profile() -> dict[str, Any]:
    return {
        "os": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "network_policy": "runner does not probe",
        "sandbox": "workspace-write",
        "credentials_provided": False,
    }


def trial_status(checkout: Path) -> str:
    lines = command_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], checkout
    ).splitlines()
    prefix = f"?? {SKILL_RELATIVE_PATH.as_posix()}/"
    return "\n".join(line for line in lines if not line.startswith(prefix))


def _passed(record: dict[str, Any]) -> bool:
    values = [value for value in record["scores"].values() if value is not None]
    return record["exit_code"] == 0 and bool(values) and all(values)


def efficiency_ratios(records: list[dict[str, Any]]) -> dict[str, Any]:
    successful = {
        condition: [r for r in records if r["condition"] == condition and _passed(r)]
        for condition in CONDITIONS
    }
    result: dict[str, Any] = {"definition": "with_skill / without_skill mean among successful runs"}
    fields = {
        "tokens": lambda r: r.get("token_usage", {}).get("total_tokens"),
        "commands": lambda r: r["counts"]["commands"],
        "wall_clock": lambda r: r["elapsed_seconds"],
    }
    for name, getter in fields.items():
        means = {}
        for condition, rows in successful.items():
            values = [getter(row) for row in rows]
            values = [value for value in values if value is not None]
            means[condition] = sum(values) / len(values) if values else None
        denominator = means["without_skill"]
        result[name] = {
            "means": means,
            "ratio": means["with_skill"] / denominator
            if means["with_skill"] is not None and denominator
            else None,
        }
    return result


def summarize_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[bool]] = defaultdict(list)
    for record in records:
        for key, value in record["scores"].items():
            if value is not None:
                buckets[key].append(value)
    counts = {
        key: {"passed": sum(values), "total": len(values)}
        for key, values in sorted(buckets.items())
    }
    return {
        "successful_trials": {"passed": sum(_passed(r) for r in records), "total": len(records)},
        "grader_counts": counts,
        "unsafe_side_effect_rate": {
            "count": sum(not v for v in buckets.get("read_only_side_effect_free", [])),
            "total": len(buckets.get("read_only_side_effect_free", [])),
        },
        "recovery_rate": {
            "count": sum(buckets.get("safe_recovery", [])),
            "total": len(buckets.get("safe_recovery", [])),
        },
        "wall_clock_seconds": {
            "total": sum(r["elapsed_seconds"] for r in records),
            "mean": sum(r["elapsed_seconds"] for r in records) / len(records) if records else None,
        },
        "token_usage": {
            "total": sum(r.get("token_usage", {}).get("total_tokens", 0) for r in records),
            "trials_reporting": sum(bool(r.get("token_usage")) for r in records),
        },
        "commands": {"total": sum(r["counts"]["commands"] for r in records)},
        "tool_calls": {"total": sum(r["counts"]["tool_calls"] for r in records)},
        "activation_telemetry": {
            status: sum(r["activation_telemetry"]["status"] == status for r in records)
            for status in ("explicit", "inconclusive")
        },
        "confidence": "raw deterministic counts; no interval is asserted",
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "overall": summarize_group(records),
        "by_suite": {
            suite: summarize_group([r for r in records if r["suite"] == suite])
            for suite in ("routing", "task", "failure")
        },
        "by_condition": {
            condition: summarize_group([r for r in records if r["condition"] == condition])
            for condition in CONDITIONS
        },
        "successful_run_efficiency_ratios": efficiency_ratios(records),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=EVALS / "config.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--list", action="store_true", help="print the dry trial manifest without invoking Codex"
    )
    parser.add_argument(
        "--allow-external", action="store_true", help="acknowledge costly Codex API execution"
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    manifest = build_manifest(config, args.config.parent)
    if args.list:
        print(
            json.dumps(
                {
                    "schema_version": 2,
                    "planned_trials": len(manifest),
                    "trials": [
                        {k: v for k, v in row.items() if k != "scenario"} for row in manifest
                    ],
                },
                indent=2,
            )
        )
        return 0
    if not args.output:
        parser.error("--output is required unless --list is used")
    if not args.allow_external:
        parser.error("--allow-external is required; this runner may incur cost")
    if "REQUIRED" in (config["codex_version"], config["model"]):
        parser.error("pin codex_version and model in the configuration")
    actual_version = command_output(["codex", "--version"])
    if config["codex_version"] != actual_version:
        parser.error(
            f"Codex version mismatch: expected {config['codex_version']!r}, got {actual_version!r}"
        )
    commit = command_output(["git", "rev-parse", "HEAD"])
    if command_output(["git", "status", "--porcelain"]):
        parser.error("repository must be clean so every trial starts at the recorded commit")
    digest = skill_digest()
    raw_dir, redacted_dir = args.output / "raw", args.output / "redacted"
    raw_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    redacted_dir.mkdir()
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="jevloop-evals-") as temp:
        for planned in manifest:
            checkout = Path(temp) / planned["trial_id"]
            command_output(["git", "worktree", "add", "--detach", str(checkout), commit])
            try:
                installed_path = checkout / SKILL_RELATIVE_PATH
                if planned["condition"] == "with_skill":
                    install_skill(checkout)
                prompt = (
                    planned["scenario"]["prompt"]
                    + "\n\nReport commands actually run separately from unverified claims and name any repository reference used."
                )
                cmd = [
                    "codex",
                    "exec",
                    "--json",
                    "--model",
                    config["model"],
                    "--sandbox",
                    "workspace-write",
                    "--cd",
                    str(checkout),
                    prompt,
                ]
                started = time.monotonic()
                proc = subprocess.run(
                    cmd, text=True, capture_output=True, timeout=config["timeout_seconds"]
                )
                elapsed = time.monotonic() - started
                events = []
                for line in proc.stdout.splitlines():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        events.append({"type": "unparsed_stdout", "text": line})
                usage = next(
                    (event.get("usage", {}) for event in reversed(events) if event.get("usage")), {}
                )
                record = {
                    "schema_version": 2,
                    **{k: v for k, v in planned.items() if k != "scenario"},
                    "scenario": planned["scenario"],
                    "git_commit": commit,
                    "codex_version": actual_version,
                    "model": config["model"],
                    "skill": {
                        "path": SKILL_RELATIVE_PATH.as_posix(),
                        "digest": digest,
                        "installed": installed_path.is_dir(),
                    },
                    "activation_telemetry": activation_telemetry(events),
                    "environment": environment_profile(),
                    "elapsed_seconds": elapsed,
                    "token_usage": usage,
                    "counts": event_counts(events),
                    "exit_code": proc.returncode,
                    "events": events,
                    "stderr": proc.stderr,
                    "final_output": final_output(events),
                    "git_diff": command_output(
                        [
                            "git",
                            "diff",
                            "--no-ext-diff",
                            "--",
                            ".",
                            f":(exclude){SKILL_RELATIVE_PATH.as_posix()}",
                        ],
                        checkout,
                    ),
                    "git_status": trial_status(checkout),
                }
                record["scores"] = grade(record, planned["scenario"])
                records.append(record)
                (raw_dir / f"{planned['trial_id']}.json").write_text(
                    json.dumps(record, indent=2) + "\n", encoding="utf-8"
                )
                (redacted_dir / f"{planned['trial_id']}.json").write_text(
                    json.dumps(redact(record), indent=2) + "\n", encoding="utf-8"
                )
            finally:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(checkout)],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                )
    report = {
        "schema_version": 2,
        "git_commit": commit,
        "model": config["model"],
        "codex_version": actual_version,
        "skill_digest": digest,
        "planned_trials": len(manifest),
        "summary": summarize(records),
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
