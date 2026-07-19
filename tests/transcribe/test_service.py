from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import threading
import wave

import pytest

from minoru_studio.jobs.model import JobMode, JobStatus
from minoru_studio.jobs.store import JobStore
from minoru_studio.processes import ProcessResult
from minoru_studio.transcribe.contracts import (
    SegmentResult,
    WorkerResult,
    WordResult,
    load_worker_request,
    save_worker_result,
)
from minoru_studio.transcribe.media import FontChoice, MediaInfo, MediaToolVersions
from minoru_studio.transcribe.service import (
    TranscribeRequest,
    TranscribeService,
    TranscriptionFailed,
    TranscriptionInterrupted,
)


def _write_pcm(path: Path, *, frames: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * frames)


class Collaborators:
    def __init__(self, *, has_audio: bool = True, has_video: bool = True) -> None:
        self.has_audio = has_audio
        self.has_video = has_video
        self.media_duration_ms = 1_000
        self.wav_frames = 16_000
        self.probe_calls = 0
        self.extract_calls = 0
        self.worker_calls = 0
        self.artifact_calls = 0
        self.preview_calls = 0
        self.model_complete = True
        self.worker_error: Exception | None = None
        self.worker_result: WorkerResult | None = None
        self.font: FontChoice | None = None
        self.font_error: Exception | None = None
        self.preview_fonts: list[FontChoice] = []
        self.font_resolve_calls = 0

    def probe(self, source: Path) -> MediaInfo:
        self.probe_calls += 1
        return MediaInfo(self.media_duration_ms, self.has_audio, self.has_video)

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
        _write_pcm(destination, frames=self.wav_frames)
        return destination

    def worker(self, args: list[str], *, cancel_event: object | None) -> ProcessResult:
        self.worker_calls += 1
        if self.worker_error is not None:
            raise self.worker_error
        request = load_worker_request(args[-1])
        save_worker_result(
            request.output_json,
            self.worker_result or WorkerResult(
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
        font: FontChoice,
        cancel_event: object | None,
    ) -> Path:
        self.preview_calls += 1
        self.preview_fonts.append(font)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"preview")
        return destination

    def resolve_font(self) -> FontChoice:
        self.font_resolve_calls += 1
        if self.font_error is not None:
            raise self.font_error
        if self.font is None:
            raise FileNotFoundError("no test Japanese font")
        return self.font


def _service(
    tmp_path: Path,
    collaborators: Collaborators,
    *,
    logger_factory=None,
) -> TranscribeService:
    return TranscribeService(
        store=JobStore(),
        probe=collaborators.probe,
        extract=collaborators.extract,
        worker=collaborators.worker,
        artifacts=collaborators.artifacts,
        preview_renderer=collaborators.preview,
        font_resolver=collaborators.resolve_font,
        tool_versions=lambda: MediaToolVersions("ffmpeg 7.1", "ffprobe 7.1"),
        model_cache_dir=tmp_path / "models",
        model_complete=lambda cache, model: collaborators.model_complete,
        require_capacity=lambda cache, model: 10_000_000_000,
        **({"logger_factory": logger_factory} if logger_factory is not None else {}),
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


def test_audio_only_preview_fails_during_probe_before_downstream_work(tmp_path: Path):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"source")
    collaborators = Collaborators(has_video=False)
    service = _service(tmp_path, collaborators)

    with pytest.raises(TranscriptionFailed, match="input validation") as raised:
        service.create_and_run(_request(tmp_path, source, preview=True))

    manifest = JobStore().load(raised.value.job_dir, recover_interrupted=False)
    assert raised.value.category == "input validation"
    assert manifest.status is JobStatus.FAILED
    assert manifest.steps["probe-input"].status.value == "failed"
    assert not any(
        step.status.value == "succeeded"
        for name, step in manifest.steps.items()
        if name != "probe-input"
    )
    assert collaborators.probe_calls == 1
    assert collaborators.extract_calls == 0
    assert collaborators.worker_calls == 0
    assert collaborators.artifact_calls == 0
    assert collaborators.preview_calls == 0


def test_audio_only_transcription_runs_without_preview(tmp_path: Path):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"source")
    collaborators = Collaborators(has_video=False)

    job_dir = _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source))

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.SUCCEEDED
    assert collaborators.probe_calls == 1
    assert collaborators.extract_calls == 1
    assert collaborators.worker_calls == 1
    assert collaborators.artifact_calls == 1
    assert collaborators.preview_calls == 0


def test_resume_revalidates_cached_audio_only_probe_for_preview(tmp_path: Path):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"source")
    collaborators = Collaborators(has_video=False)
    service = _service(tmp_path, collaborators)
    job_dir = service.create_and_run(_request(tmp_path, source))

    def make_pre_fix_preview_job(manifest):
        manifest.settings["preview"] = True
        manifest.status = JobStatus.FAILED

    JobStore().update(job_dir, make_pre_fix_preview_job)
    collaborators.probe_calls = 0
    collaborators.extract_calls = 0
    collaborators.worker_calls = 0
    collaborators.artifact_calls = 0
    collaborators.preview_calls = 0

    with pytest.raises(TranscriptionFailed, match="input validation") as raised:
        service.resume(job_dir)

    manifest = JobStore().load(raised.value.job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.FAILED
    assert manifest.steps["probe-input"].status.value == "failed"
    assert manifest.artifacts == []
    assert not any(
        step.status.value == "succeeded"
        for name, step in manifest.steps.items()
        if name != "probe-input"
    )
    assert collaborators.probe_calls == 1
    assert collaborators.extract_calls == 0
    assert collaborators.worker_calls == 0
    assert collaborators.artifact_calls == 0
    assert collaborators.preview_calls == 0


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
    font_file = tmp_path / "Fonts" / "YuGothR.ttc"
    font_file.parent.mkdir()
    font_file.touch()
    collaborators.font = FontChoice("Yu Gothic", font_file)

    job_dir = _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source, preview=True))

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert list(manifest.steps)[-1] == "render-preview"
    assert {item.kind for item in manifest.artifacts} == {
        "transcript-txt", "subtitles-srt", "subtitles-vtt", "preview-mp4"
    }
    assert collaborators.preview_calls == 1
    assert collaborators.preview_fonts == [FontChoice("Yu Gothic", font_file)]
    assert manifest.tools["preview-font"] == "Yu Gothic"


def test_preview_missing_font_fails_during_probe_before_downstream_work(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    service = _service(tmp_path, collaborators)

    with pytest.raises(TranscriptionFailed, match="input validation") as raised:
        service.create_and_run(_request(tmp_path, source, preview=True))

    manifest = JobStore().load(raised.value.job_dir, recover_interrupted=False)
    assert manifest.steps["probe-input"].status.value == "failed"
    assert collaborators.probe_calls == 1
    assert collaborators.extract_calls == 0
    assert collaborators.worker_calls == 0
    assert collaborators.artifact_calls == 0
    assert collaborators.preview_calls == 0


def test_worker_duration_beyond_inference_wav_is_input_validation_before_artifacts(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    collaborators.media_duration_ms = 30_000
    collaborators.wav_frames = 320_250
    collaborators.worker_result = WorkerResult(
        schema_version=1,
        model="small",
        provider_version="1.2.1",
        language="ja",
        language_probability=1.0,
        duration_ms=20_017,
        duration_after_vad_ms=20_017,
        no_speech=True,
        segments=(),
    )

    with pytest.raises(TranscriptionFailed, match="input validation") as raised:
        _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source))

    manifest = JobStore().load(raised.value.job_dir, recover_interrupted=False)
    assert raised.value.category == "input validation"
    assert manifest.last_error == "input validation"
    assert collaborators.artifact_calls == 0
    assert collaborators.preview_calls == 0


def test_decoded_wav_padding_allows_longer_worker_duration_when_cues_fit_source(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    collaborators.media_duration_ms = 20_000
    collaborators.wav_frames = 320_250
    collaborators.worker_result = WorkerResult(
        schema_version=1,
        model="small",
        provider_version="1.2.1",
        language="ja",
        language_probability=1.0,
        duration_ms=20_016,
        duration_after_vad_ms=6_340,
        no_speech=False,
        segments=(SegmentResult(100, 6_340, "確認", (WordResult(100, 6_340, "確認"),)),),
    )

    job_dir = _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source))

    assert JobStore().load(job_dir, recover_interrupted=False).status is JobStatus.SUCCEEDED
    assert collaborators.artifact_calls == 1


def test_cue_endpoint_beyond_source_is_rejected_even_when_within_inference_wav(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    collaborators.media_duration_ms = 20_000
    collaborators.wav_frames = 320_250
    collaborators.worker_result = WorkerResult(
        schema_version=1,
        model="small",
        provider_version="1.2.1",
        language="ja",
        language_probability=1.0,
        duration_ms=20_016,
        duration_after_vad_ms=20_001,
        no_speech=False,
        segments=(SegmentResult(19_900, 20_001, "確認", (WordResult(19_900, 20_001, "確認"),)),),
    )

    with pytest.raises(TranscriptionFailed, match="input validation"):
        _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source))

    assert collaborators.artifact_calls == 0


@pytest.mark.parametrize(
    ("media_duration_ms", "malicious"),
    [
        (
            30_000,
            WorkerResult(
                schema_version=1,
                model="small",
                provider_version="1.2.1",
                language="ja",
                language_probability=1.0,
                duration_ms=20_017,
                duration_after_vad_ms=20_017,
                no_speech=True,
                segments=(),
            ),
        ),
        (
            20_000,
            WorkerResult(
                schema_version=1,
                model="small",
                provider_version="1.2.1",
                language="ja",
                language_probability=1.0,
                duration_ms=20_016,
                duration_after_vad_ms=20_001,
                no_speech=False,
                segments=(SegmentResult(19_900, 20_001, "確認", ()),),
            ),
        ),
    ],
)
def test_resume_resets_cached_worker_duration_or_cue_violation_and_downstream_artifacts(
    tmp_path: Path,
    media_duration_ms: int,
    malicious: WorkerResult,
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    collaborators.media_duration_ms = media_duration_ms
    collaborators.wav_frames = 320_250
    font_file = tmp_path / "Fonts" / "YuGothR.ttc"
    font_file.parent.mkdir()
    font_file.touch()
    collaborators.font = FontChoice("Yu Gothic", font_file)
    service = _service(tmp_path, collaborators)
    job_dir = service.create_and_run(_request(tmp_path, source, preview=True))
    save_worker_result(job_dir / "work" / "raw-segments.json", malicious)
    collaborators.worker_result = malicious
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))

    with pytest.raises(TranscriptionFailed, match="input validation"):
        service.resume(job_dir)

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.artifacts == []
    assert manifest.steps["probe-input"].status.value == "succeeded"
    assert manifest.steps["extract-audio"].status.value == "succeeded"
    assert manifest.steps["transcribe"].status.value == "failed"
    assert collaborators.artifact_calls == 1
    assert collaborators.preview_calls == 1


def test_resume_repreflights_missing_cached_preview_font_before_rerendering(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    first_font = tmp_path / "Fonts" / "YuGothR.ttc"
    first_font.parent.mkdir()
    first_font.touch()
    collaborators.font = FontChoice("Yu Gothic", first_font)
    service = _service(tmp_path, collaborators)
    job_dir = service.create_and_run(_request(tmp_path, source, preview=True))
    first_font.unlink()
    second_font = tmp_path / "Fonts" / "meiryo.ttc"
    second_font.touch()
    collaborators.font = FontChoice("Meiryo", second_font)
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))

    service.resume(job_dir)

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.tools["preview-font"] == "Meiryo"
    assert collaborators.probe_calls == 2
    assert collaborators.extract_calls == 2
    assert collaborators.worker_calls == 2
    assert collaborators.artifact_calls == 2
    assert collaborators.preview_calls == 2
    assert collaborators.preview_fonts[-1] == FontChoice("Meiryo", second_font)


def test_resume_rejects_relative_cached_preview_font_path_and_reprobes(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    font_file = tmp_path / "Fonts" / "YuGothR.ttc"
    font_file.parent.mkdir()
    font_file.touch()
    collaborators.font = FontChoice("Yu Gothic", font_file)
    service = _service(tmp_path, collaborators)
    job_dir = service.create_and_run(_request(tmp_path, source, preview=True))
    state_path = job_dir / "work" / "preview-font.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["file"] = font_file.relative_to(tmp_path).as_posix()
    state_path.write_text(json.dumps(state), encoding="utf-8")
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))

    service.resume(job_dir)

    assert collaborators.probe_calls == 2
    assert collaborators.font_resolve_calls == 2
    assert collaborators.extract_calls == 2
    assert collaborators.worker_calls == 2
    assert collaborators.artifact_calls == 2
    assert collaborators.preview_calls == 2


def test_replaced_cached_preview_font_reprobes_and_clears_stale_provenance_on_failure(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    font_file = tmp_path / "Fonts" / "YuGothR.ttc"
    font_file.parent.mkdir()
    font_file.write_bytes(b"original font")
    collaborators.font = FontChoice("Yu Gothic", font_file)
    service = _service(tmp_path, collaborators)
    job_dir = service.create_and_run(_request(tmp_path, source, preview=True))
    font_file.write_bytes(b"replacement font with changed content")
    collaborators.font_error = FileNotFoundError("no Japanese font")
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))

    with pytest.raises(TranscriptionFailed, match="input validation"):
        service.resume(job_dir)

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert collaborators.probe_calls == 2
    assert collaborators.font_resolve_calls == 2
    assert collaborators.extract_calls == 1
    assert collaborators.worker_calls == 1
    assert collaborators.artifact_calls == 1
    assert collaborators.preview_calls == 1
    assert manifest.artifacts == []
    assert "preview-font" not in manifest.tools


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


def test_logger_setup_failure_marks_job_failed_and_allows_a_later_resume(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    setup_failure = [True]

    def logger_factory(job_dir: Path):
        if setup_failure[0]:
            raise OSError("recognized provider text must not be persisted")
        return logging.getLogger("test.transcribe.service")

    service = _service(tmp_path, collaborators, logger_factory=logger_factory)
    with pytest.raises(TranscriptionFailed, match="job logging") as failed:
        service.create_and_run(_request(tmp_path, source))

    manifest = JobStore().load(failed.value.job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.FAILED
    assert manifest.last_error == "job logging"
    assert "recognized provider text" not in str(failed.value)
    assert "recognized provider text" not in (manifest.last_error or "")

    setup_failure[0] = False
    assert service.resume(failed.value.job_dir) == failed.value.job_dir
    assert JobStore().load(failed.value.job_dir, recover_interrupted=False).status is JobStatus.SUCCEEDED
