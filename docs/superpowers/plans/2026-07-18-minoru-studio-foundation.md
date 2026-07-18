# MinoruStudio Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Windows-only MinoruStudio foundation: an isolated Python 3.12 package, deterministic time conversion, safe job persistence and resume, redacted process logging, environment diagnostics, job-management CLI, and a minimal launcher GUI.

**Architecture:** Add a new `src/minoru_studio/` package beside the untouched MinoruDouga implementation. The package owns job manifests and local tooling; a PowerShell shim invokes it through `uv`. Phase 1 creates and inspects pending fixed-mode jobs but performs no media processing and makes no Resolve, VOICEVOX, Whisper, Codex, or cloud calls.

**Tech Stack:** Windows PowerShell 7, Python 3.12, Python standard library at runtime, `uv`, Hatchling, pytest 8.x, JSON job manifests, tkinter.

## Global Constraints

- Target Windows only.
- Require Python `>=3.12,<3.13`.
- Keep runtime dependencies empty in Phase 1; use only the Python standard library.
- Add `pytest>=8.3,<9` as the only development dependency.
- Keep `src/minoru_douga.py`, `src/analyze_beats.py`, `scripts/MinoruDouga.py`, `install.ps1`, and `requirements.txt` unchanged.
- Do not install into Resolve's Python environment or the system Python environment.
- Do not call cloud APIs, Codex, VOICEVOX, faster-whisper, FFmpeg media operations, or Resolve APIs in Phase 1.
- Never overwrite source media or an existing `.media-job` directory.
- Store shared timestamps as non-negative integer milliseconds.
- Use atomic JSON replacement and an exclusive per-job lock for every manifest mutation.
- Redact known secret values and secret-shaped command-line arguments before logging.
- A no-argument `minoru-studio` invocation opens the tkinter launcher; explicit subcommands remain non-interactive.
- The implementation worktree must contain no unrelated staged or unstaged changes before each commit.

## Scope and Audit Points

**Allowed implementation files:**

- Create: `pyproject.toml`
- Create: `src/minoru_studio/**`
- Create: `tests/**`
- Create: `scripts/minoru-studio.ps1`
- Modify: `README.md`
- Create: `uv.lock` only through `uv lock` or `uv sync`

**Protected files:** the existing MinoruDouga source, launcher, installer, and requirements files listed in Global Constraints.

**Acceptance criteria:**

- `uv run minoru-studio --version` prints `0.1.0` and exits 0.
- `uv run minoru-studio doctor --json` emits valid JSON and exits 0 when all required tools are available, otherwise exits 2 with one result per tool.
- `uv run minoru-studio jobs create` creates a uniquely named, valid, pending `.media-job` without overwriting an existing directory.
- `uv run minoru-studio jobs inspect` prints the manifest without mutating it.
- Interrupted `running` states become `interrupted` only when no live lock owns the job.
- Secret values do not appear in process display strings or `logs/run.log`.
- Millisecond/frame conversions pass for 24, 30, 60, 23.976, and 29.97 fps.
- `uv run pytest -q` exits 0 with no failed tests.
- `git diff --check` exits 0.
- Only the allowed implementation files change.

**Expected mechanical gates:** unit tests have zero failures; CLI smoke commands return the documented exit codes; PowerShell shim prints `0.1.0`; a manual GUI smoke test opens one launcher window and creates or inspects a pending job.

**Residual risks:** tkinter availability varies with the Python distribution; Windows process-liveness checks can be affected by PID reuse; `doctor` results depend on the local PATH. These do not justify expanding into installers or media processing during this phase.

**Final independent audit:** not required for this foundation-only phase unless the final diff touches a protected file, adds a runtime dependency, or expands into Resolve, media processing, authentication, or external network access. If scope expands, stop and obtain a revised design and audit checklist before continuing.

---

## File Map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Python version, package metadata, console entry point, dev dependency, pytest settings |
| `src/minoru_studio/__init__.py` | Package version |
| `src/minoru_studio/__main__.py` | `python -m minoru_studio` entry point |
| `src/minoru_studio/cli.py` | Argument parsing and non-interactive command dispatch |
| `src/minoru_studio/timebase.py` | Rational frame rates and millisecond/frame conversion |
| `src/minoru_studio/jobs/model.py` | Manifest enums, records, serialization, schema validation |
| `src/minoru_studio/jobs/store.py` | Job naming, fingerprints, atomic persistence, resume recovery |
| `src/minoru_studio/jobs/lock.py` | Exclusive lock acquisition, stale-lock recovery, token-safe release |
| `src/minoru_studio/redaction.py` | Secret-value and secret-shaped text masking |
| `src/minoru_studio/processes.py` | Shell-free external process execution with safe display text |
| `src/minoru_studio/logging_utils.py` | Console and per-job file logging with redaction filter |
| `src/minoru_studio/doctor.py` | Required-tool checks and human/JSON reports |
| `src/minoru_studio/gui.py` | tkinter launcher and testable job-controller boundary |
| `scripts/minoru-studio.ps1` | Development PowerShell shim that delegates to `uv run` |
| `tests/**` | Focused tests matching the modules above |
| `README.md` | Phase 1 usage and explicit non-goals |

---

### Task 1: Bootstrap the Python Package and CLI

**Files:**
- Create: `pyproject.toml`
- Create: `src/minoru_studio/__init__.py`
- Create: `src/minoru_studio/__main__.py`
- Create: `src/minoru_studio/cli.py`
- Create: `scripts/minoru-studio.ps1`
- Create: `tests/test_cli.py`

**Interfaces:**
- Produces: `minoru_studio.__version__: str`
- Produces: `minoru_studio.cli.build_parser() -> argparse.ArgumentParser`
- Produces: `minoru_studio.cli.main(argv: Sequence[str] | None = None) -> int`
- Consumes: no project-local Python interfaces

- [ ] **Step 1: Add the package metadata and failing CLI tests**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "minoru-studio"
version = "0.1.0"
description = "Windows-local video production automation for DaVinci Resolve"
readme = "README.md"
requires-python = ">=3.12,<3.13"
dependencies = []

[project.scripts]
minoru-studio = "minoru_studio.cli:main"

[dependency-groups]
dev = ["pytest>=8.3,<9"]

[tool.hatch.build.targets.wheel]
packages = ["src/minoru_studio"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

Create `tests/test_cli.py`:

```python
import pytest

from minoru_studio.cli import main


def test_version_prints_package_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_empty_argv_prints_help_before_gui_is_added(capsys):
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "usage:" in output
    assert "MinoruStudio" in output
```

- [ ] **Step 2: Run the focused tests and verify the import failure**

Run:

```powershell
uv run --with pytest pytest tests/test_cli.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'minoru_studio'`.

- [ ] **Step 3: Implement the package and CLI entry points**

Create `src/minoru_studio/__init__.py`:

```python
"""MinoruStudio package."""

__version__ = "0.1.0"
```

Create `src/minoru_studio/__main__.py`:

```python
from minoru_studio.cli import main


raise SystemExit(main())
```

Create `src/minoru_studio/cli.py`:

```python
from __future__ import annotations

import argparse
from collections.abc import Sequence

from minoru_studio import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minoru-studio",
        description="MinoruStudio local video-production tools",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_subparsers(dest="command")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))
```

Create `scripts/minoru-studio.ps1`:

```powershell
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $CommandArgs
)

$ErrorActionPreference = "Stop"
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    [Console]::Error.WriteLine(
        "uv が見つかりません。MinoruStudioの専用Python環境を起動できません。"
    )
    exit 127
}

$projectRoot = Split-Path -Parent $PSScriptRoot
& $uvCommand.Source run --project $projectRoot minoru-studio @CommandArgs
exit $LASTEXITCODE
```

- [ ] **Step 4: Run package and PowerShell smoke tests**

Run:

```powershell
uv run pytest tests/test_cli.py -q
uv run minoru-studio --version
pwsh -NoProfile -File .\scripts\minoru-studio.ps1 --version
```

Expected: both tests pass; both version commands print `0.1.0` and exit 0.

- [ ] **Step 5: Lock dependencies and commit**

Run:

```powershell
uv lock
git add pyproject.toml uv.lock src/minoru_studio/__init__.py src/minoru_studio/__main__.py src/minoru_studio/cli.py scripts/minoru-studio.ps1 tests/test_cli.py
git commit -m "build: bootstrap MinoruStudio package"
```

Expected: one commit containing only the package bootstrap, shim, and focused tests.

---

### Task 2: Add Rational Timebase Conversion

**Files:**
- Create: `src/minoru_studio/timebase.py`
- Create: `tests/test_timebase.py`

**Interfaces:**
- Produces: `FrameRate(numerator: int, denominator: int = 1)`
- Produces: `parse_frame_rate(value: str) -> FrameRate`
- Produces: `milliseconds_to_frame(time_ms: int, rate: FrameRate) -> int`
- Produces: `frame_to_milliseconds(frame: int, rate: FrameRate) -> int`
- Consumes: non-negative integer millisecond values from future job manifests

- [ ] **Step 1: Write failing tests for integer and NTSC rates**

Create `tests/test_timebase.py`:

```python
import pytest

from minoru_studio.timebase import (
    FrameRate,
    frame_to_milliseconds,
    milliseconds_to_frame,
    parse_frame_rate,
)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("24", FrameRate(24, 1)),
        ("30", FrameRate(30, 1)),
        ("60", FrameRate(60, 1)),
        ("23.976", FrameRate(24000, 1001)),
        ("29.97", FrameRate(30000, 1001)),
    ],
)
def test_parse_frame_rate(label, expected):
    assert parse_frame_rate(label) == expected


@pytest.mark.parametrize(
    ("time_ms", "rate", "expected_frame"),
    [
        (0, FrameRate(24), 0),
        (502, FrameRate(30), 15),
        (1_000, FrameRate(60), 60),
        (1_000, FrameRate(24000, 1001), 24),
        (1_000, FrameRate(30000, 1001), 30),
    ],
)
def test_milliseconds_to_frame(time_ms, rate, expected_frame):
    assert milliseconds_to_frame(time_ms, rate) == expected_frame


def test_frame_round_trip_is_within_half_frame():
    rate = FrameRate(30000, 1001)
    source_ms = 18_342
    frame = milliseconds_to_frame(source_ms, rate)
    restored_ms = frame_to_milliseconds(frame, rate)
    assert abs(restored_ms - source_ms) <= 17


@pytest.mark.parametrize("value", ["0", "-24", "abc", "23.98"])
def test_unsupported_frame_rate_is_rejected(value):
    with pytest.raises(ValueError):
        parse_frame_rate(value)


def test_negative_time_is_rejected():
    with pytest.raises(ValueError):
        milliseconds_to_frame(-1, FrameRate(24))
```

- [ ] **Step 2: Run the focused tests and verify the missing-module failure**

Run:

```powershell
uv run pytest tests/test_timebase.py -q
```

Expected: collection fails because `minoru_studio.timebase` does not exist.

- [ ] **Step 3: Implement exact rational conversion**

Create `src/minoru_studio/timebase.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FrameRate:
    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if self.numerator <= 0 or self.denominator <= 0:
            raise ValueError("frame-rate numerator and denominator must be positive")


_KNOWN_RATES = {
    "23.976": FrameRate(24000, 1001),
    "24": FrameRate(24, 1),
    "25": FrameRate(25, 1),
    "29.97": FrameRate(30000, 1001),
    "30": FrameRate(30, 1),
    "50": FrameRate(50, 1),
    "59.94": FrameRate(60000, 1001),
    "60": FrameRate(60, 1),
}


def parse_frame_rate(value: str) -> FrameRate:
    try:
        return _KNOWN_RATES[value.strip()]
    except KeyError as exc:
        raise ValueError(f"unsupported frame rate: {value}") from exc


def _round_nonnegative(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def milliseconds_to_frame(time_ms: int, rate: FrameRate) -> int:
    if time_ms < 0:
        raise ValueError("time_ms must be non-negative")
    return _round_nonnegative(
        time_ms * rate.numerator,
        1000 * rate.denominator,
    )


def frame_to_milliseconds(frame: int, rate: FrameRate) -> int:
    if frame < 0:
        raise ValueError("frame must be non-negative")
    return _round_nonnegative(
        frame * 1000 * rate.denominator,
        rate.numerator,
    )
```

- [ ] **Step 4: Run focused and cumulative tests**

Run:

```powershell
uv run pytest tests/test_timebase.py tests/test_cli.py -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 5: Commit the timebase**

Run:

```powershell
git add src/minoru_studio/timebase.py tests/test_timebase.py
git commit -m "feat: add rational media timebase"
```

---

### Task 3: Define the Versioned Job Manifest

**Files:**
- Create: `src/minoru_studio/jobs/__init__.py`
- Create: `src/minoru_studio/jobs/model.py`
- Create: `tests/jobs/test_model.py`

**Interfaces:**
- Produces: `JobMode`, `JobStatus`, `StepStatus`
- Produces: `InputRef`, `StepRecord`, `ArtifactRecord`, `JobManifest`
- Produces: `new_manifest(name: str, mode: JobMode, now: datetime | None = None) -> JobManifest`
- Produces: `manifest_to_dict(manifest: JobManifest) -> dict[str, Any]`
- Produces: `manifest_from_dict(data: Mapping[str, Any]) -> JobManifest`
- Consumes: `schema_version == 1`

- [ ] **Step 1: Write failing manifest tests**

Create `tests/jobs/test_model.py`:

```python
from datetime import UTC, datetime

import pytest

from minoru_studio.jobs.model import (
    JobMode,
    JobStatus,
    ManifestError,
    StepRecord,
    StepStatus,
    manifest_from_dict,
    manifest_to_dict,
    new_manifest,
)


def test_new_manifest_has_stable_schema_and_pending_state():
    now = datetime(2026, 7, 18, 3, 0, tzinfo=UTC)
    manifest = new_manifest("demo", JobMode.BEAT_SYNC, now=now)
    assert manifest.schema_version == 1
    assert manifest.name == "demo"
    assert manifest.mode is JobMode.BEAT_SYNC
    assert manifest.status is JobStatus.PENDING
    assert manifest.created_at == "2026-07-18T03:00:00+00:00"
    assert manifest.updated_at == manifest.created_at


def test_manifest_round_trip_preserves_nested_enums():
    manifest = new_manifest("voice", JobMode.NARRATE)
    manifest.steps["prepare"] = StepRecord(status=StepStatus.RUNNING)
    restored = manifest_from_dict(manifest_to_dict(manifest))
    assert restored == manifest
    assert restored.steps["prepare"].status is StepStatus.RUNNING


def test_unknown_schema_is_rejected():
    manifest = new_manifest("demo", JobMode.TRANSCRIBE)
    data = manifest_to_dict(manifest)
    data["schema_version"] = 999
    with pytest.raises(ManifestError, match="unsupported schema_version"):
        manifest_from_dict(data)


def test_all_fixed_modes_are_declared():
    assert {mode.value for mode in JobMode} == {
        "beat-sync",
        "transcribe",
        "narrate",
        "script-draft",
        "repo-demo",
    }
```

- [ ] **Step 2: Run the tests and verify the missing-module failure**

Run:

```powershell
uv run pytest tests/jobs/test_model.py -q
```

Expected: collection fails because `minoru_studio.jobs.model` does not exist.

- [ ] **Step 3: Implement the manifest types and serialization**

Create `src/minoru_studio/jobs/__init__.py`:

```python
"""Job package model, locking, and persistence."""
```

Create `src/minoru_studio/jobs/model.py`:

```python
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
    REPO_DEMO = "repo-demo"


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
        return JobManifest(
            schema_version=SCHEMA_VERSION,
            job_id=str(data["job_id"]),
            name=str(data["name"]),
            mode=JobMode(data["mode"]),
            status=JobStatus(data["status"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            inputs=[InputRef(**item) for item in data.get("inputs", [])],
            settings=dict(data.get("settings", {})),
            steps={
                name: StepRecord(
                    **{**step, "status": StepStatus(step["status"])}
                )
                for name, step in data.get("steps", {}).items()
            },
            artifacts=[
                ArtifactRecord(**item) for item in data.get("artifacts", [])
            ],
            last_error=data.get("last_error"),
            resolve_applications=list(data.get("resolve_applications", [])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestError(f"invalid manifest: {exc}") from exc
```

- [ ] **Step 4: Run the manifest and cumulative tests**

Run:

```powershell
uv run pytest tests/jobs/test_model.py tests/test_timebase.py tests/test_cli.py -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 5: Commit the manifest model**

Run:

```powershell
git add src/minoru_studio/jobs/__init__.py src/minoru_studio/jobs/model.py tests/jobs/test_model.py
git commit -m "feat: define versioned job manifest"
```

---

### Task 4: Add Safe Job Persistence, Fingerprints, Locking, and Resume

**Files:**
- Create: `src/minoru_studio/jobs/lock.py`
- Create: `src/minoru_studio/jobs/store.py`
- Create: `tests/jobs/test_lock.py`
- Create: `tests/jobs/test_store.py`

**Interfaces:**
- Produces: `fingerprint_file(path: Path) -> InputRef`
- Produces: `safe_job_name(value: str) -> str`
- Produces: `JobStore.create(root: Path, name: str, mode: JobMode, input_paths: Iterable[Path] = (), settings: Mapping[str, Any] | None = None) -> Path`
- Produces: `JobStore.load(job_dir: Path, recover_interrupted: bool = True) -> JobManifest`
- Produces: `JobStore.save(job_dir: Path, manifest: JobManifest) -> None`
- Produces: `JobLock(job_dir: Path)` context manager
- Produces: `lock_is_active(job_dir: Path) -> bool`
- Consumes: Task 3 manifest types and serialization functions

- [ ] **Step 1: Write failing store and lock tests**

Create `tests/jobs/test_store.py`:

```python
import json

from minoru_studio.jobs.model import JobMode, JobStatus, StepRecord, StepStatus
from minoru_studio.jobs.store import JobStore, fingerprint_file, safe_job_name


def test_safe_job_name_blocks_windows_path_syntax():
    assert safe_job_name(" Demo:One/Two. ") == "Demo_One_Two"


def test_create_uses_incrementing_directory_without_overwrite(tmp_path):
    store = JobStore()
    first = store.create(tmp_path, "demo", JobMode.BEAT_SYNC)
    second = store.create(tmp_path, "demo", JobMode.BEAT_SYNC)
    assert first.name == "demo.media-job"
    assert second.name == "demo-002.media-job"
    assert json.loads((first / "job.json").read_text(encoding="utf-8"))[
        "status"
    ] == "pending"


def test_fingerprint_records_absolute_path_and_sha256(tmp_path):
    source = tmp_path / "台本.txt"
    source.write_text("hello", encoding="utf-8")
    ref = fingerprint_file(source)
    assert ref.path == str(source.resolve())
    assert ref.size == 5
    assert ref.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_load_recovers_unlocked_running_state(tmp_path):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    manifest = store.load(job_dir)
    manifest.status = JobStatus.RUNNING
    manifest.steps["extract"] = StepRecord(status=StepStatus.RUNNING)
    store.save(job_dir, manifest)
    recovered = store.load(job_dir, recover_interrupted=True)
    assert recovered.status is JobStatus.INTERRUPTED
    assert recovered.steps["extract"].status is StepStatus.INTERRUPTED


def test_load_recovers_running_state_when_lock_pid_is_dead(tmp_path, monkeypatch):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    manifest = store.load(job_dir)
    manifest.status = JobStatus.RUNNING
    store.save(job_dir, manifest)
    (job_dir / "job.lock").write_text(
        json.dumps({"pid": 999999, "token": "dead"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("minoru_studio.jobs.lock._pid_is_running", lambda pid: False)
    assert store.load(job_dir).status is JobStatus.INTERRUPTED


def test_load_preserves_running_state_while_live_lock_is_held(tmp_path):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    manifest = store.load(job_dir)
    manifest.status = JobStatus.RUNNING
    store.save(job_dir, manifest)
    with store.locked(job_dir):
        assert store.load(job_dir).status is JobStatus.RUNNING
```

Create `tests/jobs/test_lock.py`:

```python
import json

import pytest

from minoru_studio.jobs.lock import JobLock, JobLockedError


def test_lock_is_exclusive_and_release_is_token_safe(tmp_path):
    job_dir = tmp_path / "demo.media-job"
    job_dir.mkdir()
    with JobLock(job_dir):
        with pytest.raises(JobLockedError):
            JobLock(job_dir).acquire()
        payload = json.loads((job_dir / "job.lock").read_text(encoding="utf-8"))
        assert payload["pid"] > 0
        assert payload["token"]
    assert not (job_dir / "job.lock").exists()


def test_dead_pid_lock_is_replaced(tmp_path, monkeypatch):
    job_dir = tmp_path / "demo.media-job"
    job_dir.mkdir()
    (job_dir / "job.lock").write_text(
        json.dumps({"pid": 999999, "token": "old"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("minoru_studio.jobs.lock._pid_is_running", lambda pid: False)
    with JobLock(job_dir):
        payload = json.loads((job_dir / "job.lock").read_text(encoding="utf-8"))
        assert payload["token"] != "old"
```

- [ ] **Step 2: Run the focused tests and verify missing interfaces**

Run:

```powershell
uv run pytest tests/jobs/test_store.py tests/jobs/test_lock.py -q
```

Expected: collection fails because `jobs.store` and `jobs.lock` do not exist.

- [ ] **Step 3: Implement token-safe exclusive locking**

Create `src/minoru_studio/jobs/lock.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


class JobLockedError(RuntimeError):
    pass


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def lock_is_active(job_dir: Path) -> bool:
    path = Path(job_dir) / "job.lock"
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _pid_is_running(int(payload.get("pid", 0)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


class JobLock:
    def __init__(self, job_dir: Path):
        self.path = Path(job_dir) / "job.lock"
        self.token = str(uuid4())
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                )
            except FileExistsError:
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    pid = int(payload.get("pid", 0))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pid = 0
                if _pid_is_running(pid):
                    raise JobLockedError(f"job is locked by pid {pid}")
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "token": self.token}, handle)
                handle.write("\n")
            self._owned = True
            return
        raise JobLockedError("unable to acquire job lock")

    def release(self) -> None:
        if not self._owned:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if payload.get("token") == self.token:
            self.path.unlink(missing_ok=True)
        self._owned = False

    def __enter__(self) -> "JobLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()
```

- [ ] **Step 4: Implement atomic job storage and recovery**

Create `src/minoru_studio/jobs/store.py`:

```python
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from minoru_studio.jobs.lock import JobLock, lock_is_active
from minoru_studio.jobs.model import (
    InputRef,
    JobManifest,
    JobMode,
    JobStatus,
    StepStatus,
    manifest_from_dict,
    manifest_to_dict,
    new_manifest,
)


_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_job_name(value: str) -> str:
    cleaned = _INVALID_WINDOWS_CHARS.sub("_", value.strip()).rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise ValueError("job name must not be empty")
    if cleaned.upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def fingerprint_file(path: Path) -> InputRef:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"input is not a file: {resolved}")
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    stat = resolved.stat()
    return InputRef(
        path=str(resolved),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=digest.hexdigest(),
    )


class JobStore:
    def create(
        self,
        root: Path,
        name: str,
        mode: JobMode,
        input_paths: Iterable[Path] = (),
        settings: Mapping[str, Any] | None = None,
    ) -> Path:
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        base = safe_job_name(name)
        input_refs = [fingerprint_file(path) for path in input_paths]
        sequence = 1
        while True:
            suffix = "" if sequence == 1 else f"-{sequence:03d}"
            candidate = root / f"{base}{suffix}.media-job"
            try:
                candidate.mkdir()
            except FileExistsError:
                sequence += 1
                continue
            break
        for child in ("inputs", "outputs", "work", "logs", "resolve"):
            (candidate / child).mkdir()
        manifest = new_manifest(base, mode)
        manifest.inputs = input_refs
        manifest.settings = dict(settings or {})
        self.save(candidate, manifest)
        return candidate

    def load(
        self,
        job_dir: Path,
        recover_interrupted: bool = True,
    ) -> JobManifest:
        job_dir = Path(job_dir).resolve(strict=True)
        data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        manifest = manifest_from_dict(data)
        if (
            recover_interrupted
            and manifest.status is JobStatus.RUNNING
            and not lock_is_active(job_dir)
        ):
            manifest.status = JobStatus.INTERRUPTED
            for step in manifest.steps.values():
                if step.status is StepStatus.RUNNING:
                    step.status = StepStatus.INTERRUPTED
            self.save(job_dir, manifest)
        return manifest

    def save(self, job_dir: Path, manifest: JobManifest) -> None:
        job_dir = Path(job_dir).resolve()
        with JobLock(job_dir):
            self._write_manifest(job_dir, manifest)

    def _write_manifest(self, job_dir: Path, manifest: JobManifest) -> None:
        manifest.updated_at = datetime.now(UTC).isoformat()
        target = job_dir / "job.json"
        temporary = job_dir / f".job-{uuid4()}.tmp"
        payload = json.dumps(
            manifest_to_dict(manifest),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        temporary.write_text(payload, encoding="utf-8")
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def locked(self, job_dir: Path) -> JobLock:
        return JobLock(job_dir)
```

- [ ] **Step 5: Run focused and cumulative tests**

Run:

```powershell
uv run pytest tests/jobs tests/test_timebase.py tests/test_cli.py -q
```

Expected: all tests pass; no `.tmp` or `job.lock` files remain after tests.

- [ ] **Step 6: Commit persistence and locking**

Run:

```powershell
git add src/minoru_studio/jobs/lock.py src/minoru_studio/jobs/store.py tests/jobs/test_lock.py tests/jobs/test_store.py
git commit -m "feat: add safe job persistence"
```

---

### Task 5: Add Secret Redaction, Logging, and Shell-Free Process Execution

**Files:**
- Create: `src/minoru_studio/redaction.py`
- Create: `src/minoru_studio/logging_utils.py`
- Create: `src/minoru_studio/processes.py`
- Create: `tests/test_redaction.py`
- Create: `tests/test_processes.py`

**Interfaces:**
- Produces: `redact_text(value: str, secrets: Iterable[str] = ()) -> str`
- Produces: `configure_job_logger(job_dir: Path, secrets: Iterable[str] = ()) -> logging.Logger`
- Produces: `ProcessResult(returncode: int, stdout: str, stderr: str, display_command: str)`
- Produces: `run_process(args: Sequence[str], cwd: Path | None = None, timeout_s: float = 30, secrets: Iterable[str] = ()) -> ProcessResult`
- Consumes: job directory `logs/` from Task 4

- [ ] **Step 1: Write failing redaction and process tests**

Create `tests/test_redaction.py`:

```python
import logging

from minoru_studio.logging_utils import configure_job_logger
from minoru_studio.redaction import redact_text


def test_redact_text_masks_values_and_secret_shaped_arguments():
    text = "OPENAI_API_KEY=abc123 --api-key=abc123 token=other"
    redacted = redact_text(text, secrets=["abc123", "other"])
    assert "abc123" not in redacted
    assert "other" not in redacted
    assert redacted.count("***") >= 3


def test_job_logger_never_writes_known_secret(tmp_path):
    logger = configure_job_logger(tmp_path, secrets=["top-secret"])
    logger.info("credential=top-secret")
    for handler in logger.handlers:
        handler.flush()
    text = (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    assert "top-secret" not in text
    assert "***" in text
```

Create `tests/test_processes.py`:

```python
import sys

from minoru_studio.processes import run_process


def test_run_process_captures_exit_code_and_redacts_display():
    result = run_process(
        [sys.executable, "-c", "import sys; print('ok'); sys.exit(3)", "secret"],
        secrets=["secret"],
    )
    assert result.returncode == 3
    assert result.stdout.strip() == "ok"
    assert "secret" not in result.display_command
    assert "***" in result.display_command
```

- [ ] **Step 2: Run tests and verify missing modules**

Run:

```powershell
uv run pytest tests/test_redaction.py tests/test_processes.py -q
```

Expected: collection fails because the new modules do not exist.

- [ ] **Step 3: Implement redaction and logging**

Create `src/minoru_studio/redaction.py`:

```python
from __future__ import annotations

import re
from collections.abc import Iterable


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|auth|password|secret)(\s*[:=]\s*)([^\s]+)"
)


def redact_text(value: str, secrets: Iterable[str] = ()) -> str:
    redacted = value
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        redacted = redacted.replace(secret, "***")
    return _SECRET_ASSIGNMENT.sub(r"\1\2***", redacted)
```

Create `src/minoru_studio/logging_utils.py`:

```python
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from minoru_studio.redaction import redact_text


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        self.secrets = tuple(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.getMessage()), self.secrets)
        record.args = ()
        return True


def configure_job_logger(
    job_dir: Path,
    secrets: Iterable[str] = (),
) -> logging.Logger:
    logs_dir = Path(job_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"minoru_studio.job.{Path(job_dir).resolve()}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    redactor = RedactingFilter(secrets)
    for handler in (
        logging.FileHandler(logs_dir / "run.log", encoding="utf-8"),
        logging.StreamHandler(),
    ):
        handler.setFormatter(formatter)
        handler.addFilter(redactor)
        logger.addHandler(handler)
    return logger
```

- [ ] **Step 4: Implement shell-free process execution**

Create `src/minoru_studio/processes.py`:

```python
from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from minoru_studio.redaction import redact_text


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    display_command: str


def run_process(
    args: Sequence[str],
    cwd: Path | None = None,
    timeout_s: float = 30,
    secrets: Iterable[str] = (),
) -> ProcessResult:
    if not args:
        raise ValueError("args must not be empty")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        shell=False,
        check=False,
        creationflags=creationflags,
    )
    display = subprocess.list2cmdline(list(args))
    return ProcessResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        display_command=redact_text(display, secrets),
    )
```

- [ ] **Step 5: Run focused and cumulative tests**

Run:

```powershell
uv run pytest tests/test_redaction.py tests/test_processes.py tests/jobs tests/test_timebase.py tests/test_cli.py -q
```

Expected: all tests pass; the temporary log contains masking markers and no known secret.

- [ ] **Step 6: Commit the process boundary**

Run:

```powershell
git add src/minoru_studio/redaction.py src/minoru_studio/logging_utils.py src/minoru_studio/processes.py tests/test_redaction.py tests/test_processes.py
git commit -m "feat: add redacted process logging"
```

---

### Task 6: Add Environment Diagnostics and the `doctor` Command

**Files:**
- Create: `src/minoru_studio/doctor.py`
- Create: `tests/test_doctor.py`
- Modify: `src/minoru_studio/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `CheckStatus`, `ToolCheck`, `DoctorReport`
- Produces: `run_doctor() -> DoctorReport`
- Produces: `render_doctor(report: DoctorReport, as_json: bool) -> str`
- Consumes: `run_process` from Task 5
- Extends: `minoru-studio doctor [-Json|--json]`

- [ ] **Step 1: Write failing doctor tests**

Create `tests/test_doctor.py`:

```python
import json

from minoru_studio.doctor import CheckStatus, run_doctor, render_doctor


def test_doctor_reports_each_required_tool(monkeypatch):
    paths = {
        "pwsh": "C:/Program Files/PowerShell/7/pwsh.exe",
        "uv": "C:/tools/uv.exe",
        "ffmpeg": "C:/tools/ffmpeg.exe",
        "ffprobe": "C:/tools/ffprobe.exe",
    }
    monkeypatch.setattr(
        "minoru_studio.doctor.shutil.which",
        lambda name: paths.get(name),
    )
    monkeypatch.setattr(
        "minoru_studio.doctor._read_version",
        lambda path, args: "test-version",
    )
    report = run_doctor()
    assert report.ok
    assert {check.name for check in report.checks} == {
        "python",
        "powershell",
        "uv",
        "ffmpeg",
        "ffprobe",
    }
    assert all(check.status is CheckStatus.OK for check in report.checks)


def test_missing_tool_makes_report_fail(monkeypatch):
    monkeypatch.setattr("minoru_studio.doctor.shutil.which", lambda name: None)
    report = run_doctor()
    assert not report.ok
    assert any(check.status is CheckStatus.MISSING for check in report.checks)


def test_json_report_is_machine_readable(monkeypatch):
    monkeypatch.setattr("minoru_studio.doctor.shutil.which", lambda name: None)
    payload = json.loads(render_doctor(run_doctor(), as_json=True))
    assert payload["ok"] is False
    assert isinstance(payload["checks"], list)
```

Append to `tests/test_cli.py`:

```python
def test_doctor_exit_code_reflects_report(monkeypatch):
    from minoru_studio.doctor import DoctorReport

    monkeypatch.setattr(
        "minoru_studio.cli.run_doctor",
        lambda: DoctorReport(checks=[]),
    )
    assert main(["doctor", "--json"]) == 0
```

- [ ] **Step 2: Run tests and verify missing doctor interfaces**

Run:

```powershell
uv run pytest tests/test_doctor.py tests/test_cli.py -q
```

Expected: collection or monkeypatch setup fails because `doctor.py` and CLI wiring do not exist.

- [ ] **Step 3: Implement deterministic tool diagnostics**

Create `src/minoru_studio/doctor.py`:

```python
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum

from minoru_studio.processes import run_process


class CheckStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ToolCheck:
    name: str
    required: bool
    status: CheckStatus
    path: str | None
    version: str | None
    message: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: list[ToolCheck]

    @property
    def ok(self) -> bool:
        return all(
            not check.required or check.status is CheckStatus.OK
            for check in self.checks
        )


def _read_version(path: str, args: list[str]) -> str:
    result = run_process([path, *args], timeout_s=10)
    text = (result.stdout or result.stderr).strip().splitlines()
    if result.returncode != 0 or not text:
        raise RuntimeError(f"version command failed with {result.returncode}")
    return text[0]


def _external_check(
    name: str,
    candidates: tuple[str, ...],
    version_args: list[str],
) -> ToolCheck:
    path = next((shutil.which(item) for item in candidates if shutil.which(item)), None)
    if path is None:
        return ToolCheck(name, True, CheckStatus.MISSING, None, None, "not found on PATH")
    try:
        version = _read_version(path, version_args)
    except Exception as exc:
        return ToolCheck(name, True, CheckStatus.ERROR, path, None, str(exc))
    return ToolCheck(name, True, CheckStatus.OK, path, version, "available")


def run_doctor() -> DoctorReport:
    python_ok = sys.version_info[:2] == (3, 12)
    checks = [
        ToolCheck(
            "python",
            True,
            CheckStatus.OK if python_ok else CheckStatus.UNSUPPORTED,
            sys.executable,
            sys.version.split()[0],
            "Python 3.12" if python_ok else "Python 3.12 is required",
        ),
        _external_check("powershell", ("pwsh",), ["--version"]),
        _external_check("uv", ("uv",), ["--version"]),
        _external_check("ffmpeg", ("ffmpeg",), ["-version"]),
        _external_check("ffprobe", ("ffprobe",), ["-version"]),
    ]
    return DoctorReport(checks)


def render_doctor(report: DoctorReport, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {"ok": report.ok, "checks": [asdict(item) for item in report.checks]},
            ensure_ascii=False,
            indent=2,
        )
    lines = ["MinoruStudio environment check"]
    lines.extend(
        f"[{item.status.value}] {item.name}: {item.message}"
        for item in report.checks
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Wire `doctor` into the CLI**

Update `src/minoru_studio/cli.py` imports and parser:

```python
from minoru_studio.doctor import render_doctor, run_doctor


def _doctor_command(args: argparse.Namespace) -> int:
    report = run_doctor()
    print(render_doctor(report, as_json=args.as_json))
    return 0 if report.ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minoru-studio",
        description="MinoruStudio local video-production tools",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")
    doctor_parser = subparsers.add_parser("doctor", help="check required local tools")
    doctor_parser.add_argument("-Json", "--json", dest="as_json", action="store_true")
    doctor_parser.set_defaults(handler=_doctor_command)
    return parser
```

Retain the existing `main()` implementation unchanged.

- [ ] **Step 5: Run doctor tests and inspect a real report**

Run:

```powershell
uv run pytest tests/test_doctor.py tests/test_cli.py -q
uv run minoru-studio doctor --json
```

Expected: tests pass. The real command emits valid JSON; it exits 0 when all five required checks are `ok`, otherwise exits 2 and names every unavailable or unsupported tool.

- [ ] **Step 6: Commit diagnostics**

Run:

```powershell
git add src/minoru_studio/doctor.py src/minoru_studio/cli.py tests/test_doctor.py tests/test_cli.py
git commit -m "feat: add environment doctor command"
```

---

### Task 7: Add Non-Interactive Job Management Commands

**Files:**
- Create: `src/minoru_studio/job_commands.py`
- Create: `tests/test_job_commands.py`
- Modify: `src/minoru_studio/cli.py`

**Interfaces:**
- Produces: `create_job_command(args: argparse.Namespace) -> int`
- Produces: `inspect_job_command(args: argparse.Namespace) -> int`
- Extends: `minoru-studio jobs create -Mode <mode> -Name <name> -OutputDir <dir>`
- Extends: `minoru-studio jobs inspect <job-dir>`
- Consumes: `JobStore`, `JobMode`, and `manifest_to_dict`

- [ ] **Step 1: Write failing command tests**

Create `tests/test_job_commands.py`:

```python
import json

from minoru_studio.cli import main


def test_jobs_create_prints_new_job_path(tmp_path, capsys):
    assert main([
        "jobs",
        "create",
        "-Mode",
        "beat-sync",
        "-Name",
        "demo",
        "-OutputDir",
        str(tmp_path),
    ]) == 0
    created = capsys.readouterr().out.strip()
    assert created.endswith("demo.media-job")


def test_jobs_inspect_prints_valid_json(tmp_path, capsys):
    main([
        "jobs", "create", "-Mode", "narrate", "-Name", "voice",
        "-OutputDir", str(tmp_path),
    ])
    job_dir = capsys.readouterr().out.strip()
    before = (tmp_path / "voice.media-job" / "job.json").read_text(encoding="utf-8")
    assert main(["jobs", "inspect", job_dir]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "narrate"
    assert payload["status"] == "pending"
    after = (tmp_path / "voice.media-job" / "job.json").read_text(encoding="utf-8")
    assert after == before
```

- [ ] **Step 2: Run the tests and verify missing CLI commands**

Run:

```powershell
uv run pytest tests/test_job_commands.py -q
```

Expected: argparse exits with an invalid-choice error for `jobs`.

- [ ] **Step 3: Implement job command handlers**

Create `src/minoru_studio/job_commands.py`:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from minoru_studio.jobs.model import JobMode, manifest_to_dict
from minoru_studio.jobs.store import JobStore


def create_job_command(args: argparse.Namespace) -> int:
    job_dir = JobStore().create(
        root=Path(args.output_dir),
        name=args.name,
        mode=JobMode(args.mode),
    )
    print(job_dir)
    return 0


def inspect_job_command(args: argparse.Namespace) -> int:
    manifest = JobStore().load(Path(args.job_dir), recover_interrupted=False)
    print(json.dumps(manifest_to_dict(manifest), ensure_ascii=False, indent=2))
    return 0
```

- [ ] **Step 4: Add nested `jobs` parsers**

In `build_parser()` in `src/minoru_studio/cli.py`, add these parsers before `return parser`:

```python
from minoru_studio.job_commands import create_job_command, inspect_job_command
from minoru_studio.jobs.model import JobMode


jobs_parser = subparsers.add_parser("jobs", help="create and inspect job packages")
jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command", required=True)

create_parser = jobs_subparsers.add_parser("create", help="create a pending job")
create_parser.add_argument(
    "-Mode", "--mode", dest="mode", required=True,
    choices=[mode.value for mode in JobMode],
)
create_parser.add_argument("-Name", "--name", dest="name", required=True)
create_parser.add_argument(
    "-OutputDir", "--output-dir", dest="output_dir", required=True,
)
create_parser.set_defaults(handler=create_job_command)

inspect_parser = jobs_subparsers.add_parser("inspect", help="print a job manifest")
inspect_parser.add_argument("job_dir")
inspect_parser.set_defaults(handler=inspect_job_command)
```

Place the imports at module scope, not inside `build_parser()`.

- [ ] **Step 5: Run focused and cumulative CLI tests**

Run:

```powershell
uv run pytest tests/test_job_commands.py tests/test_cli.py tests/jobs -q
```

Expected: all tests pass; repeated creation uses `-002` without changing the first manifest.

- [ ] **Step 6: Commit job commands**

Run:

```powershell
git add src/minoru_studio/job_commands.py src/minoru_studio/cli.py tests/test_job_commands.py
git commit -m "feat: add job management commands"
```

---

### Task 8: Add the Minimal tkinter Launcher

**Files:**
- Create: `src/minoru_studio/gui.py`
- Create: `tests/test_gui_controller.py`
- Modify: `src/minoru_studio/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `LauncherController.create_job(mode: str, name: str, output_dir: str) -> Path`
- Produces: `LauncherController.inspect_job(job_dir: str) -> JobManifest`
- Produces: `launch_gui(controller: LauncherController | None = None) -> None`
- Changes: no-argument `main([])` calls `launch_gui()` and returns 0
- Consumes: `JobStore`, `JobMode`, and manifest status fields

- [ ] **Step 1: Write failing controller and CLI-dispatch tests**

Create `tests/test_gui_controller.py`:

```python
from minoru_studio.gui import LauncherController


def test_controller_creates_and_inspects_pending_job(tmp_path):
    controller = LauncherController()
    job_dir = controller.create_job("repo-demo", "demo", str(tmp_path))
    manifest = controller.inspect_job(str(job_dir))
    assert manifest.mode.value == "repo-demo"
    assert manifest.status.value == "pending"
```

Replace the temporary empty-argv test in `tests/test_cli.py` with:

```python
def test_empty_argv_launches_gui(monkeypatch):
    calls = []
    monkeypatch.setattr("minoru_studio.cli.launch_gui", lambda: calls.append("gui"))
    assert main([]) == 0
    assert calls == ["gui"]
```

- [ ] **Step 2: Run focused tests and verify the GUI module is missing**

Run:

```powershell
uv run pytest tests/test_gui_controller.py tests/test_cli.py -q
```

Expected: collection or monkeypatch setup fails because `gui.py` and GUI dispatch do not exist.

- [ ] **Step 3: Implement the controller and launcher window**

Create `src/minoru_studio/gui.py`:

```python
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from minoru_studio.jobs.model import JobManifest, JobMode
from minoru_studio.jobs.store import JobStore


class LauncherController:
    def __init__(self, store: JobStore | None = None):
        self.store = store or JobStore()

    def create_job(self, mode: str, name: str, output_dir: str) -> Path:
        return self.store.create(
            root=Path(output_dir),
            name=name,
            mode=JobMode(mode),
        )

    def inspect_job(self, job_dir: str) -> JobManifest:
        return self.store.load(Path(job_dir), recover_interrupted=False)


def launch_gui(controller: LauncherController | None = None) -> None:
    controller = controller or LauncherController()
    root = tk.Tk()
    root.title("MinoruStudio")
    root.geometry("560x330")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    mode_var = tk.StringVar(value=JobMode.BEAT_SYNC.value)
    name_var = tk.StringVar(value="video-job")
    output_var = tk.StringVar(value=str(Path.home() / "Videos" / "MinoruStudio"))
    status_var = tk.StringVar(value="新しいジョブを作成するか、既存ジョブを開いてください。")

    ttk.Label(frame, text="固定モード").grid(row=0, column=0, sticky="w")
    mode_box = ttk.Combobox(
        frame,
        textvariable=mode_var,
        values=[mode.value for mode in JobMode],
        state="readonly",
        width=28,
    )
    mode_box.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)

    ttk.Label(frame, text="ジョブ名").grid(row=1, column=0, sticky="w")
    ttk.Entry(frame, textvariable=name_var, width=31).grid(
        row=1, column=1, columnspan=2, sticky="ew", pady=4
    )

    ttk.Label(frame, text="保存先").grid(row=2, column=0, sticky="w")
    ttk.Entry(frame, textvariable=output_var, width=31).grid(
        row=2, column=1, sticky="ew", pady=4
    )

    def choose_output() -> None:
        selected = filedialog.askdirectory(initialdir=output_var.get())
        if selected:
            output_var.set(selected)

    ttk.Button(frame, text="選択", command=choose_output).grid(row=2, column=2, padx=4)

    def create_job() -> None:
        try:
            job_dir = controller.create_job(
                mode_var.get(), name_var.get(), output_var.get()
            )
        except Exception as exc:
            messagebox.showerror("ジョブ作成失敗", str(exc), parent=root)
            return
        status_var.set(f"作成: {job_dir}")
        messagebox.showinfo(
            "ジョブを作成しました",
            f"{job_dir}\n\n状態: pending",
            parent=root,
        )

    def open_job() -> None:
        selected = filedialog.askdirectory(title=".media-job を選択")
        if not selected:
            return
        try:
            manifest = controller.inspect_job(selected)
        except Exception as exc:
            messagebox.showerror("ジョブ読込失敗", str(exc), parent=root)
            return
        status_var.set(
            f"{manifest.name} / {manifest.mode.value} / {manifest.status.value}"
        )

    ttk.Button(frame, text="新しいジョブを作成", command=create_job).grid(
        row=3, column=0, columnspan=2, sticky="ew", pady=(18, 8)
    )
    ttk.Button(frame, text="既存ジョブを開く", command=open_job).grid(
        row=3, column=2, sticky="ew", pady=(18, 8)
    )
    ttk.Separator(frame).grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
    ttk.Label(frame, textvariable=status_var, wraplength=500).grid(
        row=5, column=0, columnspan=3, sticky="w"
    )
    frame.columnconfigure(1, weight=1)
    root.mainloop()
```

- [ ] **Step 4: Change no-argument CLI behavior to launch the GUI lazily**

Add this wrapper to `src/minoru_studio/cli.py` so explicit subcommands do not import tkinter:

```python
def launch_gui() -> None:
    from minoru_studio.gui import launch_gui as run_gui

    run_gui()
```

Change the handler-free branch in `main()` to:

```python
    if handler is None:
        launch_gui()
        return 0
```

Explicit subcommands continue to dispatch without creating a tkinter root.

- [ ] **Step 5: Run automated tests and a manual Windows GUI smoke test**

Run:

```powershell
uv run pytest tests/test_gui_controller.py tests/test_cli.py tests/test_job_commands.py -q
uv run minoru-studio
```

Expected automated result: all tests pass. Expected manual result: one window opens; creating a `repo-demo` job produces a pending `.media-job`; opening that directory displays its name, mode, and status. Close the window normally after verification.

- [ ] **Step 6: Commit the launcher**

Run:

```powershell
git add src/minoru_studio/gui.py src/minoru_studio/cli.py tests/test_gui_controller.py tests/test_cli.py
git commit -m "feat: add MinoruStudio launcher GUI"
```

---

### Task 9: Document Phase 1 and Run Final Acceptance Gates

**Files:**
- Modify: `README.md`
- Verify: all files allowed by this plan

**Interfaces:**
- Documents: development setup, CLI, GUI, job creation, diagnostics, explicit Phase 1 non-goals
- Consumes: all Phase 1 commands and paths from Tasks 1-8

- [ ] **Step 1: Add the MinoruStudio foundation section to README**

Insert before the existing `# MinoruDouga` heading:

```markdown
# MinoruStudio（基盤開発中）

MinoruStudioは、MinoruDougaの音ハメ機能を将来移植し、字幕、読み上げ、
デモ動画制作を固定モードで扱うWindows向けローカルツールです。

現在の実装段階は共通基盤のみです。メディア処理とResolve連携はまだ
MinoruStudioへ移植していません。既存MinoruDougaは下記の手順で引き続き利用できます。

## 開発環境

```powershell
uv sync --dev
uv run pytest -q
```

## 基盤コマンド

```powershell
uv run minoru-studio --version
uv run minoru-studio doctor --json
uv run minoru-studio jobs create -Mode beat-sync -Name demo -OutputDir .\jobs
uv run minoru-studio jobs inspect .\jobs\demo.media-job
uv run minoru-studio
```

引数なしではジョブ管理GUIを開きます。Phase 1のジョブは `pending` 状態の
基盤データだけを作り、動画、音声、Resolveタイムラインを変更しません。

---
```

- [ ] **Step 2: Run the full automated suite**

Run:

```powershell
uv sync --dev
uv run pytest -q
```

Expected: exit 0 with zero failed tests.

- [ ] **Step 3: Run CLI and persistence acceptance checks**

Run in a disposable test directory under the repository:

```powershell
uv run minoru-studio --version
uv run minoru-studio doctor --json
uv run minoru-studio jobs create -Mode beat-sync -Name acceptance -OutputDir .\.tmp-acceptance
uv run minoru-studio jobs create -Mode beat-sync -Name acceptance -OutputDir .\.tmp-acceptance
uv run minoru-studio jobs inspect .\.tmp-acceptance\acceptance.media-job
```

Expected:

- Version is `0.1.0`.
- Doctor emits parseable JSON and returns the correct 0 or 2 status for the machine.
- The two job paths end in `acceptance.media-job` and `acceptance-002.media-job`.
- Inspect reports mode `beat-sync`, status `pending`, schema version 1.
- No source media, legacy MinoruDouga file, or external service is touched.

Resolve and validate the disposable target before deleting it:

```powershell
$acceptanceRoot = (Resolve-Path '.\.tmp-acceptance').Path
$repositoryRoot = (Resolve-Path '.').Path
$expectedAcceptanceRoot = [IO.Path]::GetFullPath(
    (Join-Path $repositoryRoot '.tmp-acceptance')
)
if (
    $acceptanceRoot -ne $expectedAcceptanceRoot -or
    [IO.Path]::GetDirectoryName($acceptanceRoot) -ne $repositoryRoot
) {
    throw "Unexpected acceptance cleanup target: $acceptanceRoot"
}
Remove-Item -LiteralPath $acceptanceRoot -Recurse
```

- [ ] **Step 4: Run the final scope and diff gates**

Run:

```powershell
git diff --check
git status --short
git diff --name-only HEAD~8..HEAD
```

Expected: diff check exits 0; changed paths are limited to the allowed implementation files; protected MinoruDouga files are absent.

- [ ] **Step 5: Perform the manual GUI acceptance check**

Run:

```powershell
pwsh -NoProfile -File .\scripts\minoru-studio.ps1
```

Expected: one MinoruStudio window opens; create and inspect operations work; explicit CLI commands never open the GUI. Close the window after the check.

- [ ] **Step 6: Commit README and any test-only corrections**

Run:

```powershell
git add README.md
git commit -m "docs: document MinoruStudio foundation"
```

If a gate required a source or test correction, stage only the exact allowed files involved and use a separate focused commit before this README commit.

- [ ] **Step 7: Record the final evidence**

Report:

- pytest outcome and number of failures
- CLI smoke outcomes and doctor exit status
- job naming and inspect results
- manual GUI result
- protected-file check result
- residual warnings, including PATH-dependent doctor failures

Do not claim Phase 1 complete unless every acceptance criterion has direct evidence or an explicitly documented environment-only limitation.
