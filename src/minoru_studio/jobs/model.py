from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4


SCHEMA_VERSION = 1
class ManifestError(ValueError):
    pass


class JobMode(StrEnum):
    BEAT_SYNC = "beat-sync"
    TRANSCRIBE = "transcribe"
    NARRATE = "narrate"
    SCRIPT_DRAFT = "script-draft"


_STEP_ORDERS = {
    JobMode.NARRATE: (
        "probe-input",
        "parse-script",
        "synthesize-utterances",
        "concat-audio",
        "render-artifacts",
        "render-preview",
    ),
    JobMode.TRANSCRIBE: (
        "probe-input",
        "extract-audio",
        "transcribe",
        "render-artifacts",
        "render-preview",
    ),
    JobMode.SCRIPT_DRAFT: (
        "probe-input",
        "extract-scene-frames",
        "extract-interval-frames",
        "render-draft",
    ),
}


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(slots=True)
class InputRef:
    path: str
    size: int
    mtime_ns: int
    sha256: str


@dataclass(slots=True)
class StepRecord:
    status: StepStatus = StepStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    error: str | None = None


@dataclass(slots=True)
class ArtifactRecord:
    kind: str
    path: str
    size: int
    sha256: str


@dataclass(slots=True)
class JobManifest:
    schema_version: int
    job_id: str
    name: str
    mode: JobMode
    status: JobStatus
    created_at: str
    updated_at: str
    inputs: list[InputRef] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    steps: dict[str, StepRecord] = field(default_factory=dict)
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    last_error: str | None = None
    resolve_applications: list[dict[str, Any]] = field(default_factory=list)
    tools: dict[str, str] = field(default_factory=dict)


def _utc_text(now: datetime | None = None) -> str:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def new_manifest(
    name: str,
    mode: JobMode,
    now: datetime | None = None,
) -> JobManifest:
    timestamp = _utc_text(now)
    return JobManifest(
        schema_version=SCHEMA_VERSION,
        job_id=str(uuid4()),
        name=name,
        mode=mode,
        status=JobStatus.PENDING,
        created_at=timestamp,
        updated_at=timestamp,
    )


def manifest_to_dict(manifest: JobManifest) -> dict[str, Any]:
    data = asdict(manifest)
    data["mode"] = manifest.mode.value
    data["status"] = manifest.status.value
    data["steps"] = {
        name: {**asdict(step), "status": step.status.value}
        for name, step in manifest.steps.items()
    }
    return data


def manifest_from_dict(data: Mapping[str, Any]) -> JobManifest:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(
            f"unsupported schema_version: {data.get('schema_version')}"
        )
    try:
        mode = JobMode(data["mode"])
        steps = data.get("steps", {})
        if not isinstance(steps, Mapping):
            raise TypeError("steps must be a mapping")
        return JobManifest(
            schema_version=SCHEMA_VERSION,
            job_id=str(data["job_id"]),
            name=str(data["name"]),
            mode=mode,
            status=JobStatus(data["status"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            inputs=[InputRef(**item) for item in data.get("inputs", [])],
            settings=dict(data.get("settings", {})),
            steps={
                name: StepRecord(
                    **{**step, "status": StepStatus(step["status"])}
                )
                for name, step in _ordered_steps(steps, mode)
            },
            artifacts=[
                ArtifactRecord(**item) for item in data.get("artifacts", [])
            ],
            last_error=data.get("last_error"),
            resolve_applications=list(data.get("resolve_applications", [])),
            tools=dict(data.get("tools", {})),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestError(f"invalid manifest: {exc}") from exc


def _ordered_steps(
    steps: Mapping[str, Any], mode: JobMode,
) -> list[tuple[str, Any]]:
    known = [name for name in _STEP_ORDERS.get(mode, ()) if name in steps]
    remaining = [name for name in steps if name not in known]
    return [(name, steps[name]) for name in (*known, *remaining)]
