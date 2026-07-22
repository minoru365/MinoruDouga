import json
import logging
import os
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from minoru_studio.jobs.model import JobStatus, StepStatus
from minoru_studio.jobs.store import JobStore
from minoru_studio.narrate.models import NarrateRequest, Utterance, VideoInfo, VoicevoxProvenance, WavInfo
from minoru_studio.processes import ProcessCancelledError
from minoru_studio.transcribe.media import FontChoice


class Fakes:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.engine = "test-engine"
        self.narration_seconds = Decimal("2.3")
        self.cancel_concat = False
        self.private = "private utterance"
        self.font: Path | None = None
        self.synthesis_error: Exception | None = None

    def probe(self, _: Path) -> VideoInfo:
        self.calls.append("probe")
        return VideoInfo(5_000, 1920, 1080)

    def parse(self, _: Path) -> tuple[Utterance, ...]:
        self.calls.append("parse")
        return (Utterance(1, self.private), Utterance(2, "another private utterance"))

    def preflight(self, *, cancel_event=None) -> VoicevoxProvenance:
        self.calls.append("preflight")
        return VoicevoxProvenance(self.engine, "ずんだもん", "ノーマル", 3)

    def synthesize(self, text: str, speaker_id: int, *, cancel_event=None) -> bytes:
        self.calls.append("synthesize")
        assert text in {self.private, "another private utterance"}
        if self.synthesis_error is not None:
            raise self.synthesis_error
        return b"fake wav"

    def publish(self, data: bytes, path: Path) -> WavInfo:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ValueError("create-only staged WAV")
        path.write_bytes(data)
        return WavInfo(Decimal("1"), 24000, 1, 2)

    def inspect(self, path: Path) -> WavInfo:
        duration = self.narration_seconds if path.name == "narration.wav" else (self.narration_seconds - Decimal("0.3")) / 2
        if not path.is_file():
            raise ValueError("missing fake wav")
        return WavInfo(duration, 24000, 1, 2)

    def concat(self, wavs, destination: Path, *, silence_ms: int, cancel_event=None) -> WavInfo:
        self.calls.append("concat")
        if self.cancel_concat:
            raise ProcessCancelledError("private FFmpeg command")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"narration")
        return WavInfo(self.narration_seconds, 24000, 1, 2)

    def subtitles(self, output: Path, cues):
        self.calls.append("subtitles")
        (output / "subtitles.srt").write_text("srt", encoding="utf-8")
        (output / "subtitles.vtt").write_text("vtt", encoding="utf-8")
        return output / "subtitles.srt", output / "subtitles.vtt"

    def preview(self, source, narration, subtitles, target, video, narration_ms, *, font=None, cancel_event=None):
        self.calls.append("preview")
        target.write_bytes(b"preview")
        return target

    def valid(self, job_dir: Path, *, include_preview: bool) -> bool:
        names = {"narration.wav", "subtitles.srt", "subtitles.vtt"}
        if include_preview:
            names.add("preview.mp4")
        return all((job_dir / "outputs" / name).is_file() for name in names) and (job_dir / "outputs" / "narration.wav").read_bytes() == b"narration"


def _request(tmp_path: Path, *, preview: bool = False) -> NarrateRequest:
    tmp_path.mkdir(parents=True, exist_ok=True)
    video = tmp_path / "source.mp4"; video.write_bytes(b"video")
    script = tmp_path / "script.txt"; script.write_text("private utterance", encoding="utf-8")
    return NarrateRequest(video, script, "narration", tmp_path / "jobs", preview)


def _service(fake: Fakes, *, logger_factory=None):
    from minoru_studio.narrate.service import NarrateService
    return NarrateService(
        store=JobStore(), probe=fake.probe, parse=fake.parse, voicevox=fake,
        publish_wav=fake.publish, inspect_wav=fake.inspect, concat=fake.concat,
        subtitles=fake.subtitles, preview_renderer=fake.preview, artifacts_validator=fake.valid,
        tool_versions=lambda: type("Tools", (), {"ffmpeg": "ffmpeg test", "ffprobe": "ffprobe test"})(),
        font_resolver=lambda: FontChoice("Yu Gothic", fake.font),
        **({"logger_factory": logger_factory} if logger_factory else {}),
    )


@pytest.mark.parametrize("preview,expected_steps", [(False, 5), (True, 6)])
def test_create_persists_exact_durable_contract_and_preserves_sources(tmp_path: Path, preview: bool, expected_steps: int):
    request = _request(tmp_path, preview=preview); fake = Fakes()
    if preview:
        fake.font = tmp_path / "YuGothR.ttc"; fake.font.write_bytes(b"font")
    video_before = (request.input_path.read_bytes(), request.input_path.stat().st_mtime_ns)
    script_before = (request.script_path.read_bytes(), request.script_path.stat().st_mtime_ns)

    job_dir = _service(fake).create_and_run(request)

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.SUCCEEDED
    assert len(manifest.inputs) == 2
    assert manifest.settings == {"speaker_name": "ずんだもん", "style_name": "ノーマル", "speed_scale": 1.0, "silence_ms": 300, "max_utterance_codepoints": 60, "script_format": ".txt", "preview": preview}
    assert list(manifest.steps) == ["probe-input", "parse-script", "synthesize-utterances", "concat-audio", "render-artifacts", *(["render-preview"] if preview else [])]
    assert len(manifest.steps) == expected_steps
    assert (job_dir / "inputs" / "script.txt").read_bytes() == script_before[0]
    assert json.loads((job_dir / "work" / "voicevox-provenance.json").read_text(encoding="utf-8"))["engine_version"] == "test-engine"
    assert manifest.tools["ffmpeg"] == "ffmpeg test" and manifest.tools["ffprobe"] == "ffprobe test"
    assert fake.calls.count("synthesize") == 2
    assert {item.path for item in manifest.artifacts} == {"outputs/utterances/utterance-0001.wav", "outputs/utterances/utterance-0002.wav", "outputs/narration.wav", "outputs/subtitles.srt", "outputs/subtitles.vtt", *({"outputs/preview.mp4"} if preview else set())}
    assert (request.input_path.read_bytes(), request.input_path.stat().st_mtime_ns) == video_before
    assert (request.script_path.read_bytes(), request.script_path.stat().st_mtime_ns) == script_before
    assert fake.private not in (job_dir / "logs" / "run.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("duration,has_warning", [(Decimal("2.3"), False), (Decimal("5.1"), True)])
def test_duration_warning_is_content_free_and_only_written_on_overrun(tmp_path: Path, duration: Decimal, has_warning: bool):
    fake = Fakes(); fake.narration_seconds = duration
    job_dir = _service(fake).create_and_run(_request(tmp_path))
    warning = job_dir / "work" / "duration-warning.json"
    assert warning.exists() is has_warning
    if has_warning:
        assert json.loads(warning.read_text(encoding="utf-8")) == {"source_duration_ms": 5000, "narration_duration_ms": 5100}
        assert fake.private not in warning.read_text(encoding="utf-8")


def test_cancellation_is_checked_before_each_synthesis_request(tmp_path: Path):
    request = _request(tmp_path); fake = Fakes(); event = threading.Event()
    original = fake.preflight
    def preflight(*, cancel_event=None):
        result = original(cancel_event=cancel_event); event.set(); return result
    fake.preflight = preflight  # type: ignore[method-assign]

    from minoru_studio.narrate.service import NarrateInterrupted
    with pytest.raises(NarrateInterrupted) as raised:
        _service(fake).create_and_run(request, cancel_event=event)
    manifest = JobStore().load(raised.value.job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.INTERRUPTED
    assert fake.calls.count("synthesize") == 0


def test_ffmpeg_cancellation_and_logger_failure_are_durable_and_content_free(tmp_path: Path):
    from minoru_studio.narrate.service import NarrateFailed, NarrateInterrupted
    fake = Fakes(); fake.cancel_concat = True
    with pytest.raises(NarrateInterrupted) as interrupted:
        _service(fake).create_and_run(_request(tmp_path))
    manifest = JobStore().load(interrupted.value.job_dir, recover_interrupted=False)
    assert manifest.steps["concat-audio"].status is StepStatus.INTERRUPTED

    failing = Fakes()
    def logger_failure(_: Path): raise OSError("private logger body")
    with pytest.raises(NarrateFailed, match="^job logging:") as failed:
        _service(failing, logger_factory=logger_failure).create_and_run(_request(tmp_path / "logging"))
    failed_manifest = JobStore().load(failed.value.job_dir, recover_interrupted=False)
    assert failed_manifest.status is JobStatus.FAILED and failed_manifest.last_error == "job logging"
    assert "private logger body" not in str(failed.value)


def test_voicevox_synthesis_failure_uses_a_stable_content_free_category(tmp_path: Path):
    from minoru_studio.narrate.service import NarrateFailed
    from minoru_studio.narrate.voicevox import VoicevoxSynthesisError
    fake = Fakes(); fake.synthesis_error = VoicevoxSynthesisError()
    with pytest.raises(NarrateFailed, match="^VOICEVOX synthesis:") as failed:
        _service(fake).create_and_run(_request(tmp_path))
    assert fake.private not in str(failed.value)
    assert JobStore().load(failed.value.job_dir, recover_interrupted=False).last_error == "VOICEVOX synthesis"


def test_voicevox_preflight_failure_uses_the_unavailable_category(tmp_path: Path):
    from minoru_studio.narrate.service import NarrateFailed
    from minoru_studio.narrate.voicevox import VoicevoxUnavailable
    fake = Fakes()
    def unavailable(*, cancel_event=None): raise VoicevoxUnavailable()
    fake.preflight = unavailable  # type: ignore[method-assign]
    with pytest.raises(NarrateFailed, match="^VOICEVOX unavailable:"):
        _service(fake).create_and_run(_request(tmp_path))


def test_terminal_success_is_not_resumable(tmp_path: Path):
    service = _service(Fakes()); job_dir = service.create_and_run(_request(tmp_path))
    with pytest.raises(RuntimeError, match="current status"):
        service.resume(job_dir)


@pytest.mark.parametrize("status,allowed", [(JobStatus.PENDING, True), (JobStatus.FAILED, True), (JobStatus.INTERRUPTED, True), (JobStatus.RUNNING, False)])
def test_resume_accepts_only_nonterminal_nonrunning_statuses(tmp_path: Path, status: JobStatus, allowed: bool):
    service = _service(Fakes()); job_dir = service.create_and_run(_request(tmp_path / status.value))
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", status))
    if allowed:
        assert service.resume(job_dir) == job_dir
    else:
        with pytest.raises(RuntimeError, match="already running"):
            service.resume(job_dir)


@pytest.mark.parametrize("changed", ["video", "script", "settings"])
def test_resume_rejects_changed_original_inputs_and_exact_settings(tmp_path: Path, changed: str):
    request = _request(tmp_path); fake = Fakes(); service = _service(fake); job_dir = service.create_and_run(request)
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))
    if changed == "video": request.input_path.write_bytes(b"changed video")
    elif changed == "script": request.script_path.write_text("changed script", encoding="utf-8")
    else: JobStore().update(job_dir, lambda manifest: manifest.settings.__setitem__("speed_scale", 1))
    from minoru_studio.narrate.service import NarrateFailed
    with pytest.raises(NarrateFailed, match="^input validation:"):
        service.resume(job_dir)


def test_resume_reuses_valid_preceding_steps_and_resets_from_first_invalid_work(tmp_path: Path):
    fake = Fakes(); service = _service(fake); job_dir = service.create_and_run(_request(tmp_path))
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))
    (job_dir / "work" / "utterances.json").write_text("{}", encoding="utf-8")
    before = list(fake.calls)

    service.resume(job_dir)

    rerun = fake.calls[len(before):]
    assert rerun[0] == "parse" and "probe" not in rerun
    assert rerun == ["parse", "preflight", "concat", "subtitles"]


def test_resume_reuses_only_matching_staged_wavs_and_engine_change_invalidates_them(tmp_path: Path):
    fake = Fakes(); service = _service(fake); job_dir = service.create_and_run(_request(tmp_path))
    JobStore().update(job_dir, lambda manifest: (setattr(manifest, "status", JobStatus.FAILED), setattr(manifest.steps["synthesize-utterances"], "status", StepStatus.INTERRUPTED), manifest.steps.pop("concat-audio"), manifest.steps.pop("render-artifacts")))
    for path in (job_dir / "outputs").rglob("*"):
        if path.is_file(): path.unlink()
    before = fake.calls.count("synthesize")
    service.resume(job_dir)
    assert fake.calls.count("synthesize") == before

    JobStore().update(job_dir, lambda manifest: (setattr(manifest, "status", JobStatus.FAILED), setattr(manifest.steps["synthesize-utterances"], "status", StepStatus.INTERRUPTED), manifest.steps.pop("concat-audio"), manifest.steps.pop("render-artifacts")))
    for path in (job_dir / "outputs").rglob("*"):
        if path.is_file(): path.unlink()
    fake.engine = "changed-engine"; before = fake.calls.count("synthesize")
    service.resume(job_dir)
    assert fake.calls.count("synthesize") == before + 2


@pytest.mark.parametrize("root_name", ["work", "outputs"])
def test_resume_rejects_root_escaping_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_name: str):
    from minoru_studio.narrate.service import NarrateFailed
    fake = Fakes(); service = _service(fake); job_dir = service.create_and_run(_request(tmp_path))
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))
    outside = tmp_path / "outside"; outside.mkdir(); original = type(job_dir).resolve
    def redirect(path: Path, *args, **kwargs):
        resolved = original(path, *args, **kwargs)
        return outside if resolved == job_dir / "work" else resolved
    monkeypatch.setattr(type(job_dir), "resolve", redirect)
    with pytest.raises(NarrateFailed, match="^input validation:"):
        service.resume(job_dir)

    monkeypatch.undo()


def test_tampered_successful_final_output_is_rejected_without_overwrite_or_delete(tmp_path: Path):
    from minoru_studio.narrate.service import NarrateFailed
    fake = Fakes(); service = _service(fake); job_dir = service.create_and_run(_request(tmp_path))
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))
    (job_dir / "outputs" / "narration.wav").write_bytes(b"tampered")
    with pytest.raises(NarrateFailed, match="^output validation:"):
        service.resume(job_dir)
    assert (job_dir / "outputs" / "narration.wav").read_bytes() == b"tampered"
