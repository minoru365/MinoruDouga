from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import threading
import wave

import pytest

from minoru_studio.jobs.model import JobMode, JobStatus
from minoru_studio.jobs.store import JobStore
from minoru_studio.processes import ProcessResult
from minoru_studio.transcribe.contracts import (
    WorkerResult,
    load_worker_request,
    save_worker_result,
)
from minoru_studio.transcribe.media import MediaInfo, MediaToolVersions
from minoru_studio.transcribe.service import (
    TranscribeRequest,
    TranscribeService,
    TranscriptionFailed,
    TranscriptionInterrupted,
)


def _write_pcm(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(struct.pack("<hhh", -1200, 0, 1200))


class Collaborators:
    def __init__(self, *, has_audio: bool = True) -> None:
        self.has_audio = has_audio
        self.probe_calls = 0
        self.extract_calls = 0
        self.worker_calls = 0
        self.artifact_calls = 0
        self.preview_calls = 0
        self.model_complete = True
        self.worker_error: Exception | None = None

    def probe(self, source: Path) -> MediaInfo:
        self.probe_calls += 1
        return MediaInfo(1_000, self.has_audio, True)

    def extract(
        self,
        source: Path,
        destination: Path,
        *,
        normalize: bool,
        denoise: bool,
        cancel_event: object | None,
    ) -> Path:
        self.extract_calls += 1
        _write_pcm(destination)
        return destination

    def worker(self, args: list[str], *, cancel_event: object | None) -> ProcessResult:
        self.worker_calls += 1
        if self.worker_error is not None:
            raise self.worker_error
        request = load_worker_request(args[-1])
        save_worker_result(
            request.output_json,
            WorkerResult(
                schema_version=1,
                model=request.model,
                provider_version="1.2.1",
                language="ja",
                language_probability=1.0,
                duration_ms=1_000,
                duration_after_vad_ms=900,
                no_speech=True,
                segments=(),
            ),
        )
        return ProcessResult(0, "", "", "worker")

    def artifacts(self, directory: Path, result: WorkerResult) -> tuple[Path, Path, Path]:
        self.artifact_calls += 1
        directory.mkdir(parents=True, exist_ok=True)
        paths = (
            directory / "transcript.txt",
            directory / "subtitles.srt",
            directory / "subtitles.vtt",
        )
        for path, text in zip(paths, ("", "", "WEBVTT\n\n"), strict=True):
            path.write_text(text, encoding="utf-8")
        return paths

    def preview(
        self,
        source: Path,
        subtitles: Path,
        destination: Path,
        media_info: MediaInfo,
        *,
        cancel_event: object | None,
    ) -> Path:
        self.preview_calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"preview")
        return destination


def _service(tmp_path: Path, collaborators: Collaborators) -> TranscribeService:
    return TranscribeService(
        store=JobStore(),
        probe=collaborators.probe,
        extract=collaborators.extract,
        worker=collaborators.worker,
        artifacts=collaborators.artifacts,
        preview_renderer=collaborators.preview,
        tool_versions=lambda: MediaToolVersions("ffmpeg 7.1", "ffprobe 7.1"),
        model_cache_dir=tmp_path / "models",
        model_complete=lambda cache, model: collaborators.model_complete,
        require_capacity=lambda cache, model: 10_000_000_000,
    )


def _request(tmp_path: Path, source: Path, *, preview: bool = False) -> TranscribeRequest:
    return TranscribeRequest(
        input_path=source,
        name="demo",
        output_dir=tmp_path / "jobs",
        preview=preview,
    )


def test_create_runs_ordered_steps_records_content_free_provenance_and_preserves_source(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source bytes")
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    collaborators = Collaborators()

    job_dir = _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source))

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.SUCCEEDED
    assert list(manifest.steps) == [
        "probe-input", "extract-audio", "transcribe", "render-artifacts"
    ]
    assert {item.kind for item in manifest.artifacts} == {
        "transcript-txt", "subtitles-srt", "subtitles-vtt"
    }
    assert manifest.tools == {
        "python": f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}.{__import__('sys').version_info.micro}",
        "ffmpeg": "ffmpeg 7.1",
        "ffprobe": "ffprobe 7.1",
        "faster-whisper": "1.2.1",
    }
    assert "allow_model_download" not in manifest.settings
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash
    assert collaborators.preview_calls == 0


def test_input_without_audio_fails_before_worker_starts(tmp_path: Path):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"source")
    collaborators = Collaborators(has_audio=False)
    service = _service(tmp_path, collaborators)

    with pytest.raises(TranscriptionFailed, match="input validation") as raised:
        service.create_and_run(_request(tmp_path, source))

    manifest = JobStore().load(raised.value.job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.FAILED
    assert collaborators.worker_calls == 0


def test_resume_resets_invalid_work_and_following_steps_but_reuses_valid_probe(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    service = _service(tmp_path, collaborators)
    job_dir = service.create_and_run(_request(tmp_path, source))
    (job_dir / "work" / "inference.wav").write_bytes(b"not a wave")

    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))
    service.resume(job_dir)

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.SUCCEEDED
    assert collaborators.probe_calls == 1
    assert collaborators.extract_calls == 2
    assert collaborators.worker_calls == 2
    assert collaborators.artifact_calls == 2


def test_resume_rejects_changed_source_and_existing_running_job(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    service = _service(tmp_path, collaborators)
    job_dir = service.create_and_run(_request(tmp_path, source))
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))
    source.write_bytes(b"changed")

    with pytest.raises(TranscriptionFailed, match="input validation"):
        service.resume(job_dir)

    source.write_bytes(b"source")
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.RUNNING))
    with pytest.raises(RuntimeError, match="running"):
        service.resume(job_dir)


def test_missing_model_without_ephemeral_permission_never_starts_worker(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    collaborators.model_complete = False
    service = _service(tmp_path, collaborators)

    with pytest.raises(TranscriptionFailed, match="model unavailable") as raised:
        service.create_and_run(_request(tmp_path, source), allow_model_download=False)

    manifest = JobStore().load(raised.value.job_dir, recover_interrupted=False)
    assert manifest.last_error == "model unavailable"
    assert collaborators.worker_calls == 0
    assert "allow_model_download" not in manifest.settings


def test_cancellation_and_provider_error_become_stable_content_free_job_errors(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    service = _service(tmp_path, collaborators)
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(TranscriptionInterrupted) as interrupted:
        service.create_and_run(_request(tmp_path, source), cancel_event=cancelled)
    cancelled_manifest = JobStore().load(interrupted.value.job_dir, recover_interrupted=False)
    assert cancelled_manifest.status is JobStatus.INTERRUPTED

    collaborators = Collaborators()
    collaborators.worker_error = RuntimeError("recognized text must never be persisted")
    with pytest.raises(TranscriptionFailed, match="worker") as failed:
        _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source))
    failed_manifest = JobStore().load(failed.value.job_dir, recover_interrupted=False)
    run_log = (failed.value.job_dir / "logs" / "run.log").read_text(encoding="utf-8")
    assert failed_manifest.status is JobStatus.FAILED
    assert failed_manifest.last_error == "worker"
    assert "recognized text" not in run_log
    assert "recognized text" not in (failed_manifest.last_error or "")


def test_preview_is_an_optional_final_step_and_artifact(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()

    job_dir = _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source, preview=True))

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert list(manifest.steps)[-1] == "render-preview"
    assert {item.kind for item in manifest.artifacts} == {
        "transcript-txt", "subtitles-srt", "subtitles-vtt", "preview-mp4"
    }
    assert collaborators.preview_calls == 1


def test_step_transitions_record_timestamps_and_failure_exit_metadata(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    job_dir = _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source))

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert all(step.started_at and step.finished_at for step in manifest.steps.values())
    assert all(step.exit_code == 0 for step in manifest.steps.values())

    collaborators = Collaborators()
    collaborators.worker_error = RuntimeError("untrusted provider output")
    with pytest.raises(TranscriptionFailed) as failed:
        _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source))
    failed_manifest = JobStore().load(failed.value.job_dir, recover_interrupted=False)
    failed_step = failed_manifest.steps["transcribe"]
    assert failed_step.started_at and failed_step.finished_at
    assert failed_step.exit_code is None
    assert failed_step.error == "worker"


def test_interrupted_job_resumes_with_current_download_permission_only(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    collaborators.model_complete = False
    service = _service(tmp_path, collaborators)
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(TranscriptionInterrupted) as interrupted:
        service.create_and_run(_request(tmp_path, source), cancel_event=cancelled)

    cancelled.clear()
    job_dir = service.resume(interrupted.value.job_dir, allow_model_download=True)
    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.SUCCEEDED
    assert collaborators.worker_calls == 1
    assert "allow_model_download" not in manifest.settings


def test_resume_rejects_non_transcription_mode_and_terminal_success(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    service = _service(tmp_path, Collaborators())
    job_dir = service.create_and_run(_request(tmp_path, source))

    with pytest.raises(RuntimeError, match="current status"):
        service.resume(job_dir)

    JobStore().update(job_dir, lambda manifest: setattr(manifest, "mode", JobMode.NARRATE))
    with pytest.raises(RuntimeError, match="not a transcription"):
        service.resume(job_dir)
