"""Versioned, redacted evidence records shared by real and simulated runs."""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from . import __version__

SCHEMA_VERSION = 3
PROCESS_RUN_ID = uuid.uuid4().hex
PROCESS_STARTED_AT = time.time()
_ROOT = Path(__file__).resolve().parent.parent
_SECRET_KEYS = {"authorization", "apca-api-key-id", "apca-api-secret-key", "api_key", "secret_key", "token"}


def stable_digest(value: Any) -> str:
    """Return a stable SHA-256 digest for JSON-like/configuration input."""
    if is_dataclass(value):
        value = asdict(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT, check=True, capture_output=True, text=True
        ).stdout.strip() or "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


@dataclass(frozen=True)
class EvidenceContext:
    run_id: str
    process_started_at: float
    runtime_mode: str
    data_source: str
    symbol: str
    provider_route: str | None
    provider_model: str | None
    git_commit: str
    skill_digest: str
    battery_schema_digest: str
    strategy_config_digest: str
    limits_digest: str

    @classmethod
    def create(cls, *, runtime_mode: str, data_source: str, symbol: str, route: str | None,
               model: str | None, limits: Any) -> "EvidenceContext":
        return cls(
            PROCESS_RUN_ID, PROCESS_STARTED_AT, runtime_mode, data_source, symbol, route, model,
            git_commit(), file_digest(_ROOT / "SKILL.md"),
            file_digest(_ROOT / "jevloop" / "battery.py"),
            file_digest(_ROOT / "jevloop" / "strategy.py"), stable_digest(limits),
        )

    def fields(self) -> dict[str, Any]:
        result = asdict(self)
        result.update({"schema_version": SCHEMA_VERSION, "package_version": __version__})
        return result


def redact(value: Any) -> Any:
    """Recursively reject credential-shaped fields rather than trying to mask them."""
    if isinstance(value, dict):
        for key in value:
            if str(key).lower() in _SECRET_KEYS:
                raise ValueError(f"credential field forbidden in evidence: {key}")
        return {str(k): redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def serialize_record(record: dict[str, Any]) -> str:
    validate_record(record)
    return json.dumps(redact(record), sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_record(record: dict[str, Any]) -> None:
    required = {"schema_version", "package_version", "run_id", "runtime_mode", "data_source",
                "symbol", "git_commit", "process_started_at", "skill_digest",
                "battery_schema_digest", "strategy_config_digest", "limits_digest"}
    missing = required - record.keys()
    if record.get("schema_version") != SCHEMA_VERSION or missing:
        raise ValueError(f"invalid schema v3 evidence; missing={sorted(missing)}")
    if record.get("runtime_mode") not in {"dry-real", "paper-real", "simulation"}:
        raise ValueError("invalid runtime_mode")
    redact(record)


def classify_loaded_record(record: dict[str, Any]) -> dict[str, Any]:
    """Mark old rows as legacy; strict cohort consumers must exclude them."""
    if record.get("schema_version") != SCHEMA_VERSION:
        return {**record, "cohort_eligible": False,
                "cohort_exclusion_reason": f"legacy schema v{record.get('schema_version', 'unknown')}"}
    try:
        validate_record(record)
    except ValueError as exc:
        return {**record, "cohort_eligible": False, "cohort_exclusion_reason": str(exc)}
    return {**record, "cohort_eligible": True, "cohort_exclusion_reason": None}
