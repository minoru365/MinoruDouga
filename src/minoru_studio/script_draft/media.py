from __future__ import annotations

import json
import math
from pathlib import Path
import struct
from typing import Callable, Literal
from uuid import uuid4

from minoru_studio.processes import ProcessResult, run_cancellable_process, run_process
from minoru_studio.script_draft.models import (
    FRAME_FORMAT,
    INTERVAL_MS,
    MAX_FRAME_EDGE,
    SCENE_THRESHOLD,
    FrameCandidate,
    VideoInfo,
)
from minoru_studio.timebase import seconds_to_milliseconds


Runner = Callable[..., ProcessResult]
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SHOWINFO_PTS_PREFIX = "pts_time:"


def probe_video(path: Path, *, runner: Runner = run_process) -> VideoInfo:
    result = runner([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "format=duration:stream=width,height,codec_type",
        "-of", "json", str(path),
    ])
    if result.returncode != 0:
        raise ValueError("input has no valid video stream")
    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        if stream["codec_type"] != "video":
            raise ValueError
        duration_ms = seconds_to_milliseconds(float(payload["format"]["duration"]))
        width = stream["width"]
        height = stream["height"]
        if type(width) is not int or type(height) is not int:
            raise ValueError
        return VideoInfo(duration_ms=duration_ms, width=width, height=height)
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("input has no valid video stream") from None


def extract_scene_candidates(
    source: Path,
    work_dir: Path,
    *,
    runner: Runner = run_cancellable_process,
    cancel_event: object | None = None,
) -> list[FrameCandidate]:
    return _extract_candidates(
        source, work_dir, "scene-frames", "scene",
        f"select='gt(scene,{SCENE_THRESHOLD:.2f})',showinfo,",
        runner, cancel_event,
    )


def extract_interval_candidates(
    source: Path,
    work_dir: Path,
    *,
    runner: Runner = run_cancellable_process,
    cancel_event: object | None = None,
) -> list[FrameCandidate]:
    return _extract_candidates(
        source, work_dir, "interval-frames", "interval",
        f"fps=1/{INTERVAL_MS // 1000},showinfo,",
        runner, cancel_event,
    )


def _extract_candidates(
    source: Path,
    work_dir: Path,
    directory_name: str,
    reason: Literal["scene", "interval"],
    filter_prefix: str,
    runner: Runner,
    cancel_event: object | None,
) -> list[FrameCandidate]:
    pass_dir = work_dir / directory_name
    output_dir = pass_dir / f".frames-{uuid4().hex}"
    output_dir.mkdir(parents=True, exist_ok=False)
    output_pattern = output_dir / "frame-%06d.png"
    filter_value = (
        f"{filter_prefix}scale=w='min({MAX_FRAME_EDGE},iw)':"
        f"h='min({MAX_FRAME_EDGE},ih)':force_original_aspect_ratio=decrease"
    )
    result = _run(runner, [
        "ffmpeg", "-nostdin", "-v", "info", "-y", "-i", str(source), "-an",
        "-vf", filter_value, str(output_pattern),
    ], cancel_event)
    if result.returncode != 0:
        raise ValueError("FFmpeg frame extraction failed")
    timestamps = _showinfo_timestamps(result.stderr)
    frames = sorted(output_dir.glob(f"frame-*.{FRAME_FORMAT}"))
    if len(timestamps) != len(frames):
        raise ValueError("frame output count does not match timestamps")
    resolved_work = work_dir.resolve()
    candidates: list[FrameCandidate] = []
    for timestamp, frame in zip(timestamps, frames, strict=True):
        resolved_frame = frame.resolve()
        if not resolved_frame.is_relative_to(resolved_work):
            raise ValueError("frame output is outside work directory")
        _validate_png_frame(resolved_frame)
        candidates.append(FrameCandidate(timestamp, frame, reason))
    return candidates


def _showinfo_timestamps(output: str) -> list[int]:
    timestamps: list[int] = []
    for fragment in output.split(_SHOWINFO_PTS_PREFIX)[1:]:
        value = fragment.split()[0] if fragment.split() else ""
        try:
            seconds = float(value)
            if not math.isfinite(seconds):
                raise ValueError
            timestamps.append(seconds_to_milliseconds(seconds))
        except ValueError:
            raise ValueError("invalid frame timestamp") from None
    return timestamps


def _validate_png_frame(path: Path) -> None:
    if path.suffix.casefold() != f".{FRAME_FORMAT}" or not path.is_file():
        raise ValueError("frame output is not a PNG file")
    try:
        with path.open("rb") as raw_file:
            header = raw_file.read(24)
        if header[:8] != _PNG_SIGNATURE or header[12:16] != b"IHDR":
            raise ValueError
        width, height = struct.unpack(">II", header[16:24])
    except (OSError, struct.error, ValueError):
        raise ValueError("frame output is not a PNG file") from None
    if width == 0 or height == 0 or width > MAX_FRAME_EDGE or height > MAX_FRAME_EDGE:
        raise ValueError("frame exceeds maximum edge")


def _run(runner: Runner, args: list[str], cancel_event: object | None) -> ProcessResult:
    return runner(args, cancel_event=cancel_event)
