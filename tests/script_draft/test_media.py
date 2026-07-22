import json
import struct
from pathlib import Path

import pytest

from minoru_studio.processes import ProcessResult
from minoru_studio.script_draft.media import (
    extract_interval_candidates,
    extract_scene_candidates,
    probe_video,
)
from minoru_studio.script_draft.models import FrameCandidate, VideoInfo


def _result(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> ProcessResult:
    return ProcessResult(returncode, stdout, stderr, "ffmpeg")


def _png(path: Path, width: int = 1280, height: int = 720) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def test_probe_video_returns_content_validated_first_video_stream_metadata(tmp_path: Path):
    source = tmp_path / "input.mp4"
    calls: list[list[str]] = []

    def runner(args):
        calls.append(list(args))
        return _result(stdout=json.dumps({
            "format": {"duration": "12.345"},
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
        }))

    assert probe_video(source, runner=runner) == VideoInfo(12_345, 1920, 1080)
    assert calls == [[
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "format=duration:stream=width,height,codec_type",
        "-of", "json", str(source),
    ]]


@pytest.mark.parametrize("result", [
    _result(returncode=1, stderr="source details must not leak"),
    _result(stdout="not json"),
    _result(stdout=json.dumps({"format": {"duration": "1"}, "streams": []})),
    _result(stdout=json.dumps({"format": {"duration": "1"}, "streams": [{"codec_type": "video", "width": 0, "height": 1}]})),
    _result(stdout=json.dumps({"format": {"duration": "nan"}, "streams": [{"codec_type": "video", "width": 1, "height": 1}]})),
])
def test_probe_video_rejects_every_invalid_probe_shape(result: ProcessResult, tmp_path: Path):
    with pytest.raises(ValueError, match="^input has no valid video stream$"):
        probe_video(tmp_path / "input.mp4", runner=lambda args: result)


def test_extract_scene_candidates_pairs_showinfo_times_with_safe_png_outputs(tmp_path: Path):
    source = tmp_path / "input.mp4"
    work = tmp_path / "work"
    calls: list[tuple[list[str], object]] = []
    cancel_event = object()

    def runner(args, *, cancel_event):
        calls.append((list(args), cancel_event))
        output = Path(args[-1]).parent
        _png(output / "frame-000001.png")
        _png(output / "frame-000002.png", 640, 480)
        return _result(stderr="showinfo pts_time:0.000\nshowinfo pts_time:2.345\n")

    candidates = extract_scene_candidates(source, work, runner=runner, cancel_event=cancel_event)

    assert [(candidate.time_ms, candidate.reason) for candidate in candidates] == [
        (0, "scene"), (2_345, "scene"),
    ]
    assert all(candidate.source_path.parent.parent == work / "scene-frames" for candidate in candidates)
    assert all(candidate.source_path.parent.name.startswith(".frames-") for candidate in candidates)
    assert calls[0] == ([
        "ffmpeg", "-nostdin", "-v", "info", "-y", "-i", str(source), "-an",
        "-vf", "select='gt(scene,0.30)',showinfo,scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease",
        str(candidates[0].source_path.parent / "frame-%06d.png"),
    ], cancel_event)


def test_extract_interval_candidates_requires_the_initial_zero_millisecond_frame(tmp_path: Path):
    source = tmp_path / "input.mp4"
    work = tmp_path / "work"

    def runner(args, *, cancel_event):
        _png(Path(args[-1]).parent / "frame-000001.png")
        _png(Path(args[-1]).parent / "frame-000002.png")
        return _result(stderr="showinfo pts_time:0.0\nshowinfo pts_time:5.0\n")

    candidates = extract_interval_candidates(source, work, runner=runner)

    assert [(candidate.time_ms, candidate.reason) for candidate in candidates] == [
        (0, "interval"), (5_000, "interval"),
    ]
    assert candidates[0].source_path.parent.parent == work / "interval-frames"
    assert candidates[0].source_path.parent.name.startswith(".frames-")


@pytest.mark.parametrize("timestamps", ("", "showinfo pts_time:5.0\n"))
def test_extract_interval_candidates_rejects_missing_initial_zero_millisecond_frame(
    tmp_path: Path, timestamps: str,
):
    def runner(args, *, cancel_event):
        if timestamps:
            _png(Path(args[-1]).parent / "frame-000001.png")
        return _result(stderr=timestamps)

    with pytest.raises(ValueError, match="^interval extraction requires exactly one initial zero-millisecond frame$"):
        extract_interval_candidates(tmp_path / "input.mp4", tmp_path / "work", runner=runner)


def test_extract_interval_candidates_rejects_duplicate_zero_millisecond_frames(tmp_path: Path):
    def runner(args, *, cancel_event):
        output = Path(args[-1]).parent
        _png(output / "frame-000001.png")
        _png(output / "frame-000002.png")
        return _result(stderr="showinfo pts_time:0.0\nshowinfo pts_time:0.0\n")

    with pytest.raises(ValueError, match="^interval extraction requires exactly one initial zero-millisecond frame$"):
        extract_interval_candidates(tmp_path / "input.mp4", tmp_path / "work", runner=runner)


def test_candidate_extraction_isolates_outputs_without_mutating_matching_source(tmp_path: Path):
    work = tmp_path / "work"
    output = work / "scene-frames"
    output.mkdir(parents=True)
    source = output / "frame-000001.png"
    _png(source, 1, 1)
    source_before = source.read_bytes()
    source_mtime_ns = source.stat().st_mtime_ns

    def runner(args, *, cancel_event):
        assert source.read_bytes() == source_before
        temporary_output = Path(args[-1]).parent
        assert temporary_output.parent == output
        _png(temporary_output / "frame-000001.png")
        return _result(stderr="showinfo pts_time:1.0\n")

    candidates = extract_scene_candidates(source, work, runner=runner)

    assert candidates[0].source_path != source
    assert candidates[0].source_path.parent.parent == output
    assert source.read_bytes() == source_before
    assert source.stat().st_mtime_ns == source_mtime_ns


@pytest.mark.parametrize("result, width, expected", [
    (_result(returncode=1), 1280, "FFmpeg frame extraction failed"),
    (_result(stderr="showinfo pts_time:nan\n"), 1280, "invalid frame timestamp"),
    (_result(stderr="showinfo pts_time:1.0\n"), 1281, "frame exceeds maximum edge"),
])
def test_candidate_extraction_rejects_invalid_results_without_leaking_process_output(
    result: ProcessResult, width: int, expected: str, tmp_path: Path,
):
    def runner(args, *, cancel_event):
        _png(Path(args[-1]).parent / "frame-000001.png", width, 1)
        return result

    with pytest.raises(ValueError, match=f"^{expected}$"):
        extract_scene_candidates(tmp_path / "input.mp4", tmp_path / "work", runner=runner)


def test_candidate_extraction_rejects_showinfo_frame_count_mismatch(tmp_path: Path):
    def runner(args, *, cancel_event):
        _png(Path(args[-1]).parent / "frame-000001.png")
        return _result(stderr="showinfo pts_time:1.0\nshowinfo pts_time:2.0\n")

    with pytest.raises(ValueError, match="^frame output count does not match timestamps$"):
        extract_scene_candidates(tmp_path / "input.mp4", tmp_path / "work", runner=runner)


def test_candidate_extraction_rejects_non_png_frame_content(tmp_path: Path):
    def runner(args, *, cancel_event):
        (Path(args[-1]).parent / "frame-000001.png").write_bytes(b"not a PNG")
        return _result(stderr="showinfo pts_time:1.0\n")

    with pytest.raises(ValueError, match="^frame output is not a PNG file$"):
        extract_scene_candidates(tmp_path / "input.mp4", tmp_path / "work", runner=runner)
