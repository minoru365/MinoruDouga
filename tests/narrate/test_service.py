import json
from decimal import Decimal
from pathlib import Path

from minoru_studio.jobs.model import JobStatus
from minoru_studio.jobs.store import JobStore
from minoru_studio.narrate.models import NarrateRequest, Utterance, VideoInfo, VoicevoxProvenance, WavInfo


class Fakes:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def probe(self, path: Path) -> VideoInfo:
        self.calls.append("probe")
        return VideoInfo(5_000, 1920, 1080)

    def parse(self, path: Path) -> tuple[Utterance, ...]:
        self.calls.append("parse")
        return (Utterance(1, "private utterance"), Utterance(2, "another private utterance"))

    def preflight(self, *, cancel_event=None) -> VoicevoxProvenance:
        self.calls.append("preflight")
        return VoicevoxProvenance("test-engine", "ずんだもん", "ノーマル", 3)

    def synthesize(self, text: str, speaker_id: int, *, cancel_event=None) -> bytes:
        self.calls.append("synthesize")
        return b"fake wav"

    def publish(self, data: bytes, path: Path) -> WavInfo:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return WavInfo(Decimal("1"), 24000, 1, 2)

    def inspect(self, path: Path) -> WavInfo:
        duration = Decimal("2.3") if path.name == "narration.wav" else Decimal("1")
        return WavInfo(duration, 24000, 1, 2)

    def concat(self, wavs, destination: Path, *, silence_ms: int, cancel_event=None) -> WavInfo:
        self.calls.append("concat"); destination.write_bytes(b"narration")
        return WavInfo(Decimal("2.3"), 24000, 1, 2)

    def subtitles(self, output: Path, cues):
        self.calls.append("subtitles")
        (output / "subtitles.srt").write_text("srt", encoding="utf-8")
        (output / "subtitles.vtt").write_text("vtt", encoding="utf-8")
        return output / "subtitles.srt", output / "subtitles.vtt"

    def valid(self, job_dir: Path, *, include_preview: bool) -> bool:
        return True


def test_create_runs_durable_narration_lifecycle_with_exact_state(tmp_path: Path):
    from minoru_studio.narrate.service import NarrateService

    video = tmp_path / "source.mp4"; video.write_bytes(b"video")
    script = tmp_path / "script.txt"; script.write_text("private utterance", encoding="utf-8")
    fake = Fakes()
    service = NarrateService(
        store=JobStore(), probe=fake.probe, parse=fake.parse, voicevox=fake,
        publish_wav=fake.publish, inspect_wav=fake.inspect, concat=fake.concat,
        subtitles=fake.subtitles, artifacts_validator=fake.valid,
        tool_versions=lambda: type("Tools", (), {"ffmpeg": "ffmpeg test", "ffprobe": "ffprobe test"})(),
    )

    job_dir = service.create_and_run(NarrateRequest(video, script, "narration", tmp_path / "jobs"))

    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.SUCCEEDED
    assert list(manifest.steps) == ["probe-input", "parse-script", "synthesize-utterances", "concat-audio", "render-artifacts"]
    assert len(manifest.inputs) == 2
    assert manifest.settings == {"speaker_name": "ずんだもん", "style_name": "ノーマル", "speed_scale": 1.0, "silence_ms": 300, "max_utterance_codepoints": 60, "script_format": ".txt", "preview": False}
    assert (job_dir / "inputs" / "script.txt").read_text(encoding="utf-8") == "private utterance"
    assert json.loads((job_dir / "work" / "voicevox-provenance.json").read_text(encoding="utf-8"))["engine_version"] == "test-engine"
    assert [item.path for item in manifest.artifacts] == ["outputs/utterances/utterance-0001.wav", "outputs/utterances/utterance-0002.wav", "outputs/narration.wav", "outputs/subtitles.srt", "outputs/subtitles.vtt"]
    assert fake.calls == ["probe", "parse", "preflight", "synthesize", "synthesize", "concat", "subtitles"]
    assert "private utterance" not in (job_dir / "logs" / "run.log").read_text(encoding="utf-8")


def test_matching_staged_wavs_are_reused_without_overwriting_published_outputs(tmp_path: Path):
    from minoru_studio.narrate.service import NarrateService

    video = tmp_path / "source.mp4"; video.write_bytes(b"video")
    script = tmp_path / "script.txt"; script.write_text("private utterance", encoding="utf-8")
    fake = Fakes()
    service = NarrateService(store=JobStore(), probe=fake.probe, parse=fake.parse, voicevox=fake,
        publish_wav=fake.publish, inspect_wav=fake.inspect, concat=fake.concat, subtitles=fake.subtitles,
        artifacts_validator=fake.valid, tool_versions=lambda: type("Tools", (), {"ffmpeg": "ffmpeg test", "ffprobe": "ffprobe test"})())
    job_dir = service.create_and_run(NarrateRequest(video, script, "narration", tmp_path / "jobs"))
    context: dict[str, object] = {}
    assert service._load_utterances(job_dir, context)
    syntheses = fake.calls.count("synthesize")

    service._synthesis_step(job_dir, context, None)

    assert fake.calls.count("synthesize") == syntheses
