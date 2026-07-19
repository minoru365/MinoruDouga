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

    assert candidates == [
        FrameCandidate(0, work / "scene-frames" / "frame-000001.png", "scene"),
        FrameCandidate(2_345, work / "scene-frames" / "frame-000002.png", "scene"),
    ]
    assert calls == [([
        "ffmpeg", "-nostdin", "-v", "info", "-y", "-i", str(source), "-an",
        "-vf", "select='gt(scene,0.30)',showinfo,scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease",
        str(work / "scene-frames" / "frame-%06d.png"),
    ], cancel_event)]


def test_extract_interval_candidates_uses_five_second_filter_and_reason(tmp_path: Path):
    source = tmp_path / "input.mp4"
    work = tmp_path / "work"

    def runner(args, *, cancel_event):
        _png(Path(args[-1]).parent / "frame-000001.png")
        return _result(stderr="showinfo pts_time:5.0\n")

    candidates = extract_interval_candidates(source, work, runner=runner)

    assert candidates == [FrameCandidate(5_000, work / "interval-frames" / "frame-000001.png", "interval")]


def test_candidate_extraction_removes_only_stale_sequential_temporary_outputs(tmp_path: Path):
    work = tmp_path / "work"
    output = work / "scene-frames"
    output.mkdir(parents=True)
    _png(output / "frame-000002.png")
    preserved = output / "user-note.txt"
    preserved.write_text("preserve")

    def runner(args, *, cancel_event):
        assert not (output / "frame-000002.png").exists()
        _png(output / "frame-000001.png")
        return _result(stderr="showinfo pts_time:1.0\n")

    candidates = extract_scene_candidates(tmp_path / "input.mp4", work, runner=runner)

    assert candidates == [FrameCandidate(1_000, output / "frame-000001.png", "scene")]
    assert preserved.read_text() == "preserve"


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
