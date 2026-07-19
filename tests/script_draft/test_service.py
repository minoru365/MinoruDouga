import json
import struct
import threading
from pathlib import Path

import pytest

from minoru_studio.jobs.model import JobStatus, StepStatus
from minoru_studio.jobs.store import JobStore
from minoru_studio.script_draft.models import FrameCandidate, ScriptDraftRequest, VideoInfo
from minoru_studio.script_draft.service import (
    ScriptDraftFailed,
    ScriptDraftInterrupted,
    ScriptDraftService,
)


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 1280, 720)
        + b"\x08\x02\x00\x00\x00"
    )


class Collaborators:
    def __init__(self) -> None:
        self.probe_calls = 0
        self.scene_calls = 0
        self.interval_calls = 0
        self.render_calls = 0
        self.fail_scene = False

    def probe(self, source: Path) -> VideoInfo:
        self.probe_calls += 1
        return VideoInfo(12_000, 1920, 1080)

    def scene(self, source: Path, work: Path, *, cancel_event: object | None = None):
        self.scene_calls += 1
        if self.fail_scene:
            raise ValueError("untrusted ffmpeg stderr")
        image = work / "scene-frames" / "frame-000001.png"
        _png(image)
        return [FrameCandidate(1_000, image, "scene")]

    def interval(self, source: Path, work: Path, *, cancel_event: object | None = None):
        self.interval_calls += 1
        image = work / "interval-frames" / "frame-000001.png"
        _png(image)
        return [FrameCandidate(1_050, image, "interval")]

    def render(self, outputs: Path, info: VideoInfo, merged):
        self.render_calls += 1
        frames = outputs / "frames"
        _png(frames / "frame-0001.png")
        (outputs / "frame-index.json").write_text(json.dumps({
            "schema_version": 1, "duration_ms": 12_000, "width": 1920,
            "height": 1080,
            "settings": {"scene_threshold": 0.30, "interval_ms": 5_000,
                         "merge_tolerance_ms": 100, "max_frame_edge": 1_280,
                         "frame_format": "png"},
            "entries": [{"index": 1, "time_ms": 1_000,
                         "image_path": "frames/frame-0001.png",
                         "reasons": ["scene", "interval"]}],
        }), encoding="utf-8")
        (outputs / "script.md").write_text("frames/frame-0001.png", encoding="utf-8")
        return []


def _service(tmp_path: Path, collaborators: Collaborators) -> ScriptDraftService:
    return ScriptDraftService(
        store=JobStore(), probe=collaborators.probe, extract_scene=collaborators.scene,
        extract_interval=collaborators.interval, render=collaborators.render,
        tool_versions=lambda: type("Tools", (), {"ffmpeg": "ffmpeg test", "ffprobe": "ffprobe test"})(),
    )


def _request(tmp_path: Path, source: Path) -> ScriptDraftRequest:
    return ScriptDraftRequest(source, "draft", tmp_path / "jobs")


def test_create_runs_durable_four_step_job_with_artifact_fingerprints(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()

    job_dir = _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source))

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.SUCCEEDED
    assert list(manifest.steps) == ["probe-input", "extract-scene-frames", "extract-interval-frames", "render-draft"]
    assert all(step.status is StepStatus.SUCCEEDED for step in manifest.steps.values())
    assert {item.path for item in manifest.artifacts} == {
        "outputs/frames/frame-0001.png", "outputs/frame-index.json", "outputs/script.md",
    }
    assert manifest.tools["ffmpeg"] == "ffmpeg test"
    assert manifest.tools["ffprobe"] == "ffprobe test"


def test_resume_reuses_valid_steps_and_reruns_first_invalid_step_and_later_steps(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    service = _service(tmp_path, collaborators)
    job_dir = service.create_and_run(_request(tmp_path, source))
    (job_dir / "work" / "interval-frames" / "frame-000001.png").unlink()
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))

    service.resume(job_dir)

    assert collaborators.probe_calls == 1
    assert collaborators.scene_calls == 1
    assert collaborators.interval_calls == 2
    assert collaborators.render_calls == 2


def test_changed_source_is_rejected_before_process_calls_and_error_is_content_free(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    service = _service(tmp_path, collaborators)
    job_dir = service.create_and_run(_request(tmp_path, source))
    JobStore().update(job_dir, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))
    source.write_bytes(b"changed")
    before = (collaborators.probe_calls, collaborators.scene_calls, collaborators.interval_calls)

    with pytest.raises(ScriptDraftFailed, match="^input validation$") as raised:
        service.resume(job_dir)

    assert str(raised.value) == "input validation"
    assert before == (collaborators.probe_calls, collaborators.scene_calls, collaborators.interval_calls)


def test_ffmpeg_failure_is_durable_and_content_free(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    collaborators = Collaborators()
    collaborators.fail_scene = True

    with pytest.raises(ScriptDraftFailed, match="^FFmpeg$") as raised:
        _service(tmp_path, collaborators).create_and_run(_request(tmp_path, source))

    manifest = JobStore().load(raised.value.job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.FAILED
    assert manifest.steps["extract-scene-frames"].error == "FFmpeg"
    assert "untrusted" not in str(raised.value)


def test_cancelled_step_is_marked_interrupted(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(ScriptDraftInterrupted) as raised:
        _service(tmp_path, Collaborators()).create_and_run(_request(tmp_path, source), cancel_event=cancelled)

    manifest = JobStore().load(raised.value.job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.INTERRUPTED
    assert manifest.steps["probe-input"].status is StepStatus.INTERRUPTED
