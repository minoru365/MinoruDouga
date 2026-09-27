from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from minoru_studio.jobs.model import JobStatus
from minoru_studio.jobs.store import JobStore
from minoru_studio.narrate.models import StoryboardRequest
from minoru_studio.narrate.service import NarrateFailed, NarrateService, StoryboardMusicRequiresPreview
from minoru_studio.narrate.visual_media import plan_music_runs, render_visual_preview
from minoru_studio.transcribe.media import MediaInfo

from test_narrate_visuals import VisualFakes


class MusicFakes(VisualFakes):
    def __init__(self) -> None:
        super().__init__()
        self.music: list[object] = []
        self.probed: list[str] = []

    def music_probe(self, path: Path) -> MediaInfo:
        self.probed.append(Path(path).name)
        return MediaInfo(duration_ms=60_000, has_audio=True, has_video=False)

    def preview(self, clips, narration, subtitles, target, narration_ms, *, font=None, cancel_event=None, music=None):
        self.music.append(music)
        return super().preview(clips, narration, subtitles, target, narration_ms, font=font, cancel_event=cancel_event)


def _service(fake: MusicFakes) -> NarrateService:
    return NarrateService(
        store=JobStore(), probe=fake.video_probe, image_probe=fake.image_probe, music_probe=fake.music_probe, parse=fake.parse,
        voicevox=fake, publish_wav=fake.publish, inspect_wav=fake.inspect, concat=fake.concat,
        subtitles=fake.subtitles, visual_preview_renderer=fake.preview, artifacts_validator=fake.valid,
        tool_versions=lambda: type("Tools", (), {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"})(),
    )


def _story(tmp_path: Path) -> Path:
    (tmp_path / "still.png").write_bytes(b"image")
    (tmp_path / "normal.mp3").write_bytes(b"normal")
    (tmp_path / "battle.mp3").write_bytes(b"battle")
    story = tmp_path / "story.json"
    story.write_text(json.dumps({
        "version": 1,
        "music": {"tracks": [{"id": "normal", "source": "normal.mp3"}, {"id": "battle", "source": "battle.mp3", "gain_db": -12}], "default": "normal", "crossfade_ms": 0},
        "clips": [
            {"id": "calm", "kind": "image", "source": "still.png", "narration": "一。"},
            {"id": "fight", "kind": "image", "source": "still.png", "narration": "二。", "music": "battle"},
            {"id": "home", "kind": "image", "source": "still.png", "narration": "三。"},
        ],
    }), encoding="utf-8")
    return story


def test_storyboard_music_is_fingerprinted_planned_and_passed_to_the_preview(tmp_path: Path):
    fake = MusicFakes()
    job = _service(fake).create_and_run(StoryboardRequest(_story(tmp_path), "story", tmp_path / "jobs", preview=True))

    manifest = JobStore().load(job, recover_interrupted=False)
    assert manifest.status is JobStatus.SUCCEEDED
    assert [Path(item.path).name for item in manifest.inputs] == ["story.json", "still.png", "normal.mp3", "battle.mp3"]
    assert fake.probed == ["normal.mp3", "battle.mp3"]
    plan = json.loads((job / "work" / "visual-plan.json").read_text(encoding="utf-8"))
    runs = plan["music"]["runs"]
    assert plan["music"]["crossfade_ms"] == 0
    assert [(run["track"], Path(run["source"]).name, run["gain_db"]) for run in runs] == [("normal", "normal.mp3", -18.0), ("battle", "battle.mp3", -12.0), ("normal", "normal.mp3", -18.0)]
    assert [(run["start_ms"], run["end_ms"]) for run in runs] == [(clip["start_ms"], clip["end_ms"]) for clip in plan["clips"]]
    assert fake.music == [runs]


def test_storyboard_music_requires_preview_before_a_job_is_created(tmp_path: Path):
    fake = MusicFakes()

    with pytest.raises(StoryboardMusicRequiresPreview):
        _service(fake).create_and_run(StoryboardRequest(_story(tmp_path), "story", tmp_path / "jobs"))
    assert not (tmp_path / "jobs").exists() and fake.synthesized == []


def test_changed_music_source_is_refused_on_resume(tmp_path: Path):
    story = _story(tmp_path); fake = MusicFakes(); service = _service(fake)
    job = service.create_and_run(StoryboardRequest(story, "story", tmp_path / "jobs", preview=True))
    JobStore().update(job, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))
    (tmp_path / "battle.mp3").write_bytes(b"changed")

    with pytest.raises(NarrateFailed, match="input validation"):
        service.resume(job)
    assert fake.synthesized == ["一。", "二。", "三。"]


def test_music_changed_during_preview_rendering_fails_success_validation(tmp_path: Path):
    story = _story(tmp_path); fake = MusicFakes()
    original = fake.preview

    def change_music_then_render(*args, **kwargs):
        (tmp_path / "normal.mp3").write_bytes(b"changed during render")
        return original(*args, **kwargs)

    fake.preview = change_music_then_render  # type: ignore[method-assign]

    with pytest.raises(NarrateFailed):
        _service(fake).create_and_run(StoryboardRequest(story, "story", tmp_path / "jobs", preview=True))


def test_storyboard_without_music_writes_no_music_plan(tmp_path: Path):
    (tmp_path / "still.png").write_bytes(b"image")
    story = tmp_path / "story.json"
    story.write_text(json.dumps({"version": 1, "clips": [{"id": "one", "kind": "image", "source": "still.png", "narration": "一。"}]}), encoding="utf-8")
    fake = MusicFakes()
    job = _service(fake).create_and_run(StoryboardRequest(story, "story", tmp_path / "jobs", preview=True))

    assert "music" not in json.loads((job / "work" / "visual-plan.json").read_text(encoding="utf-8"))
    assert [Path(item.path).name for item in JobStore().load(job, recover_interrupted=False).inputs] == ["story.json", "still.png"]
    assert fake.music == [None] and fake.probed == []


_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _ffmpeg(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _mean_volume(path: Path, start: float, duration: float) -> float:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-ss", f"{start}", "-t", f"{duration}", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    line = next(item for item in result.stderr.splitlines() if "mean_volume" in item)
    return float(line.split("mean_volume:")[1].split("dB")[0])


@pytest.mark.skipif(not _FFMPEG, reason="FFmpeg and FFprobe are required")
def test_ffmpeg_mixes_looping_tracks_under_silent_narration(tmp_path: Path):
    image = tmp_path / "frame.png"; narration = tmp_path / "narration.wav"; subtitles = tmp_path / "subtitles.srt"
    tone_a = tmp_path / "a.wav"; tone_b = tmp_path / "b.wav"
    _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=320x240", "-frames:v", "1", str(image))
    _ffmpeg("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "4", "-c:a", "pcm_s16le", str(narration))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "0.7", str(tone_a))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=660:sample_rate=44100", "-t", "0.7", str(tone_b))
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nテスト\n", encoding="utf-8")
    clips = [{"kind": "image", "source": str(image), "start_ms": 0, "end_ms": 2_000}, {"kind": "image", "source": str(image), "start_ms": 2_000, "end_ms": 4_000}]
    runs = [
        {**run, "source": str(tone_a if run["track"] == "a" else tone_b), "gain_db": -6.0}
        for run in plan_music_runs([("a", 0, 2_000), ("b", 2_000, 4_000)], crossfade_ms=500)
    ]

    output = render_visual_preview(clips, narration, subtitles, tmp_path / "out" / "preview.mp4", 4_000, music=runs)

    # Both tones are shorter than their runs, so audible sound late in each run proves looping.
    assert _mean_volume(output, 1.2, 0.4) > -40
    assert _mean_volume(output, 3.0, 0.4) > -40
    assert not list(tmp_path.glob("**/.preview.*.mp4"))
