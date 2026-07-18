"""Resumable, local orchestration for transcription jobs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sys
import wave
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
from minoru_studio.processes import ProcessCancelledError, ProcessResult, run_cancellable_process
from minoru_studio.transcribe.contracts import (
    WorkerRequest,
    WorkerResult,
    load_worker_result,
    save_worker_request,
)
from minoru_studio.transcribe.media import (
    MediaInfo,
    extract_audio,
    probe_media,
    read_media_tool_versions,
    render_preview,
)
from minoru_studio.transcribe.models import (
    default_model_cache_dir,
    model_is_complete,
    require_model_capacity,
)
from minoru_studio.transcribe.subtitles import write_artifacts


_STEP_NAMES = (
    "probe-input",
    "extract-audio",
    "transcribe",
    "render-artifacts",
)
_PREVIEW_STEP = "render-preview"
_ARTIFACTS = {
    "transcript-txt": "transcript.txt",
    "subtitles-srt": "subtitles.srt",
    "subtitles-vtt": "subtitles.vtt",
}
_PREVIEW_ARTIFACT = "preview-mp4"
_PREVIEW_FONT = "windows-japanese-auto"


@dataclass(frozen=True, slots=True)
class TranscribeRequest:
    input_path: Path
    name: str
    output_dir: Path
    model: str = "small"
    language: str = "ja"
    normalize: bool = False
    denoise: bool = False
    preview: bool = False


class TranscriptionFailed(RuntimeError):
    """A stable, content-free transcription failure."""

    def __init__(self, job_dir: Path, category: str) -> None:
        self.job_dir = Path(job_dir)
        self.category = category
        detail = (
            "; selected model is not cached; rerun with -AllowModelDownload"
            if category == "model unavailable"
            else ""
        )
        super().__init__(f"{category}{detail}")


class TranscriptionInterrupted(RuntimeError):
    """Raised after cancellation has been durably recorded."""

    def __init__(self, job_dir: Path) -> None:
        self.job_dir = Path(job_dir)
        super().__init__("transcription interrupted")


class _InputInvalid(ValueError):
    pass


class _ModelUnavailable(RuntimeError):
    pass


class _WorkerExit(RuntimeError):
    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code
        super().__init__("worker process returned a non-zero exit code")


Progress = Callable[[str], object]


class TranscribeService:
    """Run and resume a transcription job without exposing recognized text."""

    def __init__(
        self,
        *,
        store: JobStore | None = None,
        probe: Callable[[Path], MediaInfo] = probe_media,
        extract: Callable[..., Path] = extract_audio,
        worker: Callable[..., ProcessResult] = run_cancellable_process,
        artifacts: Callable[[Path, WorkerResult], tuple[Path, Path, Path]] = write_artifacts,
        preview_renderer: Callable[..., Path] = render_preview,
        tool_versions: Callable[[], object] = read_media_tool_versions,
        model_cache_dir: Path | None = None,
        model_complete: Callable[[Path, str], bool] = model_is_complete,
        require_capacity: Callable[[Path, str], int] = require_model_capacity,
        logger_factory: Callable[[Path], logging.Logger] = configure_job_logger,
    ) -> None:
        self._store = store or JobStore()
        self._probe = probe
        self._extract = extract
        self._worker = worker
        self._artifacts = artifacts
        self._preview_renderer = preview_renderer
        self._tool_versions = tool_versions
        self._model_cache_dir = Path(model_cache_dir) if model_cache_dir is not None else default_model_cache_dir()
        self._model_complete = model_complete
        self._require_capacity = require_capacity
        self._logger_factory = logger_factory

    def create_and_run(
        self,
        request: TranscribeRequest,
        *,
        allow_model_download: bool = False,
        cancel_event: object | None = None,
        progress: Progress | None = None,
    ) -> Path:
        self._validate_request(request)
        settings = self._settings_for(request)
        job_dir = self._store.create(
            request.output_dir,
            request.name,
            JobMode.TRANSCRIBE,
            (request.input_path,),
            settings,
        )
        try:
            self._claim(job_dir, settings, {JobStatus.PENDING})
        except _InputInvalid as exc:
            self._mark_failed(job_dir, None, "input validation", exc, None)
            raise TranscriptionFailed(job_dir, "input validation") from None
        except JobLockedError:
            raise RuntimeError("transcription job is already running") from None
        return self._execute(
            job_dir,
            request,
            allow_model_download=allow_model_download,
            cancel_event=cancel_event,
            progress=progress,
        )

    def resume(
        self,
        job_dir: Path,
        *,
        allow_model_download: bool = False,
        cancel_event: object | None = None,
        progress: Progress | None = None,
    ) -> Path:
        job_dir = Path(job_dir).resolve(strict=True)
        manifest = self._store.load(job_dir, recover_interrupted=False)
        if manifest.mode is not JobMode.TRANSCRIBE:
            raise RuntimeError("job is not a transcription job")
        if manifest.status is JobStatus.RUNNING:
            raise RuntimeError("transcription job is already running")
        if manifest.status not in {JobStatus.PENDING, JobStatus.INTERRUPTED, JobStatus.FAILED}:
            raise RuntimeError("transcription job cannot be resumed from its current status")
        try:
            request = self._request_from_manifest(manifest, job_dir)
            self._claim(
                job_dir,
                self._settings_for(request),
                {JobStatus.PENDING, JobStatus.INTERRUPTED, JobStatus.FAILED},
            )
        except _InputInvalid as exc:
            self._mark_failed(job_dir, None, "input validation", exc, None)
            raise TranscriptionFailed(job_dir, "input validation") from None
        except JobLockedError:
            raise RuntimeError("transcription job is already running") from None
        return self._execute(
            job_dir,
            request,
            allow_model_download=allow_model_download,
            cancel_event=cancel_event,
            progress=progress,
        )

    def _execute(
        self,
        job_dir: Path,
        request: TranscribeRequest,
        *,
        allow_model_download: bool,
        cancel_event: object | None,
        progress: Progress | None,
    ) -> Path:
        logger = self._logger_factory(job_dir)
        current_step: str | None = None
        category = "input validation"
        exit_code: int | None = None
        try:
            context, start_at = self._reconcile(job_dir, request)
            steps = self._step_names(request.preview)
            for index in range(start_at, len(steps)):
                current_step = steps[index]
                category = self._category_for(current_step)
                exit_code = None
                if current_step == "probe-input":
                    exit_code = self._run_step(
                        job_dir,
                        current_step,
                        lambda: self._run_probe(job_dir, request, context),
                        cancel_event,
                        progress,
                        logger,
                    )
                elif current_step == "extract-audio":
                    exit_code = self._run_step(
                        job_dir,
                        current_step,
                        lambda: self._run_extract(job_dir, request, context, cancel_event),
                        cancel_event,
                        progress,
                        logger,
                    )
                elif current_step == "transcribe":
                    exit_code = self._run_step(
                        job_dir,
                        current_step,
                        lambda: self._run_transcribe(
                            job_dir, request, context, allow_model_download, cancel_event
                        ),
                        cancel_event,
                        progress,
                        logger,
                    )
                elif current_step == "render-artifacts":
                    exit_code = self._run_step(
                        job_dir,
                        current_step,
                        lambda: self._run_artifacts(job_dir, context),
                        cancel_event,
                        progress,
                        logger,
                    )
                else:
                    exit_code = self._run_step(
                        job_dir,
                        current_step,
                        lambda: self._run_preview(job_dir, request, context, cancel_event),
                        cancel_event,
                        progress,
                        logger,
                    )
            if not self._artifacts_valid(job_dir, request.preview):
                raise _InputInvalid("rendered artifacts did not validate")
            self._store.update(job_dir, self._mark_succeeded)
            logger.info("job status=succeeded")
            return job_dir
        except (ProcessCancelledError, KeyboardInterrupt):
            self._mark_interrupted(job_dir, current_step)
            logger.info("job status=interrupted step=%s", current_step or "none")
            raise TranscriptionInterrupted(job_dir) from None
        except TranscriptionInterrupted:
            raise
        except Exception as exc:
            if isinstance(exc, _WorkerExit):
                exit_code = exc.exit_code
            failure_category = "model unavailable" if isinstance(exc, _ModelUnavailable) else category
            self._mark_failed(job_dir, current_step, failure_category, exc, exit_code)
            logger.error(
                "step=%s category=%s exception=%s exit_code=%s",
                current_step or "none",
                failure_category,
                type(exc).__name__,
                exit_code if exit_code is not None else "none",
            )
            raise TranscriptionFailed(job_dir, failure_category) from None

    def _claim(
        self,
        job_dir: Path,
        expected_settings: dict[str, Any],
        allowed_statuses: set[JobStatus],
    ) -> None:
        def claim(manifest: JobManifest) -> None:
            if manifest.mode is not JobMode.TRANSCRIBE:
                raise RuntimeError("job is not a transcription job")
            if manifest.status is JobStatus.RUNNING:
                raise RuntimeError("transcription job is already running")
            if manifest.status not in allowed_statuses:
                raise RuntimeError("transcription job cannot be resumed from its current status")
            self._validate_manifest_inputs(manifest)
            if manifest.settings != expected_settings:
                raise _InputInvalid("job settings do not match transcription settings")
            manifest.status = JobStatus.RUNNING
            manifest.last_error = None

        self._store.update(job_dir, claim)

    def _reconcile(self, job_dir: Path, request: TranscribeRequest) -> tuple[dict[str, Any], int]:
        manifest = self._store.load(job_dir, recover_interrupted=False)
        context: dict[str, Any] = {}
        steps = self._step_names(request.preview)
        validators: dict[str, Callable[[], bool]] = {
            "probe-input": lambda: self._load_media_info(job_dir, context),
            "extract-audio": lambda: self._wave_is_valid(job_dir / "work" / "inference.wav"),
            "transcribe": lambda: self._load_worker_result(job_dir, request, context),
            "render-artifacts": lambda: self._artifacts_valid(job_dir, False),
            "render-preview": lambda: self._artifacts_valid(job_dir, True),
        }
        for index, name in enumerate(steps):
            step = manifest.steps.get(name)
            if step is None or step.status is not StepStatus.SUCCEEDED or not validators[name]():
                self._store.update(
                    job_dir,
                    lambda latest: self._reset_from(latest, steps, index),
                )
                return context, index
        return context, len(steps)

    def _run_step(
        self,
        job_dir: Path,
        name: str,
        operation: Callable[[], int],
        cancel_event: object | None,
        progress: Progress | None,
        logger: logging.Logger,
    ) -> int:
        self._store.update(job_dir, lambda manifest: self._mark_step_running(manifest, name))
        logger.info("step=%s status=running", name)
        self._raise_if_cancelled(cancel_event)
        if progress is not None:
            progress(name)
        exit_code = operation()
        self._raise_if_cancelled(cancel_event)
        self._store.update(
            job_dir,
            lambda manifest: self._mark_step_succeeded(manifest, name, exit_code),
        )
        logger.info("step=%s status=succeeded exit_code=%s", name, exit_code)
        return exit_code

    def _run_probe(self, job_dir: Path, request: TranscribeRequest, context: dict[str, Any]) -> int:
        media_info = self._probe(request.input_path)
        if not media_info.has_audio:
            raise _InputInvalid("input has no audio stream")
        path = job_dir / "work" / "media-info.json"
        self._write_json(path, {
            "duration_ms": media_info.duration_ms,
            "has_audio": media_info.has_audio,
            "has_video": media_info.has_video,
        })
        if not self._load_media_info(job_dir, context):
            raise _InputInvalid("media probe output did not validate")
        versions = self._tool_versions()
        ffmpeg = getattr(versions, "ffmpeg", None)
        ffprobe = getattr(versions, "ffprobe", None)
        if not self._nonblank(ffmpeg) or not self._nonblank(ffprobe):
            raise _InputInvalid("media tool provenance did not validate")

        def record_tools(manifest: JobManifest) -> None:
            manifest.tools["python"] = ".".join(map(str, sys.version_info[:3]))
            manifest.tools["ffmpeg"] = ffmpeg
            manifest.tools["ffprobe"] = ffprobe

        self._store.update(job_dir, record_tools)
        return 0

    def _run_extract(
        self,
        job_dir: Path,
        request: TranscribeRequest,
        context: dict[str, Any],
        cancel_event: object | None,
    ) -> int:
        self._require_media_info(context)
        destination = job_dir / "work" / "inference.wav"
        self._extract(
            request.input_path,
            destination,
            normalize=request.normalize,
            denoise=request.denoise,
            cancel_event=cancel_event,
        )
        if not self._wave_is_valid(destination):
            raise _InputInvalid("extracted audio did not validate")
        return 0

    def _run_transcribe(
        self,
        job_dir: Path,
        request: TranscribeRequest,
        context: dict[str, Any],
        allow_model_download: bool,
        cancel_event: object | None,
    ) -> int:
        wav = job_dir / "work" / "inference.wav"
        if not self._wave_is_valid(wav):
            raise _InputInvalid("inference audio did not validate")
        if not self._model_complete(self._model_cache_dir, request.model):
            if not allow_model_download:
                raise _ModelUnavailable("selected model is not cached")
            try:
                self._require_capacity(self._model_cache_dir, request.model)
            except Exception as exc:
                raise _ModelUnavailable("model download capacity is unavailable") from exc
        result_path = job_dir / "work" / "raw-segments.json"
        request_path = job_dir / "work" / "worker-request.json"
        save_worker_request(
            request_path,
            WorkerRequest(
                schema_version=1,
                input_wav=str(wav.resolve()),
                output_json=str(result_path.resolve()),
                model=request.model,
                language=request.language,
                device="cpu",
                compute_type="int8",
                vad_filter=True,
                word_timestamps=True,
                model_cache_dir=str(self._model_cache_dir.resolve()),
                allow_model_download=allow_model_download,
            ),
        )
        process_result = self._worker(
            [
                sys.executable,
                "-m",
                "minoru_studio.transcribe.worker",
                str(request_path),
            ],
            cancel_event=cancel_event,
        )
        if process_result.returncode != 0:
            raise _WorkerExit(process_result.returncode)
        if not self._load_worker_result(job_dir, request, context):
            raise _InputInvalid("worker result did not validate")
        result = self._require_worker_result(context)
        self._store.update(
            job_dir,
            lambda manifest: manifest.tools.__setitem__("faster-whisper", result.provider_version),
        )
        return process_result.returncode

    def _run_artifacts(self, job_dir: Path, context: dict[str, Any]) -> int:
        result = self._require_worker_result(context)
        paths = self._artifacts(job_dir / "outputs", result)
        expected = tuple((job_dir / "outputs" / filename) for filename in _ARTIFACTS.values())
        if tuple(Path(path) for path in paths) != expected:
            raise _InputInvalid("subtitle artifact paths did not validate")
        self._record_artifacts(job_dir, dict(zip(_ARTIFACTS, expected, strict=True)))
        if not self._artifacts_valid(job_dir, False):
            raise _InputInvalid("subtitle artifacts did not validate")
        return 0

    def _run_preview(
        self,
        job_dir: Path,
        request: TranscribeRequest,
        context: dict[str, Any],
        cancel_event: object | None,
    ) -> int:
        media_info = self._require_media_info(context)
        destination = job_dir / "outputs" / "preview.mp4"
        self._preview_renderer(
            request.input_path,
            job_dir / "outputs" / _ARTIFACTS["subtitles-srt"],
            destination,
            media_info,
            cancel_event=cancel_event,
        )
        self._record_artifacts(job_dir, {_PREVIEW_ARTIFACT: destination})
        if not self._artifacts_valid(job_dir, True):
            raise _InputInvalid("preview artifact did not validate")
        return 0

    def _record_artifacts(self, job_dir: Path, paths: dict[str, Path]) -> None:
        records = [fingerprint_artifact(job_dir, path, kind) for kind, path in paths.items()]

        def record(manifest: JobManifest) -> None:
            kinds = set(paths)
            manifest.artifacts = [item for item in manifest.artifacts if item.kind not in kinds]
            manifest.artifacts.extend(records)

        self._store.update(job_dir, record)

    def _artifacts_valid(self, job_dir: Path, include_preview: bool) -> bool:
        manifest = self._store.load(job_dir, recover_interrupted=False)
        expected = dict(_ARTIFACTS)
        if include_preview:
            expected[_PREVIEW_ARTIFACT] = "preview.mp4"
        for kind, filename in expected.items():
            matching = [item for item in manifest.artifacts if item.kind == kind]
            if len(matching) != 1:
                return False
            path = job_dir / "outputs" / filename
            try:
                if fingerprint_artifact(job_dir, path, kind) != matching[0]:
                    return False
            except (OSError, ValueError):
                return False
        return True

    def _load_media_info(self, job_dir: Path, context: dict[str, Any]) -> bool:
        path = job_dir / "work" / "media-info.json"
        try:
            fingerprint_file(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            if set(data) != {"duration_ms", "has_audio", "has_video"}:
                return False
            duration = data["duration_ms"]
            if type(duration) is not int or duration <= 0:
                return False
            if type(data["has_audio"]) is not bool or type(data["has_video"]) is not bool:
                return False
            context["media_info"] = MediaInfo(duration, data["has_audio"], data["has_video"])
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        return True

    def _wave_is_valid(self, path: Path) -> bool:
        try:
            fingerprint_file(path)
            with wave.open(str(path), "rb") as input_file:
                return (
                    input_file.getcomptype() == "NONE"
                    and input_file.getnchannels() == 1
                    and input_file.getframerate() == 16_000
                    and input_file.getsampwidth() == 2
                    and input_file.getnframes() > 0
                )
        except (OSError, ValueError, wave.Error):
            return False

    def _load_worker_result(
        self,
        job_dir: Path,
        request: TranscribeRequest,
        context: dict[str, Any],
    ) -> bool:
        try:
            path = job_dir / "work" / "raw-segments.json"
            fingerprint_file(path)
            result = load_worker_result(path)
            if result.model != request.model:
                return False
            context["worker_result"] = result
        except (OSError, ValueError):
            return False
        return True

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _mark_step_running(manifest: JobManifest, name: str) -> None:
        manifest.steps[name] = StepRecord(
            status=StepStatus.RUNNING,
            started_at=TranscribeService._timestamp(),
        )

    @staticmethod
    def _mark_step_succeeded(manifest: JobManifest, name: str, exit_code: int) -> None:
        previous = manifest.steps.get(name)
        manifest.steps[name] = StepRecord(
            status=StepStatus.SUCCEEDED,
            started_at=previous.started_at if previous is not None else TranscribeService._timestamp(),
            finished_at=TranscribeService._timestamp(),
            exit_code=exit_code,
        )

    @staticmethod
    def _mark_succeeded(manifest: JobManifest) -> None:
        manifest.status = JobStatus.SUCCEEDED
        manifest.last_error = None

    @staticmethod
    def _reset_from(manifest: JobManifest, steps: tuple[str, ...], index: int) -> None:
        invalidated = set(steps[index:])
        for name in invalidated:
            manifest.steps.pop(name, None)
        artifact_kinds: set[str] = set()
        if "render-artifacts" in invalidated:
            artifact_kinds.update(_ARTIFACTS)
        if _PREVIEW_STEP in invalidated:
            artifact_kinds.add(_PREVIEW_ARTIFACT)
        manifest.artifacts = [item for item in manifest.artifacts if item.kind not in artifact_kinds]
        manifest.last_error = None

    def _mark_failed(
        self,
        job_dir: Path,
        current_step: str | None,
        category: str,
        exc: BaseException,
        exit_code: int | None,
    ) -> None:
        def fail(manifest: JobManifest) -> None:
            manifest.status = JobStatus.FAILED
            manifest.last_error = category
            if current_step is not None:
                previous = manifest.steps.get(current_step)
                manifest.steps[current_step] = StepRecord(
                    status=StepStatus.FAILED,
                    started_at=(
                        previous.started_at
                        if previous is not None
                        else TranscribeService._timestamp()
                    ),
                    finished_at=TranscribeService._timestamp(),
                    exit_code=exit_code,
                    error=category,
                )

        self._store.update(job_dir, fail)

    def _mark_interrupted(self, job_dir: Path, current_step: str | None) -> None:
        def interrupt(manifest: JobManifest) -> None:
            manifest.status = JobStatus.INTERRUPTED
            if current_step is not None:
                previous = manifest.steps.get(current_step)
                manifest.steps[current_step] = StepRecord(
                    status=StepStatus.INTERRUPTED,
                    started_at=(
                        previous.started_at
                        if previous is not None
                        else TranscribeService._timestamp()
                    ),
                    finished_at=TranscribeService._timestamp(),
                )

        self._store.update(job_dir, interrupt)

    @staticmethod
    def _raise_if_cancelled(cancel_event: object | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessCancelledError("transcription")

    @staticmethod
    def _category_for(name: str) -> str:
        return {
            "probe-input": "input validation",
            "extract-audio": "FFmpeg",
            "transcribe": "worker",
            "render-artifacts": "subtitle rendering",
            "render-preview": "preview",
        }[name]

    @staticmethod
    def _step_names(preview: bool) -> tuple[str, ...]:
        return (*_STEP_NAMES, *((_PREVIEW_STEP,) if preview else ()))

    @staticmethod
    def _nonblank(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _require_media_info(context: dict[str, Any]) -> MediaInfo:
        value = context.get("media_info")
        if not isinstance(value, MediaInfo):
            raise _InputInvalid("media information is not available")
        return value

    @staticmethod
    def _require_worker_result(context: dict[str, Any]) -> WorkerResult:
        value = context.get("worker_result")
        if not isinstance(value, WorkerResult):
            raise _InputInvalid("worker result is not available")
        return value

    @staticmethod
    def _validate_request(request: TranscribeRequest) -> None:
        if not isinstance(request, TranscribeRequest):
            raise ValueError("request must be a TranscribeRequest")
        if not isinstance(request.input_path, Path) or not isinstance(request.output_dir, Path):
            raise ValueError("request paths must be Path values")
        if not isinstance(request.name, str) or not request.name.strip():
            raise ValueError("request name must not be empty")
        if not isinstance(request.model, str) or not request.model.strip():
            raise ValueError("request model must not be empty")
        if not isinstance(request.language, str) or not request.language.strip():
            raise ValueError("request language must not be empty")
        if any(type(value) is not bool for value in (request.normalize, request.denoise, request.preview)):
            raise ValueError("request filters and preview must be booleans")

    @staticmethod
    def _settings_for(request: TranscribeRequest) -> dict[str, Any]:
        return {
            "model": request.model,
            "language": request.language,
            "device": "cpu",
            "compute_type": "int8",
            "vad_filter": True,
            "word_timestamps": True,
            "normalize": request.normalize,
            "denoise": request.denoise,
            "preview": request.preview,
            "preview_font": _PREVIEW_FONT,
        }

    def _request_from_manifest(self, manifest: JobManifest, job_dir: Path) -> TranscribeRequest:
        try:
            settings = manifest.settings
            request = TranscribeRequest(
                input_path=Path(manifest.inputs[0].path),
                name=manifest.name,
                output_dir=job_dir.parent,
                model=settings["model"],
                language=settings["language"],
                normalize=settings["normalize"],
                denoise=settings["denoise"],
                preview=settings["preview"],
            )
            self._validate_request(request)
            return request
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise _InputInvalid("job request is invalid") from exc

    @staticmethod
    def _validate_manifest_inputs(manifest: JobManifest) -> None:
        if len(manifest.inputs) != 1:
            raise _InputInvalid("transcription job must have exactly one input")
        expected = manifest.inputs[0]
        if not isinstance(expected, InputRef):
            raise _InputInvalid("transcription input is invalid")
        try:
            current = fingerprint_file(Path(expected.path))
        except (OSError, ValueError) as exc:
            raise _InputInvalid("transcription input is unavailable") from exc
        if current != expected:
            raise _InputInvalid("transcription input changed")
