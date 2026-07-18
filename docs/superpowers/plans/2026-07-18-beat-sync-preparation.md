# Beat Sync Job Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Resolve-independent half of beat-sync: validate inputs, analyze BGM with librosa, persist an integer-millisecond plan, and expose the workflow through CLI and GUI.

**Architecture:** A focused `minoru_studio.beat_sync` package owns media discovery, analysis normalization, the versioned plan contract, and job orchestration. The Phase 1 job store remains the durability boundary; CLI and Tkinter call the same service and contain no media rules.

**Tech Stack:** Python 3.12, librosa 0.11.x, dataclasses, pathlib, Tkinter, pytest, uv.

## Global Constraints

- Follow `docs/superpowers/specs/2026-07-18-beat-sync-end-to-end-design.md`.
- External core Python is exactly `>=3.12,<3.13`.
- Add one direct runtime dependency: `librosa>=0.11,<0.12`. The
  [official PyPI project](https://pypi.org/project/librosa/) identifies 0.11.0
  as the current stable release and supports Python 3.12.
- Persist media times and durations only as non-negative integer milliseconds.
- Never import Resolve APIs from `src/minoru_studio`.
- Never overwrite an existing `.media-job` or a successful beat-sync plan.
- Scan only direct children of the selected media directory.
- Leave `src/minoru_douga.py`, `src/analyze_beats.py`, and `scripts/MinoruDouga.py` unchanged.
- Keep `job.status` scoped to preparation; Resolve state belongs to the adapter plan.
- Use TDD and commit after every task.

---

## File Map

- Create `src/minoru_studio/beat_sync/models.py` for the plan contract.
- Create `src/minoru_studio/beat_sync/media.py` for discovery and ordering.
- Create `src/minoru_studio/beat_sync/analyzer.py` for librosa and millisecond normalization.
- Create `src/minoru_studio/beat_sync/plan.py` for interval and plan assembly.
- Create `src/minoru_studio/beat_sync/service.py` for create/resume orchestration.
- Create `src/minoru_studio/beat_sync/commands.py` and modify `cli.py`.
- Create `src/minoru_studio/beat_sync/settings.py` and modify `gui.py`.
- Modify `jobs/store.py` for contained artifact fingerprints.
- Modify `pyproject.toml` and `uv.lock` for librosa.
- Add focused tests under `tests/beat_sync` and one Resolve-free acceptance test.

### Task 1: Versioned Beat-Sync Plan Contract

**Files:**
- Create: `src/minoru_studio/beat_sync/__init__.py`
- Create: `src/minoru_studio/beat_sync/models.py`
- Test: `tests/beat_sync/test_models.py`

**Interfaces:**
- Produces: `MaterialKind`, `BeatAnalysis`, `BeatSyncSettings`, `MaterialPlan`, `BeatSyncPlan`.
- Produces: `plan_to_dict`, `plan_from_dict`, `save_plan`, and `load_plan`.
- Consumes no other new modules.

- [ ] **Step 1: Write the failing contract tests**

~~~python
import pytest

from minoru_studio.beat_sync.models import (
    BeatAnalysis,
    BeatSyncPlan,
    BeatSyncSettings,
    MaterialKind,
    MaterialPlan,
    PlanError,
    load_plan,
    plan_from_dict,
    plan_to_dict,
    save_plan,
)


def sample_plan():
    return BeatSyncPlan(
        schema_version=1,
        job_id="job-123",
        audio_input_index=0,
        analysis=BeatAnalysis(
            duration_ms=2_000,
            bpm=120.0,
            beats_ms=(500, 1_000, 1_500),
            cut_points_ms=(0, 500, 1_000, 1_500, 2_000),
            minimum_cut_ms=150,
        ),
        settings=BeatSyncSettings("auto", 1, "asc", "Demo"),
        materials=(
            MaterialPlan(1, MaterialKind.PHOTO, 0),
            MaterialPlan(2, MaterialKind.VIDEO, 1),
        ),
    )


def test_plan_round_trip_and_atomic_write(tmp_path):
    plan = sample_plan()
    assert plan_from_dict(plan_to_dict(plan)) == plan
    path = tmp_path / "outputs" / "beat-sync-plan.json"
    save_plan(path, plan)
    assert load_plan(path) == plan
    assert not list(path.parent.glob("*.tmp"))


def test_float_milliseconds_are_rejected():
    data = plan_to_dict(sample_plan())
    data["analysis"]["duration_ms"] = 2_000.0
    with pytest.raises(PlanError, match="duration_ms"):
        plan_from_dict(data)


def test_cut_points_must_span_zero_to_duration():
    data = plan_to_dict(sample_plan())
    data["analysis"]["cut_points_ms"] = [100, 500, 2_000]
    with pytest.raises(PlanError, match="cut_points_ms"):
        plan_from_dict(data)
~~~

- [ ] **Step 2: Verify the missing package failure**

Run: `uv run pytest tests/beat_sync/test_models.py -q`

Expected: collection fails with `ModuleNotFoundError` for `minoru_studio.beat_sync`.

- [ ] **Step 3: Implement the contract**

Create an empty `src/minoru_studio/beat_sync/__init__.py`. In `models.py` define these exact immutable types:

~~~python
PLAN_SCHEMA_VERSION = 1


class PlanError(ValueError):
    pass


class MaterialKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class BeatAnalysis:
    duration_ms: int
    bpm: float
    beats_ms: tuple[int, ...]
    cut_points_ms: tuple[int, ...]
    minimum_cut_ms: int = 150


@dataclass(frozen=True, slots=True)
class BeatSyncSettings:
    every_n_requested: str | int
    every_n_resolved: int
    order_mode: str
    timeline_name: str


@dataclass(frozen=True, slots=True)
class MaterialPlan:
    input_index: int
    kind: MaterialKind
    order_index: int


@dataclass(frozen=True, slots=True)
class BeatSyncPlan:
    schema_version: int
    job_id: str
    audio_input_index: int
    analysis: BeatAnalysis
    settings: BeatSyncSettings
    materials: tuple[MaterialPlan, ...]
    mode: str = "beat-sync"
~~~

Import `json`, `math`, `os`, `asdict/dataclass`, `StrEnum`, `Path`, `Mapping/Any`, and `uuid4`. Implement `validate_plan(plan)` with all of these checks, each raising `PlanError`:

1. schema is 1, mode is `beat-sync`, job ID is non-empty, and audio input index is exactly 0;
2. every `_ms` value is an integer but not a boolean;
3. duration/minimum cut are positive, BPM is finite/positive;
4. beats and cut points are strictly increasing;
5. there are at least two beats and at least two usable internal cut points;
6. cut points start at 0, end at duration, and adjacent differences meet `minimum_cut_ms`;
7. requested interval is `auto` or 1-16, resolved interval is 1-16;
8. order is `asc` or `random` and timeline name is non-blank;
9. materials are non-empty, input indices are unique/positive, and order indices equal `range(len(materials))`.

Implement serialization with this exact public behavior:

~~~python
def plan_to_dict(plan):
    validate_plan(plan)
    data = asdict(plan)
    data["materials"] = [
        {**asdict(item), "kind": item.kind.value}
        for item in plan.materials
    ]
    return data


def save_plan(path, plan):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}-{uuid4()}.tmp"
    try:
        temporary.write_text(
            json.dumps(
                plan_to_dict(plan),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_plan(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read beat-sync plan: {exc}") from exc
    return plan_from_dict(payload)
~~~

`plan_from_dict` must explicitly construct all five dataclasses, convert material `kind` through `MaterialKind(...)`, reject a non-mapping root, call `validate_plan`, and wrap `KeyError`/`TypeError`/`ValueError` as `PlanError("invalid beat-sync plan: ...")`.

- [ ] **Step 4: Run the focused tests**

Run: `uv run pytest tests/beat_sync/test_models.py -q`

Expected: `3 passed`.

- [ ] **Step 5: Commit**

~~~powershell
git add src/minoru_studio/beat_sync tests/beat_sync/test_models.py
git commit -m "feat: define beat-sync plan contract"
~~~

### Task 2: Media Discovery and Stable Ordering

**Files:**
- Create: `src/minoru_studio/beat_sync/media.py`
- Test: `tests/beat_sync/test_media.py`

**Interfaces:**
- Produces: `MaterialSource(path: Path, kind: MaterialKind)`.
- Produces: `validate_music_file`, `discover_materials`, and `order_materials`.
- Consumes: `MaterialKind` from Task 1.

- [ ] **Step 1: Write failing tests**

~~~python
from minoru_studio.beat_sync.media import (
    MaterialKind,
    discover_materials,
    order_materials,
    validate_music_file,
)


def test_discovery_is_non_recursive_and_case_insensitive(tmp_path):
    (tmp_path / "B.MOV").write_bytes(b"video")
    (tmp_path / "a.jpg").write_bytes(b"photo")
    (tmp_path / "ignore.txt").write_text("x")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "hidden.png").write_bytes(b"photo")
    found = discover_materials(tmp_path)
    assert [(item.path.name, item.kind) for item in found] == [
        ("a.jpg", MaterialKind.PHOTO),
        ("B.MOV", MaterialKind.VIDEO),
    ]


def test_random_order_is_persistable_by_injection(tmp_path):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (tmp_path / name).write_bytes(b"x")
    ordered = order_materials(
        discover_materials(tmp_path),
        "random",
        shuffler=lambda values: values.reverse(),
    )
    assert [item.path.name for item in ordered] == ["c.jpg", "b.jpg", "a.jpg"]


def test_music_extension_is_validated(tmp_path):
    song = tmp_path / "song.MP3"
    song.write_bytes(b"audio")
    assert validate_music_file(song) == song.resolve()
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/beat_sync/test_media.py -q`

Expected: missing module failure.

- [ ] **Step 3: Implement discovery and ordering**

Create `media.py`:

~~~python
from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from minoru_studio.beat_sync.models import MaterialKind


AUDIO_EXTENSIONS = frozenset({".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aac"})
PHOTO_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".dng"}
)
VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".m4v", ".mxf", ".avi", ".mkv", ".braw", ".mts", ".m2ts"}
)


@dataclass(frozen=True, slots=True)
class MaterialSource:
    path: Path
    kind: MaterialKind


def validate_music_file(path):
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"music input is not a file: {resolved}")
    if resolved.suffix.casefold() not in AUDIO_EXTENSIONS:
        raise ValueError(f"unsupported music extension: {resolved.suffix}")
    return resolved


def discover_materials(directory):
    root = Path(directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"media directory is not a directory: {root}")
    materials = []
    for path in root.iterdir():
        if not path.is_file():
            continue
        suffix = path.suffix.casefold()
        if suffix in PHOTO_EXTENSIONS:
            materials.append(MaterialSource(path.resolve(), MaterialKind.PHOTO))
        elif suffix in VIDEO_EXTENSIONS:
            materials.append(MaterialSource(path.resolve(), MaterialKind.VIDEO))
    materials.sort(key=lambda item: (item.path.name.casefold(), str(item.path).casefold()))
    if not materials:
        raise ValueError(f"no supported visual media found: {root}")
    return materials


def order_materials(materials, mode, shuffler=None):
    ordered = sorted(
        materials,
        key=lambda item: (item.path.name.casefold(), str(item.path).casefold()),
    )
    if mode == "asc":
        return ordered
    if mode != "random":
        raise ValueError("order mode must be asc or random")
    (shuffler or random.SystemRandom().shuffle)(ordered)
    return ordered
~~~

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/beat_sync/test_media.py -q`

Expected: `3 passed`.

- [ ] **Step 5: Commit**

~~~powershell
git add src/minoru_studio/beat_sync/media.py tests/beat_sync/test_media.py
git commit -m "feat: discover beat-sync media"
~~~

### Task 3: Librosa Analysis and Millisecond Normalization

**Files:**
- Create: `src/minoru_studio/beat_sync/analyzer.py`
- Test: `tests/beat_sync/test_analyzer.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `seconds_to_milliseconds` and `normalize_analysis`.
- Produces: `LibrosaBeatAnalyzer.analyze(path) -> BeatAnalysis`.
- Consumes: `BeatAnalysis` from Task 1.

- [ ] **Step 1: Write failing analyzer tests**

~~~python
import pytest

from minoru_studio.beat_sync.analyzer import (
    AnalysisError,
    LibrosaBeatAnalyzer,
    normalize_analysis,
    seconds_to_milliseconds,
)


def test_seconds_round_to_integer_milliseconds():
    assert seconds_to_milliseconds(0.5024) == 502
    assert seconds_to_milliseconds(0.5025) == 503


def test_normalization_deduplicates_and_keeps_exact_end():
    result = normalize_analysis(
        2.04,
        120.0,
        (0.1, 0.5, 0.5004, 1.0, 1.95),
    )
    assert result.beats_ms == (100, 500, 1_000, 1_950)
    assert result.cut_points_ms == (0, 500, 1_000, 2_040)


def test_insufficient_beats_fail():
    with pytest.raises(AnalysisError, match="at least two"):
        normalize_analysis(2.0, 120.0, (0.5,))


def test_librosa_backend_is_injectable(tmp_path):
    class FakeLibrosa:
        def load(self, path, sr, mono):
            return [0.0], 48_000

        def get_duration(self, y, sr):
            return 2.0

        class beat:
            @staticmethod
            def beat_track(y, sr, units):
                return [120.0], [0.5, 1.0, 1.5]

    class FakeNumpy:
        @staticmethod
        def atleast_1d(value):
            return value

    song = tmp_path / "song.wav"
    song.write_bytes(b"audio")
    result = LibrosaBeatAnalyzer(FakeLibrosa(), FakeNumpy()).analyze(song)
    assert result.duration_ms == 2_000
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/beat_sync/test_analyzer.py -q`

Expected: missing analyzer module failure.

- [ ] **Step 3: Add librosa and refresh the lock**

Set:

~~~toml
dependencies = ["librosa>=0.11,<0.12"]
~~~

Run: `uv lock`

Expected: exit 0 and `uv.lock` contains librosa 0.11.x. Do not add direct numpy or soundfile dependencies.

- [ ] **Step 4: Implement analysis**

Create `analyzer.py`:

~~~python
from __future__ import annotations

import math
from pathlib import Path

from minoru_studio.beat_sync.models import BeatAnalysis


class AnalysisError(RuntimeError):
    pass


def seconds_to_milliseconds(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise AnalysisError("time values must be finite and non-negative")
    return math.floor(value * 1_000 + 0.5)


def normalize_analysis(duration_seconds, bpm, beat_seconds, minimum_cut_ms=150):
    duration_seconds = float(duration_seconds)
    duration_ms = seconds_to_milliseconds(duration_seconds)
    if duration_ms < minimum_cut_ms:
        raise AnalysisError("audio is shorter than minimum_cut_ms")
    bpm = float(bpm)
    if not math.isfinite(bpm) or bpm <= 0:
        raise AnalysisError("bpm must be finite and positive")
    beats_ms = tuple(sorted({
        seconds_to_milliseconds(value)
        for value in beat_seconds
        if math.isfinite(float(value)) and 0 < float(value) < duration_seconds
    }))
    if len(beats_ms) < 2:
        raise AnalysisError("beat analysis requires at least two usable beats")
    cut_points = [0]
    for beat_ms in beats_ms:
        if beat_ms - cut_points[-1] >= minimum_cut_ms:
            cut_points.append(beat_ms)
    if len(cut_points) < 3:
        raise AnalysisError("beat analysis requires at least two usable cut beats")
    if duration_ms - cut_points[-1] < minimum_cut_ms:
        if len(cut_points) == 1:
            raise AnalysisError("audio has no usable cut interval")
        cut_points[-1] = duration_ms
    else:
        cut_points.append(duration_ms)
    if len(cut_points) < 4:
        raise AnalysisError("beat analysis requires at least two internal cut points")
    return BeatAnalysis(
        duration_ms,
        bpm,
        beats_ms,
        tuple(cut_points),
        minimum_cut_ms,
    )


class LibrosaBeatAnalyzer:
    def __init__(self, librosa_module=None, numpy_module=None):
        self._librosa = librosa_module
        self._numpy = numpy_module

    def analyze(self, path):
        try:
            if self._librosa is None:
                import librosa
                self._librosa = librosa
            if self._numpy is None:
                import numpy
                self._numpy = numpy
            y, sample_rate = self._librosa.load(str(Path(path)), sr=None, mono=True)
            duration = self._librosa.get_duration(y=y, sr=sample_rate)
            tempo, beats = self._librosa.beat.beat_track(
                y=y,
                sr=sample_rate,
                units="time",
            )
            bpm = float(self._numpy.atleast_1d(tempo)[0])
            return normalize_analysis(duration, bpm, (float(value) for value in beats))
        except AnalysisError:
            raise
        except Exception as exc:
            raise AnalysisError(f"cannot analyze BGM: {exc}") from exc
~~~

- [ ] **Step 5: Run tests and lock validation**

Run: `uv run pytest tests/beat_sync/test_analyzer.py -q`

Expected: `4 passed`.

Run: `uv lock --check`

Expected: exit 0.

- [ ] **Step 6: Commit**

~~~powershell
git add pyproject.toml uv.lock src/minoru_studio/beat_sync/analyzer.py tests/beat_sync/test_analyzer.py
git commit -m "feat: analyze beats in integer milliseconds"
~~~

### Task 4: Deterministic Plan Assembly

**Files:**
- Create: `src/minoru_studio/beat_sync/plan.py`
- Test: `tests/beat_sync/test_plan.py`

**Interfaces:**
- Produces: `resolve_every_n(interval_count, material_count, requested)`.
- Produces: `build_plan(job_id, analysis, materials, requested, order_mode, timeline_name)`.
- Consumes: ordered `MaterialSource` and Task 1 models.

- [ ] **Step 1: Write failing planner tests**

~~~python
from pathlib import Path

from minoru_studio.beat_sync.media import MaterialSource
from minoru_studio.beat_sync.models import BeatAnalysis, MaterialKind
from minoru_studio.beat_sync.plan import build_plan, resolve_every_n


def test_auto_interval_uses_half_up_rounding_and_clamps():
    assert resolve_every_n(5, 2, "auto") == 3
    assert resolve_every_n(100, 2, "auto") == 16
    assert resolve_every_n(8, 3, 2) == 2


def test_plan_references_ordered_job_inputs():
    analysis = BeatAnalysis(2_000, 120.0, (500, 1_000), (0, 500, 1_000, 2_000))
    materials = [
        MaterialSource(Path("b.mov"), MaterialKind.VIDEO),
        MaterialSource(Path("a.jpg"), MaterialKind.PHOTO),
    ]
    plan = build_plan("job", analysis, materials, "auto", "random", "Demo")
    assert [item.input_index for item in plan.materials] == [1, 2]
    assert plan.settings.every_n_resolved == 2
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/beat_sync/test_plan.py -q`

Expected: missing plan module failure.

- [ ] **Step 3: Implement plan assembly**

~~~python
from __future__ import annotations

from minoru_studio.beat_sync.models import (
    PLAN_SCHEMA_VERSION,
    BeatSyncPlan,
    BeatSyncSettings,
    MaterialPlan,
    validate_plan,
)


def resolve_every_n(interval_count, material_count, requested):
    if interval_count <= 0 or material_count <= 0:
        raise ValueError("interval and material counts must be positive")
    if requested != "auto":
        if isinstance(requested, bool) or not isinstance(requested, int):
            raise ValueError("requested interval must be auto or integer")
        if not 1 <= requested <= 16:
            raise ValueError("requested interval must be 1..16")
        return requested
    rounded = (interval_count * 2 + material_count) // (2 * material_count)
    return max(1, min(16, rounded))


def build_plan(
    job_id,
    analysis,
    materials,
    every_n_requested,
    order_mode,
    timeline_name,
):
    resolved = resolve_every_n(
        len(analysis.cut_points_ms) - 1,
        len(materials),
        every_n_requested,
    )
    plan = BeatSyncPlan(
        PLAN_SCHEMA_VERSION,
        job_id,
        0,
        analysis,
        BeatSyncSettings(
            every_n_requested,
            resolved,
            order_mode,
            timeline_name.strip(),
        ),
        tuple(
            MaterialPlan(index + 1, material.kind, index)
            for index, material in enumerate(materials)
        ),
    )
    validate_plan(plan)
    return plan
~~~

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/beat_sync/test_plan.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit**

~~~powershell
git add src/minoru_studio/beat_sync/plan.py tests/beat_sync/test_plan.py
git commit -m "feat: assemble deterministic beat-sync plans"
~~~

### Task 5: Durable Preparation Service

**Files:**
- Modify: `src/minoru_studio/jobs/store.py`
- Create: `src/minoru_studio/beat_sync/service.py`
- Modify: `tests/jobs/test_store.py`
- Test: `tests/beat_sync/test_service.py`

**Interfaces:**
- Produces: `fingerprint_artifact(job_dir, path, kind) -> ArtifactRecord`.
- Produces: `BeatSyncRequest` and `PreparationFailed`.
- Produces: `BeatSyncService.create_and_prepare(request) -> Path`.
- Produces: `BeatSyncService.resume(job_dir) -> Path`.

- [ ] **Step 1: Write failing artifact and service tests**

Append to `tests/jobs/test_store.py`:

~~~python
def test_artifact_fingerprint_requires_job_containment(tmp_path):
    from minoru_studio.jobs.store import fingerprint_artifact

    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.BEAT_SYNC)
    plan = job_dir / "outputs" / "beat-sync-plan.json"
    plan.write_text("{}", encoding="utf-8")
    record = fingerprint_artifact(job_dir, plan, "beat-sync-plan")
    assert record.path == "outputs/beat-sync-plan.json"
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="inside job"):
        fingerprint_artifact(job_dir, outside, "x")
~~~

Create `tests/beat_sync/test_service.py`:

~~~python
import pytest

from minoru_studio.beat_sync.models import BeatAnalysis, load_plan
from minoru_studio.beat_sync.service import BeatSyncRequest, BeatSyncService
from minoru_studio.jobs.model import JobStatus
from minoru_studio.jobs.store import JobStore


class FakeAnalyzer:
    def analyze(self, path):
        return BeatAnalysis(
            2_000,
            120.0,
            (500, 1_000, 1_500),
            (0, 500, 1_000, 1_500, 2_000),
        )


def make_request(tmp_path):
    music = tmp_path / "song.wav"
    music.write_bytes(b"audio")
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.jpg").write_bytes(b"photo")
    return BeatSyncRequest(
        music, media, "auto", "asc", "Demo", "demo", tmp_path / "jobs"
    )


def test_service_creates_successful_registered_plan(tmp_path):
    service = BeatSyncService(analyzer=FakeAnalyzer())
    job_dir = service.create_and_prepare(make_request(tmp_path))
    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.SUCCEEDED
    assert manifest.steps["prepare"].status.value == "succeeded"
    assert manifest.artifacts[0].path == "outputs/beat-sync-plan.json"
    assert load_plan(job_dir / manifest.artifacts[0].path).job_id == manifest.job_id


def test_resume_rejects_changed_input(tmp_path):
    request = make_request(tmp_path)
    service = BeatSyncService(analyzer=FakeAnalyzer())
    job_dir = service._create_pending(request)
    (request.media_dir / "a.jpg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="input changed"):
        service.resume(job_dir)
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/jobs/test_store.py tests/beat_sync/test_service.py -q`

Expected: missing helper and service failures.

- [ ] **Step 3: Add contained artifact fingerprints**

Import `ArtifactRecord` in `jobs/store.py` and append:

~~~python
def fingerprint_artifact(job_dir: Path, path: Path, kind: str) -> ArtifactRecord:
    root = Path(job_dir).resolve(strict=True)
    resolved = Path(path).resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"artifact must be inside job: {resolved}") from exc
    ref = fingerprint_file(resolved)
    return ArtifactRecord(
        kind=kind,
        path=relative.as_posix(),
        size=ref.size,
        sha256=ref.sha256,
    )
~~~

- [ ] **Step 4: Implement the preparation lifecycle**

Create `service.py` with these complete public definitions:

~~~python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from minoru_studio.beat_sync.analyzer import LibrosaBeatAnalyzer
from minoru_studio.beat_sync.media import (
    MaterialSource,
    discover_materials,
    order_materials,
    validate_music_file,
)
from minoru_studio.beat_sync.models import MaterialKind, save_plan
from minoru_studio.beat_sync.plan import build_plan
from minoru_studio.jobs.model import JobMode, JobStatus, StepRecord, StepStatus
from minoru_studio.jobs.store import (
    JobStore,
    fingerprint_artifact,
    fingerprint_file,
)
from minoru_studio.logging_utils import configure_job_logger


@dataclass(frozen=True, slots=True)
class BeatSyncRequest:
    music: Path
    media_dir: Path
    every_n_requested: str | int
    order_mode: str
    timeline_name: str
    name: str
    output_dir: Path


class PreparationFailed(RuntimeError):
    def __init__(self, job_dir, cause):
        self.job_dir = Path(job_dir)
        self.cause = cause
        super().__init__(f"beat-sync preparation failed in {job_dir}: {cause}")


def _now():
    return datetime.now(UTC).isoformat()


class BeatSyncService:
    def __init__(self, store=None, analyzer=None, shuffler=None):
        self.store = store or JobStore()
        self.analyzer = analyzer or LibrosaBeatAnalyzer()
        self.shuffler = shuffler

    def _create_pending(self, request):
        music = validate_music_file(request.music)
        materials = order_materials(
            discover_materials(request.media_dir),
            request.order_mode,
            self.shuffler,
        )
        timeline_name = request.timeline_name.strip()
        if not timeline_name:
            raise ValueError("timeline name must not be empty")
        return self.store.create(
            request.output_dir,
            request.name,
            JobMode.BEAT_SYNC,
            [music, *(item.path for item in materials)],
            {
                "every_n_requested": request.every_n_requested,
                "order_mode": request.order_mode,
                "timeline_name": timeline_name,
                "material_kinds": [item.kind.value for item in materials],
            },
        )

    def create_and_prepare(self, request):
        return self._prepare(self._create_pending(request))

    def resume(self, job_dir):
        job_dir = Path(job_dir).resolve(strict=True)
        manifest = self.store.load(job_dir, recover_interrupted=True)
        if manifest.mode is not JobMode.BEAT_SYNC:
            raise ValueError("job mode is not beat-sync")
        if manifest.status not in {JobStatus.PENDING, JobStatus.INTERRUPTED}:
            raise ValueError(f"job cannot resume from {manifest.status.value}")
        for expected in manifest.inputs:
            if fingerprint_file(Path(expected.path)) != expected:
                raise ValueError(f"input changed: {expected.path}")
        return self._prepare(job_dir)

    def _prepare(self, job_dir):
        logger = configure_job_logger(job_dir)

        def start(manifest):
            manifest.status = JobStatus.RUNNING
            manifest.last_error = None
            manifest.steps["prepare"] = StepRecord(
                status=StepStatus.RUNNING,
                started_at=_now(),
            )

        self.store.update(job_dir, start)
        try:
            manifest = self.store.load(job_dir, recover_interrupted=False)
            analysis = self.analyzer.analyze(Path(manifest.inputs[0].path))
            materials = [
                MaterialSource(
                    Path(manifest.inputs[index + 1].path),
                    MaterialKind(kind),
                )
                for index, kind in enumerate(manifest.settings["material_kinds"])
            ]
            plan = build_plan(
                manifest.job_id,
                analysis,
                materials,
                manifest.settings["every_n_requested"],
                manifest.settings["order_mode"],
                manifest.settings["timeline_name"],
            )
            plan_path = job_dir / "outputs" / "beat-sync-plan.json"
            save_plan(plan_path, plan)
            artifact = fingerprint_artifact(
                job_dir,
                plan_path,
                "beat-sync-plan",
            )

            def succeed(latest):
                latest.status = JobStatus.SUCCEEDED
                latest.last_error = None
                latest.artifacts = [
                    item
                    for item in latest.artifacts
                    if item.kind != "beat-sync-plan"
                ] + [artifact]
                step = latest.steps["prepare"]
                step.status = StepStatus.SUCCEEDED
                step.finished_at = _now()
                step.exit_code = 0
                step.error = None

            self.store.update(job_dir, succeed)
            logger.info("beat-sync plan prepared: %s", plan_path)
            return Path(job_dir)
        except Exception as exc:
            logger.exception("beat-sync preparation failed")

            def fail(latest):
                latest.status = JobStatus.FAILED
                latest.last_error = str(exc)
                step = latest.steps.get("prepare") or StepRecord()
                latest.steps["prepare"] = step
                step.status = StepStatus.FAILED
                step.finished_at = _now()
                step.exit_code = 1
                step.error = str(exc)

            self.store.update(job_dir, fail)
            raise PreparationFailed(job_dir, exc) from exc
~~~

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/jobs/test_store.py tests/beat_sync/test_service.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

~~~powershell
git add src/minoru_studio/jobs/store.py src/minoru_studio/beat_sync/service.py tests/jobs/test_store.py tests/beat_sync/test_service.py
git commit -m "feat: prepare durable beat-sync jobs"
~~~

### Task 6: CLI Create and Resume

**Files:**
- Create: `src/minoru_studio/beat_sync/commands.py`
- Modify: `src/minoru_studio/cli.py`
- Test: `tests/beat_sync/test_commands.py`

**Interfaces:**
- Consumes: `BeatSyncService` and `BeatSyncRequest`.
- Produces: direct `beat-sync` create options and `beat-sync resume <job-dir>`.

- [ ] **Step 1: Write failing CLI tests**

~~~python
from minoru_studio.cli import main


def test_create_accepts_powershell_and_gnu_names(monkeypatch, tmp_path, capsys):
    calls = []

    class Service:
        def create_and_prepare(self, request):
            calls.append(request)
            return tmp_path / "demo.media-job"

    monkeypatch.setattr(
        "minoru_studio.beat_sync.commands.BeatSyncService",
        lambda: Service(),
    )
    assert main([
        "beat-sync",
        "-Music", str(tmp_path / "song.wav"),
        "--media-dir", str(tmp_path / "media"),
        "-EveryN", "auto",
        "-Order", "asc",
        "-TimelineName", "Demo",
        "-Name", "demo",
        "-OutputDir", str(tmp_path),
    ]) == 0
    assert calls[0].every_n_requested == "auto"
    assert capsys.readouterr().out.strip().endswith("demo.media-job")


def test_resume_calls_service(monkeypatch, tmp_path):
    calls = []

    class Service:
        def resume(self, job_dir):
            calls.append(job_dir)
            return job_dir

    monkeypatch.setattr(
        "minoru_studio.beat_sync.commands.BeatSyncService",
        lambda: Service(),
    )
    assert main(["beat-sync", "resume", str(tmp_path)]) == 0
    assert calls == [tmp_path.resolve()]
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/beat_sync/test_commands.py -q`

Expected: parser rejects the unknown command.

- [ ] **Step 3: Implement command handling**

Create `commands.py`:

~~~python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from minoru_studio.beat_sync.service import (
    BeatSyncRequest,
    BeatSyncService,
    PreparationFailed,
)


def parse_every_n(value):
    if value == "auto":
        return value
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("EveryN must be auto or 1..16") from exc
    if not 1 <= parsed <= 16:
        raise argparse.ArgumentTypeError("EveryN must be auto or 1..16")
    return parsed


def beat_sync_command(args):
    service = BeatSyncService()
    try:
        if args.beat_sync_action == "resume":
            job_dir = service.resume(Path(args.job_dir).resolve())
        else:
            required = {
                "Music": args.music,
                "MediaDir": args.media_dir,
                "TimelineName": args.timeline_name,
                "Name": args.name,
                "OutputDir": args.output_dir,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                args.command_parser.error(
                    "missing beat-sync options: " + ", ".join(missing)
                )
            job_dir = service.create_and_prepare(
                BeatSyncRequest(
                    Path(args.music),
                    Path(args.media_dir),
                    args.every_n,
                    args.order,
                    args.timeline_name,
                    args.name,
                    Path(args.output_dir).resolve(),
                )
            )
    except PreparationFailed as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(Path(job_dir).resolve())
    return 0
~~~

- [ ] **Step 4: Register the parser**

Import `beat_sync_command` and `parse_every_n` in `cli.py`. Before `return parser`, add:

~~~python
    beat_sync_parser = subparsers.add_parser(
        "beat-sync",
        help="prepare a beat-synced Resolve job",
    )
    beat_sync_parser.set_defaults(
        handler=beat_sync_command,
        command_parser=beat_sync_parser,
        beat_sync_action=None,
    )
    beat_sync_parser.add_argument("-Music", "--music", dest="music")
    beat_sync_parser.add_argument("-MediaDir", "--media-dir", dest="media_dir")
    beat_sync_parser.add_argument(
        "-EveryN", "--every-n", dest="every_n", type=parse_every_n, default="auto"
    )
    beat_sync_parser.add_argument(
        "-Order", "--order", choices=("asc", "random"), default="asc"
    )
    beat_sync_parser.add_argument("-TimelineName", "--timeline-name", dest="timeline_name")
    beat_sync_parser.add_argument("-Name", "--name", dest="name")
    beat_sync_parser.add_argument("-OutputDir", "--output-dir", dest="output_dir")
    actions = beat_sync_parser.add_subparsers(dest="beat_sync_action")
    resume_parser = actions.add_parser("resume", help="resume interrupted preparation")
    resume_parser.add_argument("job_dir")
    resume_parser.set_defaults(
        handler=beat_sync_command,
        command_parser=beat_sync_parser,
    )
~~~

- [ ] **Step 5: Run CLI tests**

Run: `uv run pytest tests/beat_sync/test_commands.py tests/test_cli.py tests/test_job_commands.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

~~~powershell
git add src/minoru_studio/beat_sync/commands.py src/minoru_studio/cli.py tests/beat_sync/test_commands.py
git commit -m "feat: expose beat-sync preparation CLI"
~~~

### Task 7: GUI Settings and Responsive Worker

**Files:**
- Create: `src/minoru_studio/beat_sync/settings.py`
- Modify: `src/minoru_studio/gui.py`
- Test: `tests/beat_sync/test_settings.py`
- Modify: `tests/test_gui_controller.py`

**Interfaces:**
- Produces: `load_settings` and `save_settings` for allowed non-secret keys.
- Produces: `LauncherController.prepare_beat_sync(...) -> Path`.
- The background worker may not call Tk directly; all UI completion uses `root.after`.

- [ ] **Step 1: Write failing settings and controller tests**

~~~python
from minoru_studio.beat_sync.settings import load_settings, save_settings


def test_settings_round_trip_only_allowed_keys(tmp_path):
    path = tmp_path / "config.json"
    save_settings(
        {
            "music": "song.wav",
            "media_dir": "media",
            "every_n": "auto",
            "order": "asc",
            "timeline_name": "Demo",
            "secret": "must-not-persist",
        },
        path,
    )
    assert load_settings(path) == {
        "music": "song.wav",
        "media_dir": "media",
        "every_n": "auto",
        "order": "asc",
        "timeline_name": "Demo",
    }
~~~

Add to `tests/test_gui_controller.py`:

~~~python
def test_controller_prepares_beat_sync_with_injected_service(tmp_path):
    calls = []

    class Service:
        def create_and_prepare(self, request):
            calls.append(request)
            return tmp_path / "demo.media-job"

    controller = LauncherController(beat_sync_service=Service())
    result = controller.prepare_beat_sync(
        music="song.wav",
        media_dir="media",
        every_n="auto",
        order="asc",
        timeline_name="Demo",
        name="demo",
        output_dir=str(tmp_path),
    )
    assert result.name == "demo.media-job"
    assert calls[0].timeline_name == "Demo"
~~~

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/beat_sync/test_settings.py tests/test_gui_controller.py -q`

Expected: missing settings and controller APIs.

- [ ] **Step 3: Implement atomic settings**

Create `settings.py`:

~~~python
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


ALLOWED_KEYS = {
    "music", "media_dir", "every_n", "order",
    "timeline_name", "name", "output_dir",
}


def default_settings_path():
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return root / "MinoruStudio" / "config.json"


def load_settings(path=None):
    target = Path(path) if path else default_settings_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in ALLOWED_KEYS if key in payload}


def save_settings(values, path=None):
    target = Path(path) if path else default_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".config-{uuid4()}.tmp"
    try:
        temporary.write_text(
            json.dumps(
                {key: values[key] for key in ALLOWED_KEYS if key in values},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
~~~

- [ ] **Step 4: Extend the controller**

Allow `LauncherController.__init__(store=None, beat_sync_service=None)` and
assign the injected service with an explicit `is not None` check so a falsy
test double is preserved:

~~~python
self.beat_sync_service = (
    beat_sync_service if beat_sync_service is not None else BeatSyncService()
)
~~~

Add:

~~~python
    def prepare_beat_sync(
        self,
        *,
        music,
        media_dir,
        every_n,
        order,
        timeline_name,
        name,
        output_dir,
    ):
        return self.beat_sync_service.create_and_prepare(
            BeatSyncRequest(
                Path(music),
                Path(media_dir),
                every_n,
                order,
                timeline_name,
                name,
                Path(output_dir),
            )
        )
~~~

- [ ] **Step 5: Add form fields and the thread boundary**

Keep generic create/open controls for non-beat-sync modes. For beat-sync add BGM picker, media-directory picker, auto/fixed 1-16 control, order radios, and timeline name. The submit callback must use:

~~~python
    def prepare_beat_sync_job():
        values = {
            "music": music_var.get().strip(),
            "media_dir": media_var.get().strip(),
            "every_n": "auto" if auto_var.get() else int(every_n_var.get()),
            "order": order_var.get(),
            "timeline_name": timeline_var.get().strip(),
            "name": name_var.get().strip(),
            "output_dir": output_var.get().strip(),
        }
        save_beat_sync_settings(values)
        prepare_button.state(["disabled"])
        status_var.set("BGMを解析しています…")

        def worker():
            try:
                job_dir = controller.prepare_beat_sync(**values)
            except Exception as exc:
                root.after(0, lambda message=str(exc): finish_error(message))
            else:
                root.after(0, lambda path=job_dir: finish_success(path))

        threading.Thread(target=worker, daemon=True).start()
~~~

`finish_error` and `finish_success` run on Tk's thread and always re-enable the button. Error shows `messagebox.showerror`. Success shows the absolute job path and Resolve-adapter instruction. Load prior values when constructing fields.

- [ ] **Step 6: Run non-visual tests**

Run: `uv run pytest tests/beat_sync/test_settings.py tests/test_gui_controller.py -q`

Expected: all selected tests pass.

- [ ] **Step 7: Smoke the window**

Run: `uv run minoru-studio`

Expected: window opens, beat-sync fields and pickers work, switching modes retains generic controls, and closing exits normally. Do not submit nondisposable inputs.

- [ ] **Step 8: Commit**

~~~powershell
git add src/minoru_studio/beat_sync/settings.py src/minoru_studio/gui.py tests/beat_sync/test_settings.py tests/test_gui_controller.py
git commit -m "feat: add beat-sync launcher workflow"
~~~

### Task 8: Resolve-Free Acceptance and Documentation

**Files:**
- Create: `tests/acceptance/test_beat_sync_preparation.py`
- Modify: `README.md`

**Interfaces:**
- Verifies a complete prepared job without Resolve.
- Documents that actual Resolve application follows in the adapter plan.

- [ ] **Step 1: Write the acceptance test**

~~~python
from minoru_studio.beat_sync.models import BeatAnalysis, load_plan
from minoru_studio.beat_sync.service import BeatSyncRequest, BeatSyncService
from minoru_studio.jobs.store import JobStore


class FixedAnalyzer:
    def analyze(self, path):
        return BeatAnalysis(
            4_000,
            120.0,
            (500, 1_000, 1_500, 2_000, 2_500, 3_000, 3_500),
            (0, 500, 1_000, 1_500, 2_000, 2_500, 3_000, 3_500, 4_000),
        )


def test_prepared_job_is_resolve_independent(tmp_path):
    music = tmp_path / "song.wav"
    music.write_bytes(b"audio")
    media = tmp_path / "media"
    media.mkdir()
    (media / "01.jpg").write_bytes(b"photo")
    (media / "02.mov").write_bytes(b"video")
    job_dir = BeatSyncService(analyzer=FixedAnalyzer()).create_and_prepare(
        BeatSyncRequest(
            music,
            media,
            "auto",
            "asc",
            "Demo",
            "demo",
            tmp_path / "jobs",
        )
    )
    manifest = JobStore().load(job_dir, recover_interrupted=False)
    plan = load_plan(job_dir / "outputs" / "beat-sync-plan.json")
    assert manifest.status.value == "succeeded"
    assert all(isinstance(value, int) for value in plan.analysis.beats_ms)
    assert [item.input_index for item in plan.materials] == [1, 2]
    assert plan.analysis.cut_points_ms[-1] == plan.analysis.duration_ms
~~~

- [ ] **Step 2: Run acceptance**

Run: `uv run pytest tests/acceptance/test_beat_sync_preparation.py -q`

Expected: `1 passed`.

- [ ] **Step 3: Document preparation**

Replace the foundation-only status in `README.md` with a MinoruStudio preparation section. Include this exact command:

~~~powershell
uv run minoru-studio beat-sync `
  -Music .\song.mp3 `
  -MediaDir .\media `
  -EveryN auto `
  -Order asc `
  -TimelineName "Beat Sync Demo" `
  -Name demo `
  -OutputDir .\jobs
~~~

Explain `outputs/beat-sync-plan.json`, integer-millisecond storage, GUI use, and that the Resolve adapter is the next plan. Keep the full legacy MinoruDouga section and label it the fallback until real Resolve acceptance.

- [ ] **Step 4: Run all gates**

Run: `uv sync --locked --dev`

Expected: exit 0.

Run: `uv run pytest -q`

Expected: every Phase 1 and preparation test passes.

Run: `uv run minoru-studio beat-sync --help`

Expected: exit 0 and both alias styles are shown.

Run: `uv run minoru-studio doctor --json`

Expected: valid JSON and all required local checks are `ok`.

- [ ] **Step 5: Commit**

~~~powershell
git add tests/acceptance/test_beat_sync_preparation.py README.md
git commit -m "docs: document beat-sync preparation"
~~~

## Preparation Plan Completion Gate

1. `git status --short` contains no unexpected changes.
2. `uv lock --check` exits 0.
3. `uv run pytest -q` passes.
4. Disposable inputs produce a `succeeded` job with a valid plan.
5. No Resolve process or project was required or mutated.
6. The three legacy MinoruDouga files are byte-for-byte unchanged from plan start.
