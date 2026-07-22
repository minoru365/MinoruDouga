from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from minoru_studio.jobs.model import JobMode
from minoru_studio.jobs.store import JobStore, fingerprint_artifact
from minoru_studio.narrate.artifacts import artifacts_valid, build_timeline, read_duration_warning, write_subtitles
from minoru_studio.narrate.models import DurationWarning, Utterance, WavInfo


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


def test_artifacts_valid_requires_exact_output_and_manifest_set_and_reads_content_free_warning(tmp_path: Path):
    job = JobStore().create(tmp_path, "narrate", JobMode.NARRATE)
    outputs = job / "outputs"; utterances = outputs / "utterances"; utterances.mkdir()
    for index in range(1, 3):
        (utterances / f"utterance-{index:04d}.wav").write_bytes(b"wav")
    for name in ("narration.wav", "subtitles.srt", "subtitles.vtt", "preview.mp4"):
        (outputs / name).write_bytes(b"x")
    records = [fingerprint_artifact(job, path, "narrate") for path in sorted(utterances.glob("*.wav"))]
    records += [fingerprint_artifact(job, outputs / name, "narrate") for name in ("narration.wav", "subtitles.srt", "subtitles.vtt", "preview.mp4")]
    JobStore().update(job, lambda manifest: manifest.artifacts.extend(records))
    assert artifacts_valid(job, include_preview=True)
    (outputs / "extra.txt").write_text("no", encoding="utf-8")
    assert not artifacts_valid(job, include_preview=True)
    (outputs / "extra.txt").unlink()
    warning = job / "work" / "duration-warning.json"
    warning.write_text('{"source_duration_ms": 1000, "narration_duration_ms": 1300}\n', encoding="utf-8")
    assert read_duration_warning(job) == DurationWarning(1_000, 1_300)
