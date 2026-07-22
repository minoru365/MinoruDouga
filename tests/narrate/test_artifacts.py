from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from minoru_studio.jobs.model import ArtifactRecord, JobMode
from minoru_studio.jobs.store import JobStore, fingerprint_artifact
from minoru_studio.narrate.artifacts import artifacts_valid, build_timeline, read_duration_warning, write_subtitles
from minoru_studio.narrate.models import DurationWarning, TimedUtterance, Utterance, WavInfo
import minoru_studio.narrate.artifacts as artifacts


def test_timeline_rounds_cumulative_decimal_boundaries_not_individual_durations(tmp_path: Path):
    utterances = [Utterance(index, text) for index, text in enumerate(("一", "二", "三"), 1)]
    wavs = [(tmp_path / f"utterance-{item.index:04d}.wav", WavInfo(Decimal("0.3335"), 10_000, 1, 2)) for item in utterances]
    cues = build_timeline(utterances, wavs)
    assert [(cue.start_ms, cue.end_ms) for cue in cues] == [(0, 334), (634, 967), (1267, 1601)]
    assert tuple(cue.wav_path for cue in cues) == tuple(f"utterances/utterance-{index:04d}.wav" for index in range(1, 4))


def test_subtitles_are_exact_utf8_create_only_srt_vtt_and_support_100_hours(tmp_path: Path):
    cues = build_timeline([Utterance(1, "日本語")], [(tmp_path / "utterance-0001.wav", WavInfo(Decimal("360000.001"), 1, 1, 1))], silence_ms=300)
    srt, vtt = write_subtitles(tmp_path / "outputs", cues)
    assert srt.read_text(encoding="utf-8") == "1\n00:00:00,000 --> 100:00:00,001\n日本語\n"
    assert vtt.read_text(encoding="utf-8") == "WEBVTT\n\n00:00:00.000 --> 100:00:00.001\n日本語\n"
    try:
        write_subtitles(tmp_path / "outputs", cues)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("publication must be create-only")


def test_subtitles_separate_multiple_srt_and_vtt_cue_blocks(tmp_path: Path):
    cues = (
        TimedUtterance(1, "一", 0, 100, "utterances/utterance-0001.wav"),
        TimedUtterance(2, "二", 400, 500, "utterances/utterance-0002.wav"),
    )
    srt, vtt = write_subtitles(tmp_path / "outputs", cues)
    assert srt.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:00,100\n一\n\n"
        "2\n00:00:00,400 --> 00:00:00,500\n二\n"
    )
    assert vtt.read_text(encoding="utf-8") == (
        "WEBVTT\n\n00:00:00.000 --> 00:00:00.100\n一\n\n"
        "00:00:00.400 --> 00:00:00.500\n二\n"
    )


def test_subtitle_staging_failure_removes_only_current_temporary_files(tmp_path: Path, monkeypatch):
    outputs = tmp_path / "outputs"
    cue = TimedUtterance(1, "字幕", 0, 100, "utterances/utterance-0001.wav")
    original_stage = artifacts._stage_text
    calls = 0
    def fail_second_stage(destination: Path, text: str) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("VTT staging failed")
        return original_stage(destination, text)
    monkeypatch.setattr(artifacts, "_stage_text", fail_second_stage)

    try:
        write_subtitles(outputs, (cue,))
    except OSError as exc:
        assert str(exc) == "VTT staging failed"
    else:
        raise AssertionError("second subtitle staging must fail")

    assert list(outputs.glob(".*.tmp")) == []
    assert not (outputs / "subtitles.srt").exists()
    assert not (outputs / "subtitles.vtt").exists()


def test_subtitle_write_failure_cleans_unreturned_vtt_temp_only(tmp_path: Path, monkeypatch):
    outputs = tmp_path / "outputs"; outputs.mkdir()
    preserved = outputs / "pre-existing.txt"; preserved.write_text("keep", encoding="utf-8")
    cue = TimedUtterance(1, "字幕", 0, 100, "utterances/utterance-0001.wav")
    original_write = Path.write_text
    def fail_after_vtt_temp_creation(path: Path, text: str, *args, **kwargs):
        if path.name.startswith(".subtitles.vtt."):
            path.touch()
            raise OSError("VTT write failed")
        return original_write(path, text, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail_after_vtt_temp_creation)

    try:
        write_subtitles(outputs, (cue,))
    except OSError as exc:
        assert str(exc) == "VTT write failed"
    else:
        raise AssertionError("VTT temporary write must fail")

    assert preserved.read_text(encoding="utf-8") == "keep"
    assert list(outputs.glob(".*.tmp")) == []
    assert not (outputs / "subtitles.srt").exists()
    assert not (outputs / "subtitles.vtt").exists()


def test_artifacts_valid_requires_exact_output_and_manifest_set_and_reads_content_free_warning(tmp_path: Path):
    job = JobStore().create(tmp_path, "narrate", JobMode.NARRATE)
    outputs = job / "outputs"; utterances = outputs / "utterances"; utterances.mkdir()
    for index in range(1, 3):
        (utterances / f"utterance-{index:04d}.wav").write_bytes(b"wav")
    for name in ("narration.wav", "subtitles.srt", "subtitles.vtt", "preview.mp4"):
        (outputs / name).write_bytes(b"x")
    records = [fingerprint_artifact(job, path, "utterance-wav") for path in sorted(utterances.glob("*.wav"))]
    records += [
        fingerprint_artifact(job, outputs / name, kind)
        for name, kind in (
            ("narration.wav", "narration-wav"), ("subtitles.srt", "subtitles-srt"),
            ("subtitles.vtt", "subtitles-vtt"), ("preview.mp4", "preview-mp4"),
        )
    ]
    JobStore().update(job, lambda manifest: manifest.artifacts.extend(records))
    assert artifacts_valid(job, include_preview=True)
    JobStore().update(job, lambda manifest: manifest.artifacts.__setitem__(0, ArtifactRecord(
        kind="wrong-kind", path=manifest.artifacts[0].path,
        size=manifest.artifacts[0].size, sha256=manifest.artifacts[0].sha256,
    )))
    assert not artifacts_valid(job, include_preview=True)
    (outputs / "extra.txt").write_text("no", encoding="utf-8")
    assert not artifacts_valid(job, include_preview=True)
    (outputs / "extra.txt").unlink()
    warning = job / "work" / "duration-warning.json"
    warning.write_text('{"source_duration_ms": 1000, "narration_duration_ms": 1300}\n', encoding="utf-8")
    assert read_duration_warning(job) == DurationWarning(1_000, 1_300)
