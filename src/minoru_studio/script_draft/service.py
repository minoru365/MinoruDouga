"""Resumable, local orchestration for representative-frame script drafts."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import shutil
import struct
import sys
from typing import Any
from uuid import uuid4

from minoru_studio.jobs.lock import JobLockedError
from minoru_studio.jobs.model import (
    ArtifactRecord,
    InputRef,
    JobManifest,
    JobMode,
    JobStatus,
    StepRecord,
    StepStatus,
)
from minoru_studio.jobs.store import JobStore, fingerprint_artifact, fingerprint_file
from minoru_studio.logging_utils import configure_job_logger
from minoru_studio.processes import ProcessCancelledError
from minoru_studio.script_draft.artifacts import artifacts_valid, merge_candidates, render_artifacts
from minoru_studio.script_draft.media import (
    extract_interval_candidates,
    extract_scene_candidates,
    probe_video,
)
from minoru_studio.script_draft.models import (
    FRAME_FORMAT,
    INTERVAL_MS,
    MAX_FRAME_EDGE,
    MERGE_TOLERANCE_MS,
    SCENE_THRESHOLD,
    FrameCandidate,
    ScriptDraftRequest,
    VideoInfo,
)
from minoru_studio.transcribe.media import read_media_tool_versions


_STEP_NAMES = (
    "probe-input",
    "extract-scene-frames",
    "extract-interval-frames",
    "render-draft",
)
_CANDIDATE_STATE = {"scene": "scene-frames/candidates.json", "interval": "interval-frames/candidates.json"}


class ScriptDraftFailed(RuntimeError):
    """A stable, content-free script draft failure."""

    def __init__(self, job_dir: Path, category: str) -> None:
        self.job_dir = Path(job_dir)
        self.category = category
        super().__init__(category)


class ScriptDraftInterrupted(RuntimeError):
    """Raised after cancellation has been durably recorded."""

    def __init__(self, job_dir: Path) -> None:
        self.job_dir = Path(job_dir)
        super().__init__("script draft interrupted")


class _InputInvalid(ValueError):
    pass


class _LoggerSetupFailure(RuntimeError):
    pass


Progress = Callable[[str], object]
Extractor = Callable[..., list[FrameCandidate]]


class _NoOpLogger:
    def info(self, message: str, *args: object) -> None:
        return None

    def error(self, message: str, *args: object) -> None:
        return None


class ScriptDraftService:
    """Create and resume offline, deterministic script-draft jobs."""

    def __init__(
        self,
        *,
        store: JobStore | None = None,
        probe: Callable[[Path], VideoInfo] = probe_video,
        extract_scene: Extractor = extract_scene_candidates,
        extract_interval: Extractor = extract_interval_candidates,
        render: Callable[..., object] = render_artifacts,
        tool_versions: Callable[[], object] = read_media_tool_versions,
        logger_factory: Callable[[Path], logging.Logger] = configure_job_logger,
    ) -> None:
        self._store = store or JobStore()
        self._probe = probe
        self._extract_scene = extract_scene
        self._extract_interval = extract_interval
        self._render = render
        self._tool_versions = tool_versions
        self._logger_factory = logger_factory

    def create_and_run(
        self,
        request: ScriptDraftRequest,
        *,
        cancel_event: object | None = None,
        progress: Progress | None = None,
    ) -> Path:
        self._validate_request(request)
        settings = self._settings()
        job_dir = self._store.create(
            request.output_dir, request.name, JobMode.SCRIPT_DRAFT, (request.input_path,), settings
        )
        try:
            self._claim(job_dir, settings, {JobStatus.PENDING})
        except _InputInvalid:
            self._mark_failed(job_dir, None, "input validation", None)
            raise ScriptDraftFailed(job_dir, "input validation") from None
        except JobLockedError:
            raise RuntimeError("script draft job is already running") from None
        return self._execute(job_dir, request, cancel_event=cancel_event, progress=progress)

    def resume(
        self,
        job_dir: Path,
        *,
        cancel_event: object | None = None,
        progress: Progress | None = None,
    ) -> Path:
        job_dir = Path(job_dir).resolve(strict=True)
        manifest = self._store.load(job_dir, recover_interrupted=False)
        if manifest.mode is not JobMode.SCRIPT_DRAFT:
            raise RuntimeError("job is not a script draft job")
        if manifest.status is JobStatus.RUNNING:
            raise RuntimeError("script draft job is already running")
        if manifest.status not in {JobStatus.PENDING, JobStatus.FAILED, JobStatus.INTERRUPTED}:
            raise RuntimeError("script draft job cannot be resumed from its current status")
        try:
            request = self._request_from_manifest(manifest, job_dir)
            self._claim(job_dir, self._settings(), {JobStatus.PENDING, JobStatus.FAILED, JobStatus.INTERRUPTED})
        except _InputInvalid:
            self._mark_failed(job_dir, None, "input validation", None)
            raise ScriptDraftFailed(job_dir, "input validation") from None
        except JobLockedError:
            raise RuntimeError("script draft job is already running") from None
        return self._execute(job_dir, request, cancel_event=cancel_event, progress=progress)

    def _execute(
        self, job_dir: Path, request: ScriptDraftRequest, *, cancel_event: object | None, progress: Progress | None
    ) -> Path:
        logger: logging.Logger | _NoOpLogger = _NoOpLogger()
        current_step: str | None = None
        category = "input validation"
        try:
            self._contained_job_paths(job_dir)
            try:
                logger = self._logger_factory(job_dir)
            except Exception as exc:
                raise _LoggerSetupFailure from exc
            context, start_at = self._reconcile(job_dir)
            for name in _STEP_NAMES[start_at:]:
                current_step = name
                category = self._category_for(name)
                if name == "probe-input":
                    self._run_step(job_dir, name, lambda: self._run_probe(job_dir, request, context), cancel_event, progress, logger)
                elif name == "extract-scene-frames":
                    self._run_step(job_dir, name, lambda: self._run_extract(job_dir, request, context, "scene", cancel_event), cancel_event, progress, logger)
                elif name == "extract-interval-frames":
                    self._run_step(job_dir, name, lambda: self._run_extract(job_dir, request, context, "interval", cancel_event), cancel_event, progress, logger)
                else:
                    self._run_step(job_dir, name, lambda: self._run_render(job_dir, context), cancel_event, progress, logger)
            if not artifacts_valid(job_dir):
                raise _InputInvalid("final artifacts did not validate")
            self._store.update(job_dir, self._mark_succeeded)
            logger.info("job status=succeeded")
            return job_dir
        except (ProcessCancelledError, KeyboardInterrupt):
            self._mark_interrupted(job_dir, current_step)
            logger.info("job status=interrupted step=%s", current_step or "none")
            raise ScriptDraftInterrupted(job_dir) from None
        except ScriptDraftInterrupted:
            raise
        except Exception as exc:
            failure_category = "job logging" if isinstance(exc, _LoggerSetupFailure) else category
            self._mark_failed(job_dir, current_step, failure_category, None)
            logger.error("step=%s category=%s exception=%s", current_step or "none", failure_category, type(exc).__name__)
            raise ScriptDraftFailed(job_dir, failure_category) from None

    def _claim(self, job_dir: Path, expected_settings: dict[str, Any], allowed: set[JobStatus]) -> None:
        def claim(manifest: JobManifest) -> None:
            if manifest.mode is not JobMode.SCRIPT_DRAFT:
                raise RuntimeError("job is not a script draft job")
            if manifest.status is JobStatus.RUNNING:
                raise RuntimeError("script draft job is already running")
            if manifest.status not in allowed:
                raise RuntimeError("script draft job cannot be resumed from its current status")
            self._validate_manifest_inputs(manifest)
            if not self._settings_valid(manifest.settings) or manifest.settings != expected_settings:
                raise _InputInvalid("job settings do not match script draft settings")
            manifest.status = JobStatus.RUNNING
            manifest.last_error = None

        self._store.update(job_dir, claim)

    def _reconcile(self, job_dir: Path) -> tuple[dict[str, Any], int]:
        manifest = self._store.load(job_dir, recover_interrupted=False)
        if not self._settings_valid(manifest.settings):
            raise _InputInvalid("job settings do not match script draft settings")
        context: dict[str, Any] = {}
        validators: dict[str, Callable[[], bool]] = {
            "probe-input": lambda: self._load_video_info(job_dir, context),
            "extract-scene-frames": lambda: self._load_candidates(job_dir, "scene", context),
            "extract-interval-frames": lambda: self._load_candidates(job_dir, "interval", context),
            "render-draft": lambda: artifacts_valid(job_dir),
        }
        for index, name in enumerate(_STEP_NAMES):
            step = manifest.steps.get(name)
            if step is None or step.status is not StepStatus.SUCCEEDED or not validators[name]():
                self._store.update(job_dir, lambda latest: self._reset_from(latest, index))
                return context, index
        return context, len(_STEP_NAMES)

    def _run_step(
        self, job_dir: Path, name: str, operation: Callable[[], None], cancel_event: object | None,
        progress: Progress | None, logger: logging.Logger | _NoOpLogger,
    ) -> None:
        self._store.update(job_dir, lambda manifest: self._mark_step_running(manifest, name))
        logger.info("step=%s status=running", name)
        self._raise_if_cancelled(cancel_event)
        if progress is not None:
            progress(name)
        operation()
        self._raise_if_cancelled(cancel_event)
        self._store.update(job_dir, lambda manifest: self._mark_step_succeeded(manifest, name))
        logger.info("step=%s status=succeeded exit_code=0", name)

    def _run_probe(self, job_dir: Path, request: ScriptDraftRequest, context: dict[str, Any]) -> None:
        info = self._probe(request.input_path)
        if not isinstance(info, VideoInfo):
            raise _InputInvalid("probe output is invalid")
        _, work, _ = self._contained_job_paths(job_dir)
        self._write_json(work / "video-info.json", {
            "duration_ms": info.duration_ms, "width": info.width, "height": info.height,
        })
        if not self._load_video_info(job_dir, context):
            raise _InputInvalid("probe output is invalid")
        versions = self._tool_versions()
        ffmpeg = getattr(versions, "ffmpeg", None)
        ffprobe = getattr(versions, "ffprobe", None)
        if not self._nonblank(ffmpeg) or not self._nonblank(ffprobe):
            raise _InputInvalid("media tool provenance is invalid")

        def record(manifest: JobManifest) -> None:
            manifest.tools["python"] = ".".join(map(str, sys.version_info[:3]))
            manifest.tools["ffmpeg"] = ffmpeg
            manifest.tools["ffprobe"] = ffprobe

        self._store.update(job_dir, record)

    def _run_extract(
        self, job_dir: Path, request: ScriptDraftRequest, context: dict[str, Any], kind: str,
        cancel_event: object | None,
    ) -> None:
        self._require_video_info(context)
        extractor = self._extract_scene if kind == "scene" else self._extract_interval
        _, work, _ = self._contained_job_paths(job_dir)
        candidates = extractor(request.input_path, work, cancel_event=cancel_event)
        self._save_candidates(job_dir, kind, candidates)
        if not self._load_candidates(job_dir, kind, context):
            raise _InputInvalid("extracted frames did not validate")

    def _run_render(self, job_dir: Path, context: dict[str, Any]) -> None:
        info = self._require_video_info(context)
        scene = self._require_candidates(context, "scene")
        interval = self._require_candidates(context, "interval")
        self._clear_outputs(job_dir)
        _, _, outputs = self._contained_job_paths(job_dir)
        self._render(outputs, info, merge_candidates([*scene, *interval]))
        self._record_artifacts(job_dir)
        if not artifacts_valid(job_dir):
            raise _InputInvalid("final artifacts did not validate")

    def _save_candidates(self, job_dir: Path, kind: str, candidates: Sequence[FrameCandidate]) -> None:
        if kind not in _CANDIDATE_STATE or any(candidate.reason != kind for candidate in candidates):
            raise _InputInvalid("candidate output is invalid")
        if kind == "interval" and (
            not candidates
            or candidates[0].time_ms != 0
            or sum(candidate.time_ms == 0 for candidate in candidates) != 1
        ):
            raise _InputInvalid("candidate output is invalid")
        _, work, _ = self._contained_job_paths(job_dir)
        state: list[dict[str, object]] = []
        for candidate in candidates:
            path = Path(candidate.source_path).resolve(strict=True)
            if path.suffix != f".{FRAME_FORMAT}" or not path.is_relative_to(work / f"{kind}-frames"):
                raise _InputInvalid("candidate frame is outside its work directory")
            ref = fingerprint_file(path)
            state.append({"time_ms": candidate.time_ms, "path": path.relative_to(work).as_posix(), "sha256": ref.sha256})
        self._write_json(job_dir / "work" / _CANDIDATE_STATE[kind], {"kind": kind, "candidates": state})

    def _load_video_info(self, job_dir: Path, context: dict[str, Any]) -> bool:
        try:
            _, work, _ = self._contained_job_paths(job_dir)
            path = work / "video-info.json"
            fingerprint_file(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"duration_ms", "width", "height"}:
                return False
            context["video_info"] = VideoInfo(payload["duration_ms"], payload["width"], payload["height"])
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return True

    def _load_candidates(self, job_dir: Path, kind: str, context: dict[str, Any]) -> bool:
        try:
            if kind not in _CANDIDATE_STATE:
                return False
            _, work, _ = self._contained_job_paths(job_dir)
            path = work / _CANDIDATE_STATE[kind]
            fingerprint_file(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"kind", "candidates"} or payload["kind"] != kind:
                return False
            raw_candidates = payload["candidates"]
            if not isinstance(raw_candidates, list):
                return False
            candidates: list[FrameCandidate] = []
            for raw in raw_candidates:
                if not isinstance(raw, dict) or set(raw) != {"time_ms", "path", "sha256"}:
                    return False
                if not isinstance(raw["path"], str) or not isinstance(raw["sha256"], str):
                    return False
                relative = Path(raw["path"])
                if relative.is_absolute() or ".." in relative.parts:
                    return False
                image = (work / relative).resolve(strict=True)
                if image.suffix != f".{FRAME_FORMAT}" or not image.is_relative_to(work / f"{kind}-frames"):
                    return False
                if fingerprint_file(image).sha256 != raw["sha256"]:
                    return False
                candidate = FrameCandidate(raw["time_ms"], image, kind)
                if candidate.time_ms > self._require_video_info(context).duration_ms:
                    return False
                self._validate_png(image)
                candidates.append(candidate)
            if kind == "interval" and (
                not candidates
                or candidates[0].time_ms != 0
                or sum(candidate.time_ms == 0 for candidate in candidates) != 1
            ):
                return False
            context[f"{kind}_candidates"] = candidates
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return True

    def _record_artifacts(self, job_dir: Path) -> None:
        _, _, outputs = self._contained_job_paths(job_dir)
        paths = [*sorted((outputs / "frames").glob(f"*.{FRAME_FORMAT}")), outputs / "frame-index.json", outputs / "script.md"]
        records: list[ArtifactRecord] = []
        for path in paths:
            kind = "frame-png" if path.suffix == ".png" else "frame-index-json" if path.suffix == ".json" else "script-markdown"
            records.append(fingerprint_artifact(job_dir, path, kind))
        if len(records) != len(paths):
            raise _InputInvalid("final artifact list is invalid")
        self._store.update(job_dir, lambda manifest: setattr(manifest, "artifacts", records))

    @staticmethod
    def _validate_png(path: Path) -> None:
        try:
            with path.open("rb") as handle:
                header = handle.read(24)
            if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
                raise ValueError
            width, height = struct.unpack(">II", header[16:24])
            if width == 0 or height == 0 or width > MAX_FRAME_EDGE or height > MAX_FRAME_EDGE:
                raise ValueError
        except (OSError, ValueError, struct.error):
            raise ValueError("candidate output is not a valid PNG") from None

    def _clear_outputs(self, job_dir: Path) -> None:
        _, _, outputs = self._contained_job_paths(job_dir)
        shutil.rmtree(outputs, ignore_errors=True)
        outputs.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _contained_job_paths(job_dir: Path) -> tuple[Path, Path, Path]:
        root = Path(job_dir).resolve(strict=True)
        intended_work = root / "work"
        intended_outputs = root / "outputs"
        work = intended_work.resolve(strict=True)
        outputs = intended_outputs.resolve(strict=True)
        if work != intended_work or outputs != intended_outputs or work == outputs:
            raise _InputInvalid("job state path is outside job")
        return root, work, outputs

    @staticmethod
    def _write_json(path: Path, data: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _mark_step_running(manifest: JobManifest, name: str) -> None:
        manifest.steps[name] = StepRecord(status=StepStatus.RUNNING, started_at=ScriptDraftService._timestamp())

    @staticmethod
    def _mark_step_succeeded(manifest: JobManifest, name: str) -> None:
        previous = manifest.steps.get(name)
        manifest.steps[name] = StepRecord(
            status=StepStatus.SUCCEEDED,
            started_at=previous.started_at if previous else ScriptDraftService._timestamp(),
            finished_at=ScriptDraftService._timestamp(), exit_code=0,
        )

    @staticmethod
    def _mark_succeeded(manifest: JobManifest) -> None:
        manifest.status = JobStatus.SUCCEEDED
        manifest.last_error = None

    @staticmethod
    def _reset_from(manifest: JobManifest, index: int) -> None:
        invalidated = set(_STEP_NAMES[index:])
        for name in invalidated:
            manifest.steps.pop(name, None)
        if "render-draft" in invalidated:
            manifest.artifacts = []
        manifest.last_error = None

    def _mark_failed(self, job_dir: Path, current_step: str | None, category: str, exit_code: int | None) -> None:
        def fail(manifest: JobManifest) -> None:
            manifest.status = JobStatus.FAILED
            manifest.last_error = category
            if current_step is not None:
                previous = manifest.steps.get(current_step)
                manifest.steps[current_step] = StepRecord(
                    status=StepStatus.FAILED,
                    started_at=previous.started_at if previous else self._timestamp(),
                    finished_at=self._timestamp(), exit_code=exit_code, error=category,
                )
        self._store.update(job_dir, fail)

    def _mark_interrupted(self, job_dir: Path, current_step: str | None) -> None:
        def interrupt(manifest: JobManifest) -> None:
            manifest.status = JobStatus.INTERRUPTED
            if current_step is not None:
                previous = manifest.steps.get(current_step)
                manifest.steps[current_step] = StepRecord(
                    status=StepStatus.INTERRUPTED,
                    started_at=previous.started_at if previous else self._timestamp(),
                    finished_at=self._timestamp(),
                )
        self._store.update(job_dir, interrupt)

    @staticmethod
    def _category_for(name: str) -> str:
        return "input validation" if name == "probe-input" else "FFmpeg" if name.startswith("extract-") else "draft rendering"

    @staticmethod
    def _raise_if_cancelled(cancel_event: object | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessCancelledError("script draft")

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _nonblank(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _require_video_info(context: dict[str, Any]) -> VideoInfo:
        value = context.get("video_info")
        if not isinstance(value, VideoInfo):
            raise _InputInvalid("video information is unavailable")
        return value

    @staticmethod
    def _require_candidates(context: dict[str, Any], kind: str) -> list[FrameCandidate]:
        value = context.get(f"{kind}_candidates")
        if not isinstance(value, list) or not all(isinstance(item, FrameCandidate) for item in value):
            raise _InputInvalid("frame candidates are unavailable")
        return value

    @staticmethod
    def _validate_request(request: ScriptDraftRequest) -> None:
        if not isinstance(request, ScriptDraftRequest):
            raise ValueError("request must be a ScriptDraftRequest")
        if not isinstance(request.input_path, Path) or not isinstance(request.output_dir, Path):
            raise ValueError("request paths must be Path values")
        if not isinstance(request.name, str) or not request.name.strip():
            raise ValueError("request name must not be empty")

    @staticmethod
    def _settings() -> dict[str, Any]:
        return {
            "scene_threshold": SCENE_THRESHOLD,
            "interval_ms": INTERVAL_MS,
            "merge_tolerance_ms": MERGE_TOLERANCE_MS,
            "max_frame_edge": MAX_FRAME_EDGE,
            "frame_format": FRAME_FORMAT,
        }

    @staticmethod
    def _settings_valid(settings: object) -> bool:
        expected = ScriptDraftService._settings()
        if not isinstance(settings, dict) or set(settings) != set(expected):
            return False
        return (
            type(settings["scene_threshold"]) is float
            and settings["scene_threshold"] == SCENE_THRESHOLD
            and all(
                type(settings[name]) is int and settings[name] == expected[name]
                for name in ("interval_ms", "merge_tolerance_ms", "max_frame_edge")
            )
            and type(settings["frame_format"]) is str
            and settings["frame_format"] == FRAME_FORMAT
        )

    def _request_from_manifest(self, manifest: JobManifest, job_dir: Path) -> ScriptDraftRequest:
        try:
            request = ScriptDraftRequest(Path(manifest.inputs[0].path), manifest.name, job_dir.parent)
            self._validate_request(request)
            return request
        except (IndexError, TypeError, ValueError) as exc:
            raise _InputInvalid("job request is invalid") from exc

    @staticmethod
    def _validate_manifest_inputs(manifest: JobManifest) -> None:
        if len(manifest.inputs) != 1 or not isinstance(manifest.inputs[0], InputRef):
            raise _InputInvalid("script draft must have exactly one input")
        try:
            current = fingerprint_file(Path(manifest.inputs[0].path))
        except (OSError, ValueError) as exc:
            raise _InputInvalid("script draft input is unavailable") from exc
        if current != manifest.inputs[0]:
            raise _InputInvalid("script draft input changed")
