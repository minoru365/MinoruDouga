from __future__ import annotations

import json
import math
import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from collections.abc import Sequence
from typing import Callable
from uuid import uuid4
import wave

from minoru_studio.narrate.models import VideoInfo, WavInfo
from minoru_studio.processes import ProcessResult, run_cancellable_process, run_process
from minoru_studio.transcribe.media import (
    PREVIEW_DURATION_TOLERANCE_MS, FontChoice, _escape_filter_path,
    is_supported_japanese_font, resolve_japanese_font,
)


Runner = Callable[..., ProcessResult]


def probe_video(path: Path, *, runner: Runner = run_process) -> VideoInfo:
    result = _run(runner, [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "format=duration:stream=codec_type,width,height",
        "-of", "json", str(path),
    ])
    _require_success(result, "FFprobe video")
    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        if stream["codec_type"] != "video":
            raise ValueError
        duration = _decimal_duration(payload["format"]["duration"])
        width, height = stream["width"], stream["height"]
        return VideoInfo(_milliseconds(duration), width, height)
    except (IndexError, KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError):
        raise ValueError("input has no valid video stream") from None


def inspect_wav(path: Path, *, runner: Runner = run_process) -> WavInfo:
    wav_path = Path(path)
    try:
        with wav_path.open("rb") as raw:
            if raw.read(4) != b"RIFF":
                raise ValueError
        with wave.open(str(wav_path), "rb") as input_file:
            if input_file.getcomptype() != "NONE" or input_file.getnframes() <= 0:
                raise ValueError
            sample_rate = input_file.getframerate()
            channels = input_file.getnchannels()
            sample_width = input_file.getsampwidth()
    except (OSError, wave.Error, ValueError):
        raise ValueError("audio output is not RIFF PCM WAV with frames") from None
    result = _run(runner, [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(wav_path),
    ])
    _require_success(result, "FFprobe WAV")
    try:
        payload = json.loads(result.stdout)
        return WavInfo(_decimal_duration(payload["format"]["duration"]), sample_rate, channels, sample_width)
    except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError):
        raise ValueError("invalid WAV duration") from None


def publish_wav(data: bytes, destination: Path, *, runner: Runner = run_process) -> WavInfo:
    if not isinstance(data, bytes) or not data:
        raise ValueError("WAV data must be nonempty bytes")
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(output, ".wav")
    try:
        temporary.write_bytes(data)
        info = inspect_wav(temporary, runner=runner)
        _publish_create_only(temporary, output)
        return info
    finally:
        temporary.unlink(missing_ok=True)


def concat_wavs(
    wavs: Sequence[Path], destination: Path, *, silence_ms: int,
    runner: Runner = run_cancellable_process, cancel_event: object | None = None,
) -> WavInfo:
    if not wavs:
        raise ValueError("at least one utterance WAV is required")
    if type(silence_ms) is not int or silence_ms <= 0:
        raise ValueError("silence_ms must be a positive integer")
    infos = [inspect_wav(path, runner=runner) for path in wavs]
    shape = (infos[0].sample_rate, infos[0].channels, infos[0].sample_width)
    if any((info.sample_rate, info.channels, info.sample_width) != shape for info in infos[1:]):
        raise ValueError("utterance WAV shapes must match")
    frames, remainder = divmod(shape[0] * silence_ms, 1_000)
    if remainder:
        raise ValueError("silence duration is not exact for sample rate")
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    silence = _temporary_sibling(output, ".silence.wav")
    concat_file = _temporary_sibling(output, ".concat.txt")
    temporary = _temporary_sibling(output, ".wav")
    try:
        _write_silence(silence, *shape, frames)
        sources: list[Path] = []
        for index, wav in enumerate(wavs):
            if index:
                sources.append(silence)
            sources.append(Path(wav).resolve())
        concat_file.write_text("".join(f"file '{_concat_path(path)}'\n" for path in sources), encoding="utf-8")
        args = [
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file), "-vn", "-c:a", "pcm_s16le", "-ar", str(shape[0]),
            "-ac", str(shape[1]), str(temporary),
        ]
        _require_success(_run(runner, args, cancel_event), "FFmpeg concat")
        info = inspect_wav(temporary, runner=runner)
        if (info.sample_rate, info.channels) != shape[:2]:
            raise ValueError("concatenated WAV shape does not match utterances")
        _publish_create_only(temporary, output)
        return info
    finally:
        for path in (temporary, concat_file, silence):
            path.unlink(missing_ok=True)


def render_preview(
    source: Path, narration: Path, subtitles: Path, destination: Path,
    video: VideoInfo, narration_ms: int, *, font: FontChoice | None = None,
    runner: Runner = run_cancellable_process, cancel_event: object | None = None,
) -> Path:
    if not isinstance(video, VideoInfo):
        raise ValueError("preview requires valid video info")
    if type(narration_ms) is not int or narration_ms <= 0:
        raise ValueError("narration duration must be positive")
    selected_font = font if font is not None else resolve_japanese_font()
    if not is_supported_japanese_font(selected_font):
        raise ValueError("preview font is invalid")
    expected_ms = max(video.duration_ms, narration_ms)
    output = Path(destination); output.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(output, ".mp4")
    subtitle_filter = (
        f"subtitles=filename='{_escape_filter_path(Path(subtitles))}':"
        f"fontsdir='{_escape_filter_path(selected_font.file.parent)}':"
        f"force_style=FontName={selected_font.family}"
    )
    video_filter = subtitle_filter if narration_ms <= video.duration_ms else (
        f"tpad=stop_mode=clone:stop_duration={(narration_ms - video.duration_ms) / 1000:.3f},{subtitle_filter}"
    )
    args = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source), "-i", str(narration),
        "-filter:v", video_filter, "-map", "0:v:0", "-map", "1:a:0", "-t", f"{expected_ms / 1000:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(temporary),
    ]
    try:
        _require_success(_run(runner, args, cancel_event), "FFmpeg preview rendering")
        validate_preview(temporary, expected_ms, runner=runner)
        _publish_create_only(temporary, output)
        return output
    finally:
        temporary.unlink(missing_ok=True)


def validate_preview(path: Path, expected_duration_ms: int, *, runner: Runner = run_process) -> None:
    if type(expected_duration_ms) is not int or expected_duration_ms <= 0:
        raise ValueError("preview duration must be a positive integer millisecond value")
    result = _run(runner, [
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
        "-of", "json", str(path),
    ])
    _require_success(result, "FFprobe preview")
    try:
        payload = json.loads(result.stdout)
        duration_ms = _milliseconds(_decimal_duration(payload["format"]["duration"]))
        stream_types = {stream["codec_type"] for stream in payload["streams"]}
    except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError):
        raise ValueError("invalid preview probe data") from None
    if "video" not in stream_types or "audio" not in stream_types:
        raise ValueError("preview requires audio and video streams")
    if abs(duration_ms - expected_duration_ms) > PREVIEW_DURATION_TOLERANCE_MS:
        raise ValueError("preview duration differs from narration/video duration")


def _decimal_duration(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError
    duration = Decimal(value)
    if not duration.is_finite() or duration <= 0:
        raise ValueError
    return duration


def _milliseconds(seconds: Decimal) -> int:
    return int((seconds * 1_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _write_silence(path: Path, sample_rate: int, channels: int, sample_width: int, frames: int) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels); output.setsampwidth(sample_width); output.setframerate(sample_rate)
        output.writeframes(b"\0" * frames * channels * sample_width)


def _temporary_sibling(path: Path, suffix: str) -> Path:
    return path.with_name(f".{path.stem}.{uuid4().hex}{suffix}")


def _concat_path(path: Path) -> str:
    return str(path.resolve()).replace("'", r"'\\''")


def _publish_create_only(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise ValueError("final artifact already exists") from None


def _run(runner: Runner, args: list[str], cancel_event: object | None = None) -> ProcessResult:
    if runner is run_cancellable_process:
        return runner(args, cancel_event=cancel_event)
    return runner(args)


def _require_success(result: ProcessResult, action: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(f"{action} failed")
