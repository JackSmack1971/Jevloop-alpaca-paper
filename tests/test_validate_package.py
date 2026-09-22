from pathlib import Path
from subprocess import CompletedProcess

from scripts.validate_package import CI_WORKFLOW, validate_delivery_files


def _lock_result(returncode: int = 0, stderr: str = ""):
    def run(*args, **kwargs):
        return CompletedProcess(args[0], returncode, stdout="", stderr=stderr)

    return run


def test_missing_lockfile_is_reported_clearly(tmp_path: Path):
    (tmp_path / CI_WORKFLOW).parent.mkdir(parents=True)
    (tmp_path / CI_WORKFLOW).write_text("\n".join(()) + "\n", encoding="utf-8")
    errors = []

    validate_delivery_files(tmp_path, {CI_WORKFLOW}, errors, lock_check=_lock_result())

    assert "missing lockfile: uv.lock (run `uv lock` and commit the result)" in errors


def test_stale_lockfile_is_reported_clearly(tmp_path: Path):
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    errors = []

    validate_delivery_files(
        tmp_path,
        {"uv.lock"},
        errors,
        lock_check=_lock_result(1, "The lockfile needs to be updated"),
    )

    assert any(error.startswith("stale lockfile: uv.lock does not match pyproject.toml") for error in errors)


def test_missing_ci_workflow_is_reported_clearly(tmp_path: Path):
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    errors = []

    validate_delivery_files(tmp_path, {"uv.lock"}, errors, lock_check=_lock_result())

    assert errors == [f"missing CI workflow: {CI_WORKFLOW}"]
