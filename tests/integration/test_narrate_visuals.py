from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from minoru_studio.jobs.model import JobStatus
from minoru_studio.jobs.store import JobStore
from minoru_studio.narrate.models import ImageInfo, NarrateRequest, StoryboardRequest, Utterance, VideoInfo, VoicevoxProvenance, WavInfo
from minoru_studio.narrate.service import NarrateFailed, NarrateService


class VisualFakes:
    def __init__(self) -> None:
        self.synthesized: list[str] = []
        self.plans: list[list[dict[str, object]]] = []

    def image_probe(self, path: Path) -> ImageInfo:
        return ImageInfo(640, 480)

    def video_probe(self, path: Path) -> VideoInfo:
        return VideoInfo(1_000, 640, 480)

    def parse(self, path: Path) -> tuple[Utterance, ...]:
        return (Utterance(1, path.read_text(encoding="utf-8")),)

    def preflight(self, *, cancel_event=None) -> VoicevoxProvenance:
        return VoicevoxProvenance("test", "ずんだもん", "ノーマル", 1)

    def synthesize(self, text: str, speaker_id: int, *, cancel_event=None) -> bytes:
        self.synthesized.append(text); return b"wav"

    def publish(self, data: bytes, path: Path) -> WavInfo:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return WavInfo(Decimal("0.5"), 24000, 1, 2)

    def inspect(self, path: Path) -> WavInfo:
        if not path.exists(): raise ValueError("missing")
        duration = Decimal("0.5")
        if path.name == "narration.wav":
            count = len(list((path.parent / "utterances").glob("*.wav")))
            duration = Decimal("0.5") * count + Decimal("0.3") * (count - 1)
        return WavInfo(duration, 24000, 1, 2)

    def concat(self, wavs, destination: Path, *, silence_ms: int, cancel_event=None) -> WavInfo:
        destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(b"narration")
        return WavInfo(Decimal("1.3"), 24000, 1, 2)

    def subtitles(self, output: Path, cues):
        (output / "subtitles.srt").write_text("srt", encoding="utf-8")
        (output / "subtitles.vtt").write_text("vtt", encoding="utf-8")
        return output / "subtitles.srt", output / "subtitles.vtt"

    def preview(self, clips, narration, subtitles, target, narration_ms, *, font=None, cancel_event=None):
        self.plans.append(clips); target.write_bytes(b"preview"); return target

    def valid(self, job: Path, *, include_preview: bool, expected_utterances: int | None = None) -> bool:
        outputs = job / "outputs"; wavs = list((outputs / "utterances").glob("*.wav"))
        names = {"narration.wav", "subtitles.srt", "subtitles.vtt"} | ({"preview.mp4"} if include_preview else set())
        manifest = JobStore().load(job, recover_interrupted=False)
        expected_paths = {"outputs/narration.wav", "outputs/subtitles.srt", "outputs/subtitles.vtt"} | ({"outputs/preview.mp4"} if include_preview else set()) | {f"outputs/utterances/utterance-{index:04d}.wav" for index in range(1, len(wavs) + 1)}
        return {path.name for path in outputs.iterdir() if path.is_file()} == names and {item.path for item in manifest.artifacts} == expected_paths and (expected_utterances is None or len(wavs) == expected_utterances)


def _service(fake: VisualFakes) -> NarrateService:
    return NarrateService(
        store=JobStore(), probe=fake.video_probe, image_probe=fake.image_probe, parse=fake.parse,
        voicevox=fake, publish_wav=fake.publish, inspect_wav=fake.inspect, concat=fake.concat,
        subtitles=fake.subtitles, visual_preview_renderer=fake.preview, artifacts_validator=fake.valid,
        tool_versions=lambda: type("Tools", (), {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})(),
    )


def test_still_and_mixed_storyboard_use_cue_plan_and_reused_source_mapping(tmp_path: Path):
    image = tmp_path / "still.png"; image.write_bytes(b"image")
    video = tmp_path / "motion.mp4"; video.write_bytes(b"video")
    story = tmp_path / "story.json"
    story.write_text(json.dumps({"version": 1, "clips": [
        {"id": "first", "kind": "image", "source": "still.png", "narration": "一。二。"},
        {"id": "again", "kind": "image", "source": "still.png", "narration": "三。"},
        {"id": "later", "kind": "video", "source": "motion.mp4", "narration": "四。", "trim_start_ms": 0, "trim_end_ms": 500},
    ]}), encoding="utf-8")
    fake = VisualFakes()
    job = _service(fake).create_and_run(StoryboardRequest(story, "story", tmp_path / "jobs", preview=True))

    plan = json.loads((job / "work" / "visual-plan.json").read_text(encoding="utf-8"))["clips"]
    assert [(item["id"], item["utterance_start"], item["utterance_end"]) for item in plan] == [("first", 1, 2), ("again", 3, 3), ("later", 4, 4)]
    assert plan[0]["end_ms"] == plan[1]["start_ms"] and plan[1]["end_ms"] == plan[2]["start_ms"]
    assert [item["kind"] for item in fake.plans[0]] == ["image", "image", "video"]
    assert len(list((job / "outputs" / "utterances").glob("*.wav"))) == 4


def test_changed_storyboard_source_is_refused_before_resume_synthesis(tmp_path: Path):
    image = tmp_path / "still.png"; image.write_bytes(b"image")
    story = tmp_path / "story.json"; story.write_text(json.dumps({"version": 1, "clips": [{"id": "one", "kind": "image", "source": "still.png", "narration": "一。"}]}), encoding="utf-8")
    fake = VisualFakes(); service = _service(fake)
    job = service.create_and_run(StoryboardRequest(story, "story", tmp_path / "jobs"))
    JobStore().update(job, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))
    image.write_bytes(b"changed")

    with pytest.raises(NarrateFailed, match="input validation"):
        service.resume(job)
    assert fake.synthesized == ["一。"]
