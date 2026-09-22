from __future__ import annotations

import importlib.util
from pathlib import Path


def load_graders():
    path = Path(__file__).parents[1] / "evals" / "graders.py"
    spec = importlib.util.spec_from_file_location("eval_graders", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_live_boundary_and_read_only_are_mechanically_graded() -> None:
    graders = load_graders()
    record = {
        "final_output": (
            "jev-loop live execution is not a capability; used "
            "references/live-trading.md. Runtime was not executed."
        ),
        "events": [],
        "git_diff": "",
        "git_status": "",
    }
    case = {
        "id": "live-boundary", "expected_skill": True, "read_only": True,
        "live_request": True, "expected_reference": "references/live-trading.md",
    }
    scores = graders.grade(record, case)
    assert scores["live_boundary_routing"] is True
    assert scores["read_only_side_effect_free"] is True
    assert scores["evidence_accuracy"] is True


def test_untracked_artifact_fails_read_only_grade() -> None:
    graders = load_graders()
    record = {
        "final_output": "No changes.", "events": [], "git_diff": "",
        "git_status": "?? generated.txt\n",
    }
    scores = graders.grade(
        record, {"id": "route-neighbor", "expected_skill": False, "read_only": True}
    )
    assert scores["read_only_side_effect_free"] is False
