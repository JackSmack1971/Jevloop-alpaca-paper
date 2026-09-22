from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_runner():
    evals = ROOT / "evals"
    sys.path.insert(0, str(evals))
    try:
        spec = importlib.util.spec_from_file_location("eval_runner", evals / "run.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(evals))


def test_manifest_has_exact_protocol_counts_and_unique_ids() -> None:
    runner = load_runner()
    config = json.loads((ROOT / "evals/config.json").read_text(encoding="utf-8"))
    manifest = runner.build_manifest(config)
    counts = {
        suite: sum(trial["suite"] == suite for trial in manifest)
        for suite in ("routing", "task", "failure")
    }
    assert counts == {"routing": 150, "task": 100, "failure": 50}
    assert len(manifest) == 300
    assert len({trial["trial_id"] for trial in manifest}) == 300
    assert {trial["condition"] for trial in manifest if trial["suite"] == "routing"} == {
        "with_skill"
    }


def test_skill_digest_and_install_cover_complete_skill(tmp_path: Path) -> None:
    runner = load_runner()
    installed = runner.install_skill(tmp_path)
    assert (installed / "SKILL.md").is_file()
    assert (installed / "references/evaluation-methodology.md").is_file()
    assert (installed / "scripts/validate_package.py").is_file()
    assert (installed / "assets/dashboard/index.html").is_file()
    assert runner.skill_digest().startswith("sha256:")


def test_activation_telemetry_requires_an_explicit_event() -> None:
    runner = load_runner()
    assert runner.activation_telemetry([])["status"] == "inconclusive"
    event = {"type": "skill.activated", "skill": "jev-loop"}
    assert runner.activation_telemetry([event]) == {"status": "explicit", "event": event}


def test_event_counts_distinguish_commands_and_other_tools() -> None:
    runner = load_runner()
    events = [
        {"type": "item.completed", "item": {"type": "command_execution"}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call"}},
    ]
    assert runner.event_counts(events) == {"commands": 1, "tool_calls": 2}
