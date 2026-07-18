# Beat Sync Resolve Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply a successful beat-sync job inside DaVinci Resolve Free through a resumable two-stage workflow and create a new editable timeline without changing existing timelines.

**Architecture:** A separately installed Python 3.6-compatible adapter validates the versioned job contract, stores each Resolve application attempt in the job, and wraps all Resolve calls behind a gateway exercised by fakes. Preparation remains in Python 3.12; the adapter contains no third-party imports and never imports the external MinoruStudio package.

**Tech Stack:** Resolve Scripting API, Python 3.6-compatible standard library, Tkinter, ctypes Windows mutexes, pytest fakes, PowerShell installer.

## Global Constraints

- Complete `docs/superpowers/plans/2026-07-18-beat-sync-preparation.md` first.
- Follow `docs/superpowers/specs/2026-07-18-beat-sync-end-to-end-design.md`.
- Adapter sources must parse as Python 3.6 and contain no third-party imports.
- Resolve application is user-initiated from the Utility scripts menu.
- Use integer milliseconds in the plan and explicit integer frames in application records.
- Preserve source files, project settings, existing bins, and existing timelines.
- Never reuse `failed` or `applied` attempts.
- Waiting states resume the same attempt; staging/applying interruption makes that attempt `failed`.
- Hold the filesystem lock only for short JSON updates; logical operation ownership prevents a second active invocation.
- Delete only the exact probe timeline object created by the current invocation.
- Leave all legacy MinoruDouga source and launcher files in place.
- Real Resolve mutation is a final, separately authorized manual acceptance checkpoint.

---

## File Map

- Create `resolve_adapter/minoru_studio_resolve/contract.py` for job/plan validation.
- Create `resolve_adapter/minoru_studio_resolve/job_io.py` for Python 3.6 atomic records and compatible locks.
- Create `resolve_adapter/minoru_studio_resolve/state.py` for application states and transitions.
- Create `resolve_adapter/minoru_studio_resolve/placement.py` for rational frame and placement math.
- Create `resolve_adapter/minoru_studio_resolve/gateway.py` for all Resolve API calls.
- Create `resolve_adapter/minoru_studio_resolve/service.py` for staging, waiting, probing, and application.
- Create `resolve_adapter/minoru_studio_resolve/ui.py` and `entry.py` for Tkinter and Resolve globals.
- Create `scripts/MinoruStudio.py` as the Utility launcher.
- Modify `install.ps1` to install uv core and copy the adapter without system-Python pip.
- Add fake Resolve objects and adapter tests under `tests/resolve_adapter`.
- Add `docs/resolve-beat-sync-acceptance.md` and update `README.md`.
- Bump `pyproject.toml` and `src/minoru_studio/__init__.py` to 0.2.0 only after automated and real acceptance.

### Task 1: Adapter Contract Reader and Python 3.6 Gate

**Files:**
- Create: `resolve_adapter/__init__.py`
- Create: `resolve_adapter/minoru_studio_resolve/__init__.py`
- Create: `tests/resolve_adapter/__init__.py`
- Create: `resolve_adapter/minoru_studio_resolve/contract.py`
- Create: `tests/resolve_adapter/test_contract.py`
- Create: `tests/resolve_adapter/test_compatibility.py`

**Interfaces:**
- Produces: `ContractError`.
- Produces: `load_validated_job(job_dir) -> dict` with keys `root`, `manifest`, `plan`, and `inputs`.
- Produces: `contained_path(root, relative)` and `sha256_file(path)`.

- [ ] **Step 1: Write failing contract tests using a prepared fixture**

~~~python
import json
from pathlib import Path

import pytest

from minoru_studio.beat_sync.models import (
    BeatAnalysis,
    BeatSyncPlan,
    BeatSyncSettings,
    MaterialKind,
    MaterialPlan,
    save_plan,
)
from minoru_studio.jobs.model import JobMode, JobStatus
from minoru_studio.jobs.store import JobStore, fingerprint_artifact
from resolve_adapter.minoru_studio_resolve.contract import (
    ContractError,
    load_validated_job,
)


def prepared_job(tmp_path):
    music = tmp_path / "song.wav"
    photo = tmp_path / "photo.jpg"
    music.write_bytes(b"audio")
    photo.write_bytes(b"photo")
    store = JobStore()
    job_dir = store.create(
        tmp_path / "jobs",
        "demo",
        JobMode.BEAT_SYNC,
        [music, photo],
    )
    manifest = store.load(job_dir, recover_interrupted=False)
    plan = BeatSyncPlan(
        1,
        manifest.job_id,
        0,
        BeatAnalysis(2_000, 120.0, (500, 1_000), (0, 500, 1_000, 2_000)),
        BeatSyncSettings("auto", 1, "asc", "Demo"),
        (MaterialPlan(1, MaterialKind.PHOTO, 0),),
    )
    path = job_dir / "outputs" / "beat-sync-plan.json"
    save_plan(path, plan)
    artifact = fingerprint_artifact(job_dir, path, "beat-sync-plan")

    def finish(latest):
        latest.status = JobStatus.SUCCEEDED
        latest.artifacts = [artifact]

    store.update(job_dir, finish)
    return job_dir


def test_valid_contract_rechecks_all_hashes(tmp_path):
    job_dir = prepared_job(tmp_path)
    loaded = load_validated_job(job_dir)
    assert loaded["plan"]["job_id"] == loaded["manifest"]["job_id"]
    assert len(loaded["inputs"]) == 2


def test_tampered_input_is_rejected_before_resolve(tmp_path):
    job_dir = prepared_job(tmp_path)
    manifest = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    input_path = Path(manifest["inputs"][1]["path"])
    input_path.write_bytes(b"changed")
    with pytest.raises(ContractError, match="input changed"):
        load_validated_job(job_dir)


def test_escaping_artifact_path_is_rejected(tmp_path):
    job_dir = prepared_job(tmp_path)
    manifest_path = job_dir / "job.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ContractError, match="escapes job"):
        load_validated_job(job_dir)
~~~

- [ ] **Step 2: Write the Python 3.6/import gate**

~~~python
import ast
from pathlib import Path


ALLOWED_ROOTS = {
    "collections", "ctypes", "datetime", "hashlib", "json", "math",
    "os", "pathlib", "random", "re", "sys", "threading", "time",
    "tkinter", "traceback", "uuid", "minoru_studio_resolve",
}


def test_adapter_is_python36_and_standard_library_only():
    root = Path("resolve_adapter/minoru_studio_resolve")
    for path in root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path), feature_version=(3, 6))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                roots = [(node.module or "").split(".", 1)[0]]
            else:
                continue
            assert set(roots) <= ALLOWED_ROOTS, (path, roots)
~~~

- [ ] **Step 3: Verify failure**

Run: `uv run pytest tests/resolve_adapter/test_contract.py tests/resolve_adapter/test_compatibility.py -q`

Expected: missing adapter package failure.

- [ ] **Step 4: Implement the contract reader**

The adapter files must not use `from __future__ import annotations`, dataclasses, `pathlib.Path.unlink(missing_ok=True)`, built-in generic annotations, unions with `|`, or `StrEnum`.

Create `contract.py` with:

~~~python
import hashlib
import json
import math
import os


class ContractError(ValueError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def contained_path(root, relative):
    root = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root, relative.replace("/", os.sep)))
    try:
        common = os.path.commonpath([root, candidate])
    except ValueError:
        raise ContractError("artifact path escapes job")
    if os.path.normcase(common) != os.path.normcase(root):
        raise ContractError("artifact path escapes job")
    return candidate


def _read_object(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ContractError("cannot read JSON: {0}".format(exc))
    if not isinstance(value, dict):
        raise ContractError("JSON root must be an object")
    return value


def _integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("{0} must be integer".format(name))
    return value
~~~

Add `_validate_plan(plan, input_count)` implementing the Task 1 contract independently: schema/mode/job identity, all integer `_ms` values, finite positive BPM, increasing beats/cuts, at least two internal cut points, exact start/end, minimum spacing, interval/order/name, contiguous materials, input-index range and uniqueness.

Implement the public loader:

~~~python
def load_validated_job(job_dir):
    root = os.path.realpath(job_dir)
    manifest = _read_object(os.path.join(root, "job.json"))
    if manifest.get("schema_version") != 1:
        raise ContractError("unsupported job schema")
    if manifest.get("mode") != "beat-sync" or manifest.get("status") != "succeeded":
        raise ContractError("job is not a successful beat-sync job")
    artifacts = [
        item for item in manifest.get("artifacts", [])
        if item.get("kind") == "beat-sync-plan"
    ]
    if len(artifacts) != 1:
        raise ContractError("job must contain one beat-sync plan")
    artifact = artifacts[0]
    plan_path = contained_path(root, artifact["path"])
    stat = os.stat(plan_path)
    if stat.st_size != artifact["size"] or sha256_file(plan_path) != artifact["sha256"]:
        raise ContractError("beat-sync plan fingerprint mismatch")
    plan = _read_object(plan_path)
    if plan.get("job_id") != manifest.get("job_id"):
        raise ContractError("plan job_id mismatch")
    inputs = manifest.get("inputs", [])
    if not isinstance(inputs, list) or not inputs:
        raise ContractError("job inputs are missing")
    for expected in inputs:
        path = os.path.realpath(expected["path"])
        try:
            current = os.stat(path)
        except OSError as exc:
            raise ContractError("input missing: {0}".format(path))
        if (
            current.st_size != expected["size"]
            or current.st_mtime_ns != expected["mtime_ns"]
            or sha256_file(path) != expected["sha256"]
        ):
            raise ContractError("input changed: {0}".format(path))
    _validate_plan(plan, len(inputs))
    return {
        "root": root,
        "manifest": manifest,
        "plan": plan,
        "inputs": inputs,
    }
~~~

- [ ] **Step 5: Run contract and compatibility tests**

Run: `uv run pytest tests/resolve_adapter/test_contract.py tests/resolve_adapter/test_compatibility.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

~~~powershell
git add resolve_adapter tests/resolve_adapter/test_contract.py tests/resolve_adapter/test_compatibility.py
git commit -m "feat: validate jobs in Resolve adapter"
~~~

### Task 2: Application State and Atomic Job Records

**Files:**
- Create: `resolve_adapter/minoru_studio_resolve/state.py`
- Create: `resolve_adapter/minoru_studio_resolve/job_io.py`
- Test: `tests/resolve_adapter/test_state.py`
- Test: `tests/resolve_adapter/test_job_io.py`

**Interfaces:**
- Produces: state constants, `transition(detail, next_state)`, and `next_action(detail)`.
- Produces: Python 3.6 `JobLock` compatible with the Phase 1 mutex name.
- Produces: `ApplicationStore.create`, `load`, `latest`, `update`, `claim`,
  `release`, and `fail`.

- [ ] **Step 1: Write failing state tests**

~~~python
import pytest

from resolve_adapter.minoru_studio_resolve.state import (
    StateError,
    next_action,
    transition,
)


def test_waiting_states_resume_and_terminal_states_reject_reuse():
    detail = {"state": "staging", "operation_token": None}
    transition(detail, "awaiting_in_out")
    assert next_action(detail) == "resume_in_out"
    transition(detail, "ready")
    transition(detail, "applying")
    transition(detail, "applied")
    with pytest.raises(StateError, match="terminal"):
        transition(detail, "staging")


def test_cancel_keeps_ready_state():
    assert next_action({"state": "ready", "operation_token": None}) == "apply"
~~~

- [ ] **Step 2: Write failing atomic-record tests**

~~~python
import json

import pytest

from minoru_studio.jobs.model import JobMode
from minoru_studio.jobs.store import JobStore
from resolve_adapter.minoru_studio_resolve.job_io import (
    ApplicationBusy,
    ApplicationStore,
)


def test_create_writes_detail_and_manifest_summary(tmp_path):
    job_dir = JobStore().create(tmp_path, "demo", JobMode.BEAT_SYNC)
    store = ApplicationStore()
    detail = store.create(
        str(job_dir),
        "job-id",
        "project-id",
        "Project",
        attempt_id="attempt-1",
        now="2026-07-18T00:00:00+00:00",
    )
    assert detail["state"] == "staging"
    saved = json.loads(
        (job_dir / "resolve" / "applications" / "attempt-1.json").read_text()
    )
    assert saved["attempt_id"] == "attempt-1"
    manifest = json.loads((job_dir / "job.json").read_text())
    assert manifest["resolve_applications"][0]["state"] == "staging"


def test_claim_refuses_second_operation(tmp_path):
    job_dir = JobStore().create(tmp_path, "demo", JobMode.BEAT_SYNC)
    store = ApplicationStore()
    store.create(str(job_dir), "job", "project", "P", attempt_id="a", now="t")
    store.claim(str(job_dir), "a", "token-1")
    with pytest.raises(ApplicationBusy):
        store.claim(str(job_dir), "a", "token-2")
~~~

- [ ] **Step 3: Verify failure**

Run: `uv run pytest tests/resolve_adapter/test_state.py tests/resolve_adapter/test_job_io.py -q`

Expected: missing state/job I/O modules.

- [ ] **Step 4: Implement legal transitions**

Create `state.py`:

~~~python
class StateError(ValueError):
    pass


TERMINAL_STATES = frozenset(("applied", "failed"))
ALLOWED = {
    "staging": frozenset(("awaiting_in_out", "checking_still", "failed")),
    "awaiting_in_out": frozenset(("checking_still", "ready", "failed")),
    "checking_still": frozenset(("awaiting_still_setting", "ready", "failed")),
    "awaiting_still_setting": frozenset(("checking_still", "failed")),
    "ready": frozenset(("applying", "failed")),
    "applying": frozenset(("applied", "failed")),
}


def transition(detail, next_state):
    current = detail["state"]
    if current in TERMINAL_STATES:
        raise StateError("terminal application cannot transition")
    if next_state not in ALLOWED.get(current, ()):
        raise StateError("illegal transition: {0} -> {1}".format(current, next_state))
    detail["state"] = next_state
    return detail


def next_action(detail):
    state = detail["state"]
    return {
        "staging": "stage",
        "awaiting_in_out": "resume_in_out",
        "checking_still": "check_still",
        "awaiting_still_setting": "retry_still",
        "ready": "apply",
        "applying": "recover_failed",
        "applied": "new_attempt",
        "failed": "new_attempt",
    }[state]
~~~

- [ ] **Step 5: Implement atomic application storage**

In `job_io.py` implement:

1. `JobLock` using `ctypes.WinDLL("kernel32", use_last_error=True)`, the exact mutex name `Local\minoru-studio-job-<sha256(casefold(realpath(job_dir)))>`, zero-timeout `WaitForSingleObject`, and a per-module held-name set;
2. lock metadata `{"pid": os.getpid(), "token": uuid}` written atomically to `job.lock` and removed only when the token still matches;
3. `_atomic_json(path, payload)` using a same-directory UUID temporary and `os.replace`;
4. `ApplicationStore` methods that lock, reread `job.json`, write the detail first, then update the matching compact summary and `updated_at`.

Use this exact detail/summary shape in `create`:

~~~python
detail = {
    "schema_version": 1,
    "attempt_id": attempt_id,
    "job_id": job_id,
    "state": "staging",
    "project_id": project_id,
    "project_name": project_name,
    "created_at": now,
    "updated_at": now,
    "operation_token": None,
    "bin": None,
    "items": [],
    "source_windows": [],
    "still": None,
    "timeline": None,
    "result": None,
    "last_error": None,
}
summary = {
    "attempt_id": attempt_id,
    "state": "staging",
    "detail_path": "resolve/applications/{0}.json".format(attempt_id),
    "project_id": project_id,
    "updated_at": now,
}
~~~

`claim` sets `operation_token` only when absent and returns the updated detail.
`release` clears only the matching token. `fail` clears the token, transitions
any nonterminal state to `failed`, and stores a short error. A discovered
leftover token means the previous invocation was interrupted:
`mark_interrupted_failed` must terminally fail that attempt before a new
attempt can be created. `latest(job_dir, project_id, states=None)` reconciles
summaries and returns the newest matching detail.

Create `resolve/applications` before the first detail write. Add
`reconcile(job_dir)`, which reads every summary's contained detail path and
repairs only stale summary `state`/`updated_at` fields under the same lock.
Call reconciliation before selecting the latest attempt so a crash between the
detail write and manifest write is recoverable.

- [ ] **Step 6: Run state/storage tests**

Run: `uv run pytest tests/resolve_adapter/test_state.py tests/resolve_adapter/test_job_io.py tests/jobs/test_lock.py -q`

Expected: all selected tests pass, including mutual exclusion with the Phase 1 lock.

- [ ] **Step 7: Commit**

~~~powershell
git add resolve_adapter/minoru_studio_resolve/state.py resolve_adapter/minoru_studio_resolve/job_io.py tests/resolve_adapter/test_state.py tests/resolve_adapter/test_job_io.py
git commit -m "feat: persist Resolve application state"
~~~

### Task 3: Rational Placement Math

**Files:**
- Create: `resolve_adapter/minoru_studio_resolve/placement.py`
- Test: `tests/resolve_adapter/test_placement.py`

**Interfaces:**
- Produces: `parse_rate`, `milliseconds_to_frame`, `timeline_to_source_frames`.
- Produces: `convert_cut_points`, `source_window`, `typical_still_target`, `fit_steps`.

- [ ] **Step 1: Write failing math tests**

~~~python
import pytest

from resolve_adapter.minoru_studio_resolve.placement import (
    convert_cut_points,
    fit_steps,
    milliseconds_to_frame,
    parse_rate,
    source_window,
    typical_still_target,
)


def test_fractional_rates_and_df_suffix_are_exact():
    assert parse_rate("23.976") == (24000, 1001)
    assert parse_rate("29.97 DF") == (30000, 1001)
    assert milliseconds_to_frame(1_000, (24000, 1001)) == 24


def test_duplicate_frame_conversion_is_rejected():
    with pytest.raises(ValueError, match="strictly increasing"):
        convert_cut_points([0, 1, 1000], (24, 1))


def test_marks_are_converted_to_exclusive_end():
    assert source_window({"video": {"in": 10, "out": 19}}, 100) == (10, 20)
    assert source_window({}, 100) == (0, 100)


def test_still_target_excludes_outro_and_uses_first_mode_tie():
    points = [0, 12, 24, 36, 96]
    assert typical_still_target(points, 1) == 12


def test_short_source_reduces_requested_beats():
    points = [0, 12, 24, 36]
    assert fit_steps(points, 0, 3, available_timeline_frames=25) == 2
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/resolve_adapter/test_placement.py -q`

Expected: missing placement module.

- [ ] **Step 3: Implement rational math**

Create `placement.py` with only integer arithmetic:

~~~python
import re


KNOWN_RATES = {
    "23.976": (24000, 1001),
    "24": (24, 1),
    "25": (25, 1),
    "29.97": (30000, 1001),
    "30": (30, 1),
    "50": (50, 1),
    "59.94": (60000, 1001),
    "60": (60, 1),
}


def _round_nonnegative(numerator, denominator):
    return (numerator + denominator // 2) // denominator


def parse_rate(value):
    label = re.sub(r"\s+DF$", "", str(value).strip(), flags=re.IGNORECASE)
    if label not in KNOWN_RATES:
        raise ValueError("unsupported frame rate: {0}".format(value))
    return KNOWN_RATES[label]


def milliseconds_to_frame(time_ms, rate):
    if isinstance(time_ms, bool) or not isinstance(time_ms, int) or time_ms < 0:
        raise ValueError("time_ms must be non-negative integer")
    return _round_nonnegative(time_ms * rate[0], 1000 * rate[1])


def timeline_to_source_frames(timeline_frames, timeline_rate, source_rate):
    return max(
        1,
        _round_nonnegative(
            timeline_frames * source_rate[0] * timeline_rate[1],
            source_rate[1] * timeline_rate[0],
        ),
    )


def convert_cut_points(points_ms, rate):
    points = [milliseconds_to_frame(value, rate) for value in points_ms]
    if any(left >= right for left, right in zip(points, points[1:])):
        raise ValueError("frame cut points must be strictly increasing")
    return points


def source_window(marks, total_frames):
    video = (marks or {}).get("video") or (marks or {}).get("audio") or {}
    start = max(0, min(int(video.get("in", 0)), total_frames - 1))
    end = max(start + 1, min(int(video.get("out", total_frames - 1)) + 1, total_frames))
    return start, end


def typical_still_target(points, every_n):
    interval_count = len(points) - 1
    if interval_count > every_n:
        starts = range(max(1, interval_count - every_n))
        lengths = [points[index + every_n] - points[index] for index in starts]
    else:
        lengths = [points[-1] - points[0]]
    counts = {}
    for length in lengths:
        counts[length] = counts.get(length, 0) + 1
    return max(lengths, key=lambda value: counts[value])


def fit_steps(points, start_index, requested, available_timeline_frames):
    steps = min(requested, len(points) - 1 - start_index)
    while steps >= 1:
        if points[start_index + steps] - points[start_index] <= available_timeline_frames:
            return steps
        steps -= 1
    return 0
~~~

- [ ] **Step 4: Run math tests**

Run: `uv run pytest tests/resolve_adapter/test_placement.py -q`

Expected: `5 passed`.

- [ ] **Step 5: Commit**

~~~powershell
git add resolve_adapter/minoru_studio_resolve/placement.py tests/resolve_adapter/test_placement.py
git commit -m "feat: calculate Resolve placement frames"
~~~

### Task 4: Resolve Gateway and First-Stage Import

**Files:**
- Create: `resolve_adapter/minoru_studio_resolve/gateway.py`
- Create: `resolve_adapter/minoru_studio_resolve/service.py`
- Create: `tests/resolve_adapter/fakes.py`
- Create: `tests/resolve_adapter/conftest.py`
- Test: `tests/resolve_adapter/test_staging.py`

**Interfaces:**
- Produces: `ResolveGateway` as the sole raw Resolve API boundary.
- Produces: `AdapterService.start(job_dir, new_attempt=False) -> dict`.
- Consumes: validated contract, `ApplicationStore`, and placement math.

- [ ] **Step 1: Create deterministic fake Resolve objects and failing staging tests**

`tests/resolve_adapter/fakes.py` must define fake Resolve, project manager, project, media pool, folder, MediaPoolItem, timeline, and TimelineItem objects. Each fake records method calls and returns stable IDs. Implement the exact methods used by the gateway: `GetProjectManager`, `GetCurrentProject`, `GetUniqueId`, `GetName`, `GetMediaPool`, `GetRootFolder`, `AddSubFolder`, `SetCurrentFolder`, `ImportMedia`, `GetClipList`, `GetSubFolderList`, `CreateEmptyTimeline`, `DeleteTimelines`, `AppendToTimeline`, `GetSetting`, `GetStartFrame`, `DeleteClips`, `AddMarker`, `GetClipProperty`, `GetMarkInOut`, and `GetDuration`.

Create an empty `tests/resolve_adapter/__init__.py` and create
`tests/resolve_adapter/conftest.py`:

~~~python
import pytest

from minoru_studio.beat_sync.models import (
    BeatAnalysis,
    BeatSyncPlan,
    BeatSyncSettings,
    MaterialKind,
    MaterialPlan,
    save_plan,
)
from minoru_studio.jobs.model import JobMode, JobStatus
from minoru_studio.jobs.store import JobStore, fingerprint_artifact


def build_prepared_job(tmp_path, kinds):
    music = tmp_path / "song.wav"
    music.write_bytes(b"audio")
    paths = []
    for index, kind in enumerate(kinds, start=1):
        suffix = ".jpg" if kind is MaterialKind.PHOTO else ".mov"
        path = tmp_path / ("material-{0}{1}".format(index, suffix))
        path.write_bytes(kind.value.encode("ascii"))
        paths.append(path)
    store = JobStore()
    job_dir = store.create(
        tmp_path / "jobs",
        "demo",
        JobMode.BEAT_SYNC,
        [music] + paths,
    )
    manifest = store.load(job_dir, recover_interrupted=False)
    plan = BeatSyncPlan(
        1,
        manifest.job_id,
        0,
        BeatAnalysis(
            2_000,
            120.0,
            (500, 1_000, 1_500),
            (0, 500, 1_000, 1_500, 2_000),
        ),
        BeatSyncSettings("auto", 1, "asc", "Demo"),
        tuple(
            MaterialPlan(index + 1, kind, index)
            for index, kind in enumerate(kinds)
        ),
    )
    plan_path = job_dir / "outputs" / "beat-sync-plan.json"
    save_plan(plan_path, plan)
    artifact = fingerprint_artifact(job_dir, plan_path, "beat-sync-plan")

    def finish(latest):
        latest.status = JobStatus.SUCCEEDED
        latest.artifacts = [artifact]

    store.update(job_dir, finish)
    return job_dir


@pytest.fixture
def prepared_photo_job(tmp_path):
    return build_prepared_job(tmp_path, (MaterialKind.PHOTO,))


@pytest.fixture
def prepared_mixed_job(tmp_path):
    return build_prepared_job(
        tmp_path,
        (MaterialKind.PHOTO, MaterialKind.VIDEO),
    )
~~~

The fake project starts with a separate sentinel current timeline. Its
`timelines` collection records only tool-created timelines. The fake media pool
defaults a probe TimelineItem's duration to the requested append duration, but
accepts an explicit `still_duration` override for mismatch tests.

Write:

~~~python
def test_stage_imports_photos_individually_and_waits_for_video(prepared_mixed_job):
    resolve = FakeResolve()
    service = AdapterService(
        ResolveGateway(resolve),
        ApplicationStore(),
        attempt_ids=iter(["attempt-1"]),
        operation_tokens=iter(["operation-1"]),
    )
    detail = service.start(str(prepared_mixed_job))
    assert detail["state"] == "awaiting_in_out"
    assert resolve.project.media_pool.photo_import_batch_sizes == [1]
    assert detail["bin"]["id"]
    assert len(detail["items"]) == 3
    assert not resolve.project.timelines


def test_photo_only_job_continues_to_checking_still(prepared_photo_job):
    resolve = FakeResolve()
    service = AdapterService(
        ResolveGateway(resolve),
        ApplicationStore(),
        attempt_ids=iter(["attempt-1"]),
        operation_tokens=iter(["operation-1"]),
    )
    detail = service.start(str(prepared_photo_job), stop_after_stage=True)
    assert detail["state"] == "checking_still"
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/resolve_adapter/test_staging.py -q`

Expected: missing gateway/service/fakes.

- [ ] **Step 3: Implement the gateway**

`ResolveGateway` must:

1. require a current project and return its ID/name;
2. create a unique application bin under the media-pool root and make it current;
3. import video paths as one list, photos as one call per path, and BGM separately;
4. normalize `GetClipProperty("File Path")` with `realpath/normcase` and match every returned item to one expected input;
5. expose item metadata as plain dictionaries containing input index, kind, item ID, path, frames, FPS; input index 0 uses kind `audio` and plan materials use `photo`/`video`;
6. traverse the recorded bin and find items by `GetUniqueId` on resume;
7. reject missing/duplicate/ambiguous imports.

Use `ImportMedia([photo_path])` for every photo and require exactly one BGM return value.
Also expose `product_info()` from `resolve.GetProductName()` and
`resolve.GetVersionString()`.

- [ ] **Step 4: Implement start/staging orchestration**

`AdapterService.start` must execute this exact order:

~~~python
validated = load_validated_job(job_dir)
project = gateway.current_project()
detail = applications.create(
    validated["root"],
    validated["manifest"]["job_id"],
    project["id"],
    project["name"],
    attempt_id=next(attempt_ids),
    now=utc_now(),
)
token = next(operation_tokens)
detail = applications.claim(
    validated["root"],
    detail["attempt_id"],
    token,
)
try:
    detail["resolve"] = gateway.product_info()
    detail["bin"] = gateway.create_application_bin(
        validated["plan"]["settings"]["timeline_name"],
        detail["attempt_id"],
    )
    detail["items"] = gateway.import_inputs(validated, detail["bin"])
    has_video = any(item["kind"] == "video" for item in detail["items"])
    has_photo = any(item["kind"] == "photo" for item in detail["items"])
    transition(
        detail,
        "awaiting_in_out" if has_video else "checking_still",
    )
    applications.update(validated["root"], detail)
finally:
    applications.release(validated["root"], detail["attempt_id"], token)
~~~

If the latest same-project attempt is terminal, `start` requires
`new_attempt=True`. If it is resumable, `start` rejects creation and directs the
caller to `resume`. With no prior attempt, the default `False` starts normally.

Wrap exceptions: save the detail as `failed`, retain the bin, release the matching token, and re-raise a concise `AdapterError`. `stop_after_stage` is test-only injection that returns at `checking_still`; production immediately calls the still-check method added in Task 5.

- [ ] **Step 5: Run staging tests**

Run: `uv run pytest tests/resolve_adapter/test_staging.py tests/resolve_adapter/test_compatibility.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

~~~powershell
git add resolve_adapter/minoru_studio_resolve/gateway.py resolve_adapter/minoru_studio_resolve/service.py tests/resolve_adapter/fakes.py tests/resolve_adapter/test_staging.py
git commit -m "feat: stage beat-sync media in Resolve"
~~~

### Task 5: In/Out Resume and Strict Still Probe

**Files:**
- Modify: `resolve_adapter/minoru_studio_resolve/gateway.py`
- Modify: `resolve_adapter/minoru_studio_resolve/service.py`
- Test: `tests/resolve_adapter/test_resume_and_still.py`

**Interfaces:**
- Produces: `AdapterService.resume(job_dir) -> dict`.
- Gateway produces: `read_source_windows`, `probe_still`, and `reimport_photos`.
- Consumes: `parse_rate`, `convert_cut_points`, `source_window`, and `typical_still_target`.

- [ ] **Step 1: Write failing two-stage and still tests**

~~~python
from resolve_adapter.minoru_studio_resolve.gateway import ResolveGateway
from resolve_adapter.minoru_studio_resolve.job_io import ApplicationStore
from resolve_adapter.minoru_studio_resolve.service import AdapterService
from tests.resolve_adapter.fakes import FakeResolve


def make_service(resolve):
    return AdapterService(
        ResolveGateway(resolve),
        ApplicationStore(),
        attempt_ids=iter(["attempt-1"]),
        operation_tokens=iter(["operation-1", "operation-2", "operation-3"]),
    )


def test_mixed_job_reads_marks_on_second_invocation(prepared_mixed_job):
    resolve = FakeResolve()
    service = make_service(resolve)
    service.start(str(prepared_mixed_job))
    video = resolve.project.media_pool.video_items[0]
    video.marks = {"video": {"in": 10, "out": 39}}
    detail = service.resume(str(prepared_mixed_job))
    assert detail["source_windows"][0]["mark_in_frame"] == 10
    assert detail["source_windows"][0]["mark_out_frame_exclusive"] == 40


def test_still_mismatch_deletes_only_probe_and_waits(prepared_photo_job):
    resolve = FakeResolve(still_duration=60)
    service = make_service(resolve)
    detail = service.start(str(prepared_photo_job))
    assert detail["state"] == "awaiting_still_setting"
    assert detail["still"]["actual_frames"] == 60
    assert detail["still"]["required_frames"] != 60
    assert resolve.project.deleted_timeline_ids == ["probe-attempt-1"]
    assert not resolve.project.final_timeline_ids
    assert resolve.project.current_timeline_id == "sentinel"


def test_still_retry_reimports_only_photos(prepared_mixed_job):
    resolve = FakeResolve(still_duration=60)
    service = make_service(resolve)
    service.start(str(prepared_mixed_job))
    first = service.resume(str(prepared_mixed_job))
    assert first["state"] == "awaiting_still_setting"
    resolve.project.media_pool.still_duration = first["still"]["required_frames"]
    second = service.resume(str(prepared_mixed_job))
    assert second["state"] == "ready"
    assert resolve.project.media_pool.photo_import_batch_sizes == [1, 1]
    assert resolve.project.media_pool.video_import_calls == 1
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/resolve_adapter/test_resume_and_still.py -q`

Expected: missing resume/probe APIs.

- [ ] **Step 3: Implement source-window resume**

`AdapterService.resume` must:

1. validate the job again;
2. select the latest summary with the same project ID and state in `awaiting_in_out`, `awaiting_still_setting`, or `ready`;
3. reject `failed`/`applied` and fail a detail that still has an operation token;
4. claim a fresh operation token;
5. verify the recorded bin ID and every MediaPoolItem ID still exist;
6. for `awaiting_in_out`, read `Frames`, `FPS`, and `GetMarkInOut()` for each video;
7. persist source FPS as numerator/denominator and marks with an exclusive Out;
8. persist `timeline_rate` from the project setting for video-only jobs;
9. transition to `checking_still` when photos exist, otherwise `ready`.

Use the recorded project ID, not project name, as the resume identity. Invalid frame count, FPS, or empty mark window fails before creating a timeline.
Assign `detail = applications.claim(...)` before Resolve calls so every
intermediate `update` retains the logical operation token; release that exact
token in `finally`.

- [ ] **Step 4: Implement the exact probe lifecycle**

Add `ResolveGateway.probe_still(photo_item, cut_points_ms, every_n, attempt_id)`.
Capture `previous = project.GetCurrentTimeline()` before creating the probe:

~~~python
probe = media_pool.CreateEmptyTimeline(
    "_MinoruStudio Probe {0}".format(attempt_id)
)
if not probe:
    raise AdapterError("cannot create still probe timeline")
if not project.SetCurrentTimeline(probe):
    raise AdapterError("cannot activate still probe timeline")
probe_id = probe.GetUniqueId()
try:
    rate = parse_rate(
        probe.GetSetting("timelineFrameRate")
        or project.GetSetting("timelineFrameRate")
    )
    points = convert_cut_points(cut_points_ms, rate)
    required = typical_still_target(points, every_n)
    base = int(probe.GetStartFrame())
    result = media_pool.AppendToTimeline([{
        "mediaPoolItem": photo_item,
        "startFrame": 0,
        "endFrame": required * 2 - 1,
        "mediaType": 1,
        "trackIndex": 1,
        "recordFrame": base,
    }])
    if not isinstance(result, list) or not result:
        raise AdapterError("cannot place still probe")
    actual = int(result[0].GetDuration())
    if not probe.DeleteClips([result[0]], False):
        raise AdapterError("cannot delete still probe clip")
finally:
    try:
        if not media_pool.DeleteTimelines([probe]):
            raise AdapterError(
                "cannot delete probe timeline {0} ({1})".format(
                    probe_id,
                    probe.GetName(),
                )
            )
    finally:
        if previous is not None and not project.SetCurrentTimeline(previous):
            raise AdapterError("cannot restore timeline after still probe")
return {
    "rate": {"numerator": rate[0], "denominator": rate[1]},
    "required_frames": required,
    "actual_frames": actual,
}
~~~

The fake must assert that `DeleteTimelines` received the same object returned by `CreateEmptyTimeline`, not a name lookup result.

- [ ] **Step 5: Implement mismatch and retry behavior**

After probing:

~~~python
detail["still"] = measurement
if abs(measurement["actual_frames"] - measurement["required_frames"]) > 1:
    transition(detail, "awaiting_still_setting")
else:
    transition(detail, "ready")
applications.update(job_root, detail)
~~~

On `awaiting_still_setting` resume, create a unique photo retry sub-bin beneath the application bin and import each photo once into it. Replace only photo item IDs in `detail["items"]`; retain video items and stored windows. Re-probe the newly imported first photo.

- [ ] **Step 6: Run resume/probe tests**

Run: `uv run pytest tests/resolve_adapter/test_resume_and_still.py tests/resolve_adapter/test_placement.py -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

~~~powershell
git add resolve_adapter/minoru_studio_resolve/gateway.py resolve_adapter/minoru_studio_resolve/service.py tests/resolve_adapter/test_resume_and_still.py
git commit -m "feat: resume Resolve jobs after user setup"
~~~

### Task 6: Final Timeline Placement and Verification

**Files:**
- Modify: `resolve_adapter/minoru_studio_resolve/gateway.py`
- Modify: `resolve_adapter/minoru_studio_resolve/placement.py`
- Modify: `resolve_adapter/minoru_studio_resolve/service.py`
- Test: `tests/resolve_adapter/test_apply.py`

**Interfaces:**
- Produces: `AdapterService.apply_ready(job_dir, confirm) -> dict`.
- Produces: `AdapterService.latest_detail(job_dir) -> dict` for refresh/error
  reporting.
- Produces: unique timeline naming, BGM/V1 placement, one video correction, markers, and result report.
- Consumes: ready attempt, validated item IDs/source windows, rational frame helpers.

- [ ] **Step 1: Write failing apply tests**

~~~python
import pytest

from resolve_adapter.minoru_studio_resolve.gateway import ResolveGateway
from resolve_adapter.minoru_studio_resolve.job_io import ApplicationStore
from resolve_adapter.minoru_studio_resolve.service import AdapterError, AdapterService
from tests.resolve_adapter.fakes import FakeResolve


def make_service(resolve):
    return AdapterService(
        ResolveGateway(resolve),
        ApplicationStore(),
        attempt_ids=iter(["attempt-1", "attempt-2"]),
        operation_tokens=iter([
            "operation-1", "operation-2", "operation-3", "operation-4",
            "operation-5", "operation-6", "operation-7", "operation-8",
        ]),
    )


@pytest.fixture
def ready_mixed_service(prepared_mixed_job):
    resolve = FakeResolve()
    service = make_service(resolve)
    service.start(str(prepared_mixed_job))
    detail = service.resume(str(prepared_mixed_job))
    assert detail["state"] == "ready"
    return resolve, service, prepared_mixed_job


@pytest.fixture
def applied_service(ready_mixed_service):
    resolve, service, job_dir = ready_mixed_service
    service.apply_ready(str(job_dir), confirm=lambda summary: True)
    return resolve, service, job_dir


def test_cancel_keeps_ready_and_creates_no_final_timeline(ready_mixed_service):
    resolve, service, job_dir = ready_mixed_service
    detail = service.apply_ready(str(job_dir), confirm=lambda summary: False)
    assert detail["state"] == "ready"
    assert not resolve.project.final_timeline_ids


def test_apply_places_bgm_visuals_and_blue_markers(ready_mixed_service):
    resolve, service, job_dir = ready_mixed_service
    detail = service.apply_ready(str(job_dir), confirm=lambda summary: True)
    assert detail["state"] == "applied"
    timeline = resolve.project.final_timelines[0]
    assert timeline.audio_track_items[1]
    assert timeline.video_track_items[1]
    assert all(marker["color"] == "Blue" for marker in timeline.markers)
    assert detail["result"]["placed"] >= 1


def test_reapply_uses_unique_timeline_name(applied_service):
    resolve, service, job_dir = applied_service
    service.start(str(job_dir), new_attempt=True)
    second = service.resume(str(job_dir))
    assert second["state"] == "ready"
    service.apply_ready(str(job_dir), confirm=lambda summary: True)
    assert [timeline.GetName() for timeline in resolve.project.final_timelines] == [
        "Demo",
        "Demo-002",
    ]


def test_mid_apply_failure_keeps_partial_timeline_and_fails_attempt(ready_mixed_service):
    resolve, service, job_dir = ready_mixed_service
    resolve.project.media_pool.fail_visual_append_after = 1
    with pytest.raises(AdapterError):
        service.apply_ready(str(job_dir), confirm=lambda summary: True)
    detail = service.latest_detail(str(job_dir))
    assert detail["state"] == "failed"
    assert detail["timeline"]["id"] in resolve.project.final_timeline_ids
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/resolve_adapter/test_apply.py -q`

Expected: missing final-apply behavior.

- [ ] **Step 3: Add pure placement helpers**

Add to `placement.py`:

~~~python
def source_frames_available(window, source_rate, timeline_rate):
    source_length = window[1] - window[0]
    return _round_nonnegative(
        source_length * timeline_rate[0] * source_rate[1],
        timeline_rate[1] * source_rate[0],
    )


def corrected_source_length(
    current_source_frames,
    target_timeline_frames,
    actual_timeline_frames,
    timeline_rate,
    source_rate,
):
    difference = target_timeline_frames - actual_timeline_frames
    correction = _round_nonnegative(
        abs(difference) * source_rate[0] * timeline_rate[1],
        source_rate[1] * timeline_rate[0],
    )
    correction = max(1, correction)
    return max(
        1,
        current_source_frames + (correction if difference > 0 else -correction),
    )
~~~

Keep one cursor per video input index. Start at mark In, advance after every use, and wrap to mark In when the next segment would pass exclusive Out.

- [ ] **Step 4: Implement unique creation and required BGM**

The gateway enumerates `project.GetTimelineByIndex(1..GetTimelineCount())` and chooses the requested name or the first available `-002`, `-003` suffix. It then:

1. creates the final timeline;
2. records ID/name immediately in the detail before adding clips;
3. reads and verifies its actual `timelineFrameRate` against the probe/application rate;
4. appends BGM with `mediaType=2`, `trackIndex=1` at `timeline.GetStartFrame()`;
5. treats a false/empty BGM result as fatal.

- [ ] **Step 5: Implement the visual loop and one correction**

For each current cut index:

1. request at most `every_n_resolved` intervals;
2. walk persisted materials from the current order cursor;
3. photos fit when measured still length can cover the target;
4. videos use `fit_steps` against their selected window and source FPS;
5. if no material fits one interval, record a gap and advance one cut interval;
6. append on V1 with `mediaType=1`, `trackIndex=1` and `recordFrame=base + points[index]`;
7. use inclusive `endFrame = startFrame + source_frames - 1`;
8. measure `TimelineItem.GetDuration()`;
9. for a video mismatch greater than one timeline frame, delete only that new TimelineItem with `timeline.DeleteClips([item], False)` and append once using `corrected_source_length`;
10. retain and report any remaining mismatch;
11. add `timeline.AddMarker(points[index], "Blue", "beat", "", 1, custom_data)` only after successful visual placement;
12. advance by the number of intervals actually placed.

`custom_data` is `minoru-studio:<job-id>:<attempt-id>`. Record placed/failed/gaps, corrections, per-input usage, unused inputs, and mismatches.

- [ ] **Step 6: Implement final state semantics**

Before mutation, present a summary containing BPM, interval, approximate cut
length, duration, material counts, still required/actual, and proposed name.
False confirmation leaves `ready` and does not claim operation ownership.

After true confirmation, assign the detail returned by
`applications.claim(...)`, transition `ready -> applying`, persist it, and only
then create the timeline. Transition to `applied` only when:

- BGM was placed;
- final timeline exists;
- at least one visual was placed;
- the detailed result and compact manifest summary were written.

Any exception after timeline creation marks the attempt `failed` and preserves the partial timeline/bin.

Implement refresh/error lookup without mutating Resolve:

~~~python
def latest_detail(self, job_dir):
    validated = load_validated_job(job_dir)
    project = self.gateway.current_project()
    detail = self.applications.latest(
        validated["root"],
        project["id"],
    )
    if detail is None:
        raise AdapterError("no Resolve application exists for this project")
    return detail
~~~

- [ ] **Step 7: Run apply tests and all fake-Resolve tests**

Run: `uv run pytest tests/resolve_adapter -q`

Expected: every adapter unit/fake test passes.

- [ ] **Step 8: Commit**

~~~powershell
git add resolve_adapter/minoru_studio_resolve/gateway.py resolve_adapter/minoru_studio_resolve/placement.py resolve_adapter/minoru_studio_resolve/service.py tests/resolve_adapter/test_apply.py
git commit -m "feat: create beat-synced Resolve timelines"
~~~

### Task 7: Tkinter Entry, Utility Launcher, and Installer

**Files:**
- Create: `resolve_adapter/minoru_studio_resolve/ui.py`
- Create: `resolve_adapter/minoru_studio_resolve/entry.py`
- Create: `scripts/MinoruStudio.py`
- Modify: `install.ps1`
- Test: `tests/resolve_adapter/test_entry.py`
- Test: `tests/resolve_adapter/test_install.py`

**Interfaces:**
- Produces: one Tkinter primary action based on `next_action`.
- Produces: `entry.run(resolve_globals)`.
- Installer copies adapter to `%LOCALAPPDATA%\MinoruStudio\resolve_adapter` and launcher to Resolve Utility scripts.

- [ ] **Step 1: Write failing entry and installer tests**

~~~python
def test_entry_requires_resolve_global(monkeypatch):
    messages = []
    monkeypatch.setattr(
        "resolve_adapter.minoru_studio_resolve.entry.show_error",
        lambda title, text: messages.append((title, text)),
    )
    assert entry.run({}) == 2
    assert "Resolve" in messages[0][1]


def test_entry_passes_resolve_to_ui(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "resolve_adapter.minoru_studio_resolve.entry.launch",
        lambda resolve: calls.append(resolve),
    )
    marker = object()
    assert entry.run({"resolve": marker}) == 0
    assert calls == [marker]
~~~

Installer test:

~~~python
import subprocess


def test_installer_can_copy_to_disposable_roots(tmp_path):
    app_root = tmp_path / "app"
    utility = tmp_path / "utility"
    result = subprocess.run(
        [
            "pwsh", "-NoProfile", "-File", "install.ps1",
            "-AppRoot", str(app_root),
            "-ResolveScriptsRoot", str(utility),
            "-SkipSync",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (app_root / "resolve_adapter" / "minoru_studio_resolve" / "entry.py").is_file()
    assert (utility / "MinoruStudio.py").is_file()
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/resolve_adapter/test_entry.py tests/resolve_adapter/test_install.py -q`

Expected: missing UI/entry and old installer behavior failures.

- [ ] **Step 3: Implement the Tkinter state UI**

`ui.launch(resolve)` creates one topmost, non-resizable window with:

- a read-only job-path entry and `.media-job を選択` directory picker;
- job name, preparation state, latest application state, and current project;
- a status/log summary label;
- one primary button whose text is selected from `素材を取り込む`, `In/Out設定後に再開`, `スチル設定変更後に再測定`, or `タイムラインを生成`;
- `新しい適用を開始` only when the latest same-project attempt is terminal;
- final confirmation through `messagebox.askokcancel`;
- concise errors through `messagebox.showerror`.

The primary callback calls `AdapterService.start`, `resume`, or `apply_ready` according to `next_action`, refreshes from disk after every call, and never runs a second operation while the button is disabled.

When an action returns `awaiting_in_out` or `awaiting_still_setting`, show the
exact next instruction and destroy the Tk root so Resolve becomes interactive.
Persist only the last selected job path in
`%APPDATA%\MinoruStudio\resolve-adapter.json` and preselect it on the next
Utility-menu invocation. A photo-only result that reaches `ready` stays in the
same invocation and changes the primary button to `タイムラインを生成`.

- [ ] **Step 4: Implement entry and launcher**

`entry.py`:

~~~python
from minoru_studio_resolve.ui import launch, show_error


def run(resolve_globals):
    resolve = resolve_globals.get("resolve")
    if resolve is None:
        show_error(
            "MinoruStudio",
            "DaVinci Resolve のスクリプトメニューから実行してください。",
        )
        return 2
    launch(resolve)
    return 0
~~~

`scripts/MinoruStudio.py` must be Python 3.6-compatible:

~~~python
# -*- coding: utf-8 -*-
import os
import sys
import traceback


app_root = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "MinoruStudio",
    "resolve_adapter",
)
if not os.path.isdir(app_root):
    raise RuntimeError("MinoruStudio Resolve adapter is not installed: " + app_root)
if app_root not in sys.path:
    sys.path.insert(0, app_root)

from minoru_studio_resolve.entry import run


try:
    exit_code = run(globals())
    if exit_code:
        raise RuntimeError("MinoruStudio adapter exited with {0}".format(exit_code))
except Exception:
    traceback.print_exc()
    raise
~~~

- [ ] **Step 5: Replace system-pip installation**

Make `install.ps1` accept:

~~~powershell
[CmdletBinding()]
param(
    [string] $AppRoot = (Join-Path $env:LOCALAPPDATA "MinoruStudio"),
    [string] $ResolveScriptsRoot = (
        Join-Path $env:APPDATA "Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
    ),
    [switch] $SkipSync
)
~~~

With `$ErrorActionPreference = "Stop"`:

1. resolve `uv` with `Get-Command` and fail clearly unless `-SkipSync`;
2. run `uv sync --locked` in `$PSScriptRoot` unless skipped;
3. create `$AppRoot\resolve_adapter` and copy the entire `resolve_adapter\minoru_studio_resolve` directory there;
4. create `$ResolveScriptsRoot` and copy `scripts\MinoruStudio.py`;
5. print both installed paths and the Resolve menu instruction;
6. do not invoke `pip`, delete `MinoruDouga.py`, or modify system Python.

- [ ] **Step 6: Run entry/install/compatibility tests**

Run: `uv run pytest tests/resolve_adapter/test_entry.py tests/resolve_adapter/test_install.py tests/resolve_adapter/test_compatibility.py -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

~~~powershell
git add resolve_adapter/minoru_studio_resolve/ui.py resolve_adapter/minoru_studio_resolve/entry.py scripts/MinoruStudio.py install.ps1 tests/resolve_adapter/test_entry.py tests/resolve_adapter/test_install.py
git commit -m "feat: install MinoruStudio Resolve adapter"
~~~

### Task 8: Automated Gates, Real Resolve Acceptance, and Release Docs

**Files:**
- Create: `docs/resolve-beat-sync-acceptance.md`
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/minoru_studio/__init__.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: repeatable disposable-project acceptance instructions.
- Promotes MinoruStudio beat-sync to version 0.2.0 only after real acceptance.

- [ ] **Step 1: Run all automated gates before installing**

Run: `uv sync --locked --dev`

Expected: exit 0.

Run: `uv run pytest -q`

Expected: every Phase 1, preparation, and adapter test passes.

Run: `uv run python -m compileall -q src resolve_adapter scripts`

Expected: exit 0.

Run: `uv run minoru-studio beat-sync --help`

Expected: exit 0.

Run: `uv run minoru-studio doctor --json`

Expected: valid JSON and all required checks are `ok`.

- [ ] **Step 2: Write the acceptance runbook**

`docs/resolve-beat-sync-acceptance.md` must require an expendable project and record:

1. Resolve product name/version and timeline rate;
2. adapter installation and Utility-menu visibility;
3. a photo-only one-invocation apply;
4. a mixed photo/video stage, In-only and In/Out marks, then resume;
5. an intentional still mismatch, exact required frame display, setting change, and retry;
6. A1 BGM, V1-only visuals, blue cut markers, and measured ±1-frame tolerance;
7. a second application producing a suffixed timeline;
8. cancel from `ready` leaving no final timeline;
9. an induced fake-only placement failure proving partial objects are retained; do not induce destructive failure in a real project;
10. verification that an existing sentinel timeline and source files are unchanged;
11. the application detail paths and created bin/timeline IDs.

Include a pass/fail table with a notes column and commands for collecting `job.json`, application JSON, and `logs/resolve.log` without including source media.

- [ ] **Step 3: Stop and request explicit authorization for real Resolve mutation**

Do not launch Resolve, install into AppData, or mutate a project automatically. Ask the user to authorize installation and a disposable-project acceptance run. If authorization is not given, leave the release at 0.1.0 and report automated completion only.

- [ ] **Step 4: Install and execute the authorized disposable-project run**

Run: `pwsh -NoProfile -File .\install.ps1`

Expected: uv sync succeeds, the adapter and Utility launcher paths are printed, and the legacy launcher is not removed.

Follow `docs/resolve-beat-sync-acceptance.md`. Every scenario must pass. If a Resolve API return shape differs, stop, capture the local API/version evidence, and return to the relevant task rather than weakening the assertion.

- [ ] **Step 5: Promote README and version only after acceptance**

Change both version declarations from 0.1.0 to 0.2.0:

~~~toml
version = "0.2.0"
~~~

~~~python
__version__ = "0.2.0"
~~~

Update `tests/test_cli.py` to expect `0.2.0`.

Run: `uv lock`

Expected: exit 0 and the root package entry in `uv.lock` reports version 0.2.0.

Rewrite README's primary beat-sync path as:

1. `.\install.ps1`;
2. prepare with CLI or no-argument GUI;
3. open a Resolve project;
4. use `ワークスペース → スクリプト → MinoruStudio`;
5. select the successful job;
6. for videos, run import, set marks, and run resume;
7. correct still length when instructed;
8. confirm and inspect the new timeline.

Keep the full MinoruDouga section under `Legacy fallback` and state that it is not automatically uninstalled.

- [ ] **Step 6: Re-run release gates**

Run: `uv lock --check`

Expected: exit 0.

Run: `uv run pytest -q`

Expected: all tests pass with version 0.2.0.

Run: `git diff --check`

Expected: no output and exit 0.

- [ ] **Step 7: Commit**

~~~powershell
git add docs/resolve-beat-sync-acceptance.md README.md pyproject.toml uv.lock src/minoru_studio/__init__.py tests/test_cli.py
git commit -m "feat: complete beat-sync Resolve workflow"
~~~

## Resolve Adapter Plan Completion Gate

1. Every automated gate passes.
2. Adapter sources pass the Python 3.6/import allowlist test.
3. Fake Resolve tests cover every state and partial-failure path.
4. The disposable Resolve Free acceptance table is entirely passing.
5. Existing sentinel timeline and input hashes are unchanged.
6. Reapplication creates a new timeline rather than reusing one.
7. Version 0.2.0 and README promotion occur only after the real gate.
8. Legacy MinoruDouga files and any installed legacy launcher remain available.
