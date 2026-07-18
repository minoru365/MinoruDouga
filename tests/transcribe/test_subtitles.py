from __future__ import annotations

from pathlib import Path

import pytest

from minoru_studio.transcribe.contracts import SegmentResult, WordResult, WorkerResult
from minoru_studio.transcribe.subtitles import (
    Cue,
    SubtitleError,
    build_cues,
    render_srt,
    render_transcript,
    render_vtt,
    write_artifacts,
)


def result_with_segments(
    *segments: SegmentResult,
    duration_ms: int = 4_000,
    no_speech: bool = False,
) -> WorkerResult:
    return WorkerResult(
        schema_version=1,
        model="small",
        provider_version="test",
        language="ja",
        language_probability=1.0,
        duration_ms=duration_ms,
        duration_after_vad_ms=duration_ms,
        no_speech=no_speech,
        segments=segments,
    )


def result_with_segment(
    text: str,
    *,
    start_ms: int = 100,
    end_ms: int = 2_100,
    words: tuple[WordResult, ...] = (),
) -> WorkerResult:
    return result_with_segments(
        SegmentResult(start_ms, end_ms, text, words), duration_ms=max(end_ms, 2_200)
    )


def test_transcript_preserves_segment_wording_and_order_while_trimming_edges():
    result = result_with_segments(
        SegmentResult(100, 500, "  最初です。  ", ()),
        SegmentResult(600, 1_000, " 次です！ ", ()),
        duration_ms=1_200,
    )

    assert render_transcript(result) == "最初です。\n次です！\n"


def test_strong_punctuation_flushes_a_cue():
    cues = build_cues(result_with_segment("一文目です。二文目です。"))

    assert [cue.lines for cue in cues] == [("一文目です。",), ("二文目です。",)]


def test_soft_punctuation_is_preferred_when_text_exceeds_cue_limit():
    text = "あ" * 20 + "、" + "い" * 22

    cues = build_cues(result_with_segment(text))

    assert ["".join(cue.lines) for cue in cues] == ["あ" * 20 + "、", "い" * 22]


def test_hard_splitting_preserves_all_characters_when_no_punctuation_exists():
    text = "あ" * 43

    cues = build_cues(result_with_segment(text))

    assert ["".join(cue.lines) for cue in cues] == ["あ" * 42, "あ"]


def test_cues_wrap_at_twenty_one_code_points_and_two_lines():
    result = result_with_segment("あ" * 42, start_ms=100, end_ms=2_100)
    cues = build_cues(result)
    assert cues[0].lines == ("あ" * 21, "あ" * 21)
    assert all(len(line) <= 21 for cue in cues for line in cue.lines)


def test_word_timestamps_determine_cue_interval():
    result = result_with_segment(
        "先。後。",
        start_ms=100,
        end_ms=2_000,
        words=(WordResult(400, 700, "先。"), WordResult(1_300, 1_600, "後。")),
    )

    cues = build_cues(result)

    assert [(cue.start_ms, cue.end_ms) for cue in cues] == [(400, 700), (1_300, 1_600)]


def test_mismatched_word_text_falls_back_without_replacing_segment_text():
    result = result_with_segment(
        "正しい文。",
        start_ms=100,
        end_ms=500,
        words=(WordResult(200, 400, "別の文。"),),
    )

    cues = build_cues(result)

    assert [("".join(cue.lines), cue.start_ms, cue.end_ms) for cue in cues] == [
        ("正しい文。", 100, 500)
    ]


def test_all_zero_length_word_spans_fall_back_to_segment_timing():
    result = result_with_segment(
        "正しい文。",
        start_ms=100,
        end_ms=500,
        words=(WordResult(100, 100, "正しい文。"),),
    )

    cues = build_cues(result)

    assert [(cue.start_ms, cue.end_ms) for cue in cues] == [(100, 500)]


def test_segment_interval_is_proportionally_distributed_without_word_spans():
    cues = build_cues(result_with_segment("あ。い。", start_ms=100, end_ms=500))

    assert [(cue.start_ms, cue.end_ms) for cue in cues] == [(100, 300), (300, 500)]


def test_zero_length_cue_uses_backward_only_silence_when_it_cannot_merge():
    import minoru_studio.transcribe.subtitles as subtitles

    repaired = subtitles._repair_intervals(
        [
            subtitles._DraftCue("a" * 42, 100, 101),
            subtitles._DraftCue("b", 102, 102),
        ],
        duration_ms=102,
    )

    assert [(cue.text, cue.start_ms, cue.end_ms) for cue in repaired] == [
        ("a" * 42, 100, 101),
        ("b", 101, 102),
    ]


def test_whitespace_is_not_a_preferred_boundary_before_hard_split():
    text = "a" * 20 + " " + "b" * 22

    cues = build_cues(result_with_segment(text))

    assert ["".join(cue.lines) for cue in cues] == [text[:42], text[42:]]


def test_overlap_from_rounded_spans_is_repaired_monotonically():
    cues = build_cues(
        result_with_segment(
            "ab。cd。",
            start_ms=100,
            end_ms=400,
            words=(WordResult(100, 201, "ab。"), WordResult(200, 400, "cd。")),
        )
    )

    assert [(cue.start_ms, cue.end_ms) for cue in cues] == [(100, 201), (201, 400)]


def test_impossible_positive_intervals_raise_subtitle_error():
    with pytest.raises(SubtitleError, match="positive"):
        build_cues(
            result_with_segments(
                SegmentResult(100, 101, "a" * 41 + "。b。", ()), duration_ms=101
            )
        )


def test_no_speech_outputs_are_valid_and_empty(tmp_path: Path):
    result = result_with_segments(duration_ms=1_000, no_speech=True)

    assert build_cues(result) == ()
    assert render_transcript(result) == ""
    assert render_srt(result) == ""
    assert render_vtt(result) == "WEBVTT\n\n"
    transcript, srt, vtt = write_artifacts(tmp_path, result)
    assert transcript.read_text(encoding="utf-8") == ""
    assert srt.read_text(encoding="utf-8") == ""
    assert vtt.read_text(encoding="utf-8") == "WEBVTT\n\n"


def test_srt_and_vtt_use_required_timestamps_headers_and_indexes():
    result = result_with_segment(
        "最初。次。",
        start_ms=1_234,
        end_ms=4_567,
        words=(WordResult(1_234, 2_900, "最初。"), WordResult(2_900, 4_567, "次。")),
    )

    assert render_srt(result) == (
        "1\n00:00:01,234 --> 00:00:02,900\n最初。\n\n"
        "2\n00:00:02,900 --> 00:00:04,567\n次。\n"
    )
    assert render_vtt(result) == (
        "WEBVTT\n\n00:00:01.234 --> 00:00:02.900\n最初。\n\n"
        "00:00:02.900 --> 00:00:04.567\n次。\n"
    )


def test_artifacts_replace_destinations_atomically_after_validation(tmp_path: Path, monkeypatch):
    result = result_with_segment("完了。")
    transcript = tmp_path / "transcript.txt"
    srt = tmp_path / "subtitles.srt"
    vtt = tmp_path / "subtitles.vtt"
    transcript.write_text("old", encoding="utf-8")
    replacements: list[tuple[Path, Path, bool]] = []
    import minoru_studio.transcribe.subtitles as subtitles

    actual_replace = subtitles.os.replace

    def record_replace(source: Path, destination: Path) -> None:
        replacements.append((Path(source), Path(destination), Path(source).exists()))
        actual_replace(source, destination)

    monkeypatch.setattr(subtitles.os, "replace", record_replace)

    assert write_artifacts(tmp_path, result) == (transcript, srt, vtt)
    assert [destination for _, destination, _ in replacements] == [transcript, srt, vtt]
    assert all(source.parent == tmp_path and source.name.startswith(".") and exists for source, _, exists in replacements)
