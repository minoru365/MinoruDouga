from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import struct
from typing import Callable
from uuid import uuid4
import wave

from minoru_studio.processes import ProcessResult, run_cancellable_process, run_process
from minoru_studio.timebase import seconds_to_milliseconds


DENOISE_FILTER = "afftdn=nr=10:nf=-80:tn=1"
LOUDNESS_TARGET = "loudnorm=I=-16:LRA=11:TP=-1.5"
PREVIEW_DURATION_TOLERANCE_MS = 250


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration_ms: int
    has_audio: bool
    has_video: bool


@dataclass(frozen=True, slots=True)
class FontChoice:
    family: str
    file: Path


@dataclass(frozen=True, slots=True)
class MediaToolVersions:
    ffmpeg: str
    ffprobe: str


Runner = Callable[..., ProcessResult]


def read_media_tool_versions(*, runner: Runner = run_process) -> MediaToolVersions:
    return MediaToolVersions(
        ffmpeg=_first_version_line("ffmpeg", runner),
        ffprobe=_first_version_line("ffprobe", runner),
    )


def probe_media(path: str | Path, runner: Runner = run_process) -> MediaInfo:
    result = _run(runner, [
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
        "-of", "json", str(path),
    ])
    _require_success(result, "FFprobe")
    try:
        payload = json.loads(result.stdout)
        duration_value = payload["format"]["duration"]
        duration_ms = seconds_to_milliseconds(float(duration_value))
        stream_types = {stream["codec_type"] for stream in payload["streams"]}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid media duration or probe data") from exc
    if duration_ms <= 0:
        raise ValueError("media duration must be positive")
    return MediaInfo(
        duration_ms=duration_ms,
        has_audio="audio" in stream_types,
        has_video="video" in stream_types,
    )


def extract_audio(
    source: str | Path,
    destination: str | Path,
    *,
    normalize: bool,
    denoise: bool,
    runner: Runner = run_cancellable_process,
    cancel_event: object | None = None,
) -> Path:
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(output, ".wav")
    try:
        if normalize:
            statistics = _measure_loudness(source, denoise, runner, cancel_event)
            filter_value = _audio_filter(denoise, statistics)
        else:
            filter_value = _audio_filter(denoise)
        args = _audio_command(source, temporary, filter_value)
        _require_success(_run(runner, args, cancel_event), "FFmpeg audio extraction")
        _validate_pcm_wav(temporary)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def resolve_japanese_font(windows_dir: Path | None = None) -> FontChoice:
    root = windows_dir if windows_dir is not None else Path(os.environ.get("WINDIR", r"C:\\Windows"))
    fonts = root / "Fonts"
    for filename, family in (
        ("YuGothR.ttc", "Yu Gothic"),
        ("meiryo.ttc", "Meiryo"),
        ("msgothic.ttc", "MS Gothic"),
    ):
        candidate = fonts / filename
        if candidate.is_file():
            return FontChoice(family=family, file=candidate)
    raise FileNotFoundError("no supported Japanese Windows font found")


def render_preview(
    source: str | Path,
    subtitles: str | Path,
    destination: str | Path,
    media_info: MediaInfo,
    *,
    runner: Runner = run_cancellable_process,
    cancel_event: object | None = None,
) -> Path:
    if not media_info.has_video:
        raise ValueError("preview requires a video stream")
    if not media_info.has_audio:
        raise ValueError("preview requires an audio stream")
    font = resolve_japanese_font()
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(output, ".mp4")
    filter_value = (
        f"subtitles=filename='{_escape_filter_path(Path(subtitles))}':"
        f"fontsdir='{_escape_filter_path(font.file.parent)}':"
        f"force_style=FontName={font.family}"
    )
    args = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
        "-vf", filter_value, "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-movflags", "+faststart", str(temporary),
    ]
    try:
        _require_success(_run(runner, args, cancel_event), "FFmpeg preview rendering")
        validate_preview(temporary, media_info.duration_ms, runner=runner)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def validate_preview(
    path: str | Path,
    source_duration_ms: int,
    runner: Runner = run_process,
) -> None:
    if not isinstance(source_duration_ms, int) or isinstance(source_duration_ms, bool) or source_duration_ms <= 0:
        raise ValueError("source duration must be a positive integer millisecond value")
    info = probe_media(path, runner=runner)
    if not info.has_video:
        raise ValueError("preview requires a video stream")
    if not info.has_audio:
        raise ValueError("preview requires an audio stream")
    if abs(info.duration_ms - source_duration_ms) > PREVIEW_DURATION_TOLERANCE_MS:
        raise ValueError("preview duration differs from source duration")


def _first_version_line(tool: str, runner: Runner) -> str:
    result = _run(runner, [tool, "-version"])
    _require_success(result, tool)
    for line in result.stdout.splitlines():
        if line.strip():
            return line.strip()
    raise ValueError(f"{tool} did not report a version")


def _audio_command(source: str | Path, temporary: Path, filter_value: str | None) -> list[str]:
    args = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
    ]
    if filter_value is not None:
        args.extend(("-af", filter_value))
    return [*args, "-c:a", "pcm_s16le", str(temporary)]


def _measure_loudness(
    source: str | Path,
    denoise: bool,
    runner: Runner,
    cancel_event: object | None,
) -> dict[str, float]:
    filter_value = _audio_filter(denoise, measurement=True)
    args = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
        "-af", filter_value, "-f", "null", "-",
    ]
    result = _run(runner, args, cancel_event)
    _require_success(result, "FFmpeg loudness measurement")
    return _parse_loudness_statistics(result.stderr)


def _audio_filter(
    denoise: bool,
    statistics: dict[str, float] | None = None,
    *,
    measurement: bool = False,
) -> str | None:
    filters: list[str] = [DENOISE_FILTER] if denoise else []
    if measurement:
        filters.append(f"{LOUDNESS_TARGET}:print_format=json")
    elif statistics is not None:
        filters.append(
            f"{LOUDNESS_TARGET}:measured_I={statistics['input_i']}:"
            f"measured_LRA={statistics['input_lra']}:measured_TP={statistics['input_tp']}:"
            f"measured_thresh={statistics['input_thresh']}:offset={statistics['target_offset']}:"
            "linear=true:print_format=json"
        )
    return ",".join(filters) if filters else None


def _parse_loudness_statistics(stderr: str) -> dict[str, float]:
    candidates = re.findall(r"\{[^{}]*\}", stderr, flags=re.DOTALL)
    for candidate in reversed(candidates):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        required = ("input_i", "input_lra", "input_tp", "input_thresh", "target_offset")
        try:
            values = {name: float(payload[name]) for name in required}
        except (KeyError, TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in values.values()):
            return values
    raise ValueError("invalid loudness statistics")


def _validate_pcm_wav(path: Path) -> None:
    try:
        with path.open("rb") as raw_file:
            riff = raw_file.read(4)
        if riff != b"RIFF":
            raise ValueError("audio output is not RIFF PCM WAV")
        with wave.open(str(path), "rb") as input_file:
            if (
                input_file.getcomptype() != "NONE"
                or input_file.getnchannels() != 1
                or input_file.getframerate() != 16_000
                or input_file.getsampwidth() != 2
                or input_file.getnframes() <= 0
            ):
                raise ValueError("audio output must be 16 kHz mono 16-bit PCM WAV with frames")
            while frames := input_file.readframes(8_192):
                samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
                if -32768 in samples or 32767 in samples:
                    raise ValueError("audio output contains clipped PCM samples")
    except wave.Error as exc:
        raise ValueError("audio output is not RIFF PCM WAV") from exc


def _temporary_sibling(destination: Path, suffix: str) -> Path:
    return destination.with_name(f".{destination.stem}.{uuid4().hex}{suffix}")


def _escape_filter_path(path: Path) -> str:
    return (
        path.as_posix()
        .replace("\\", "/")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def _run(runner: Runner, args: list[str], cancel_event: object | None = None) -> ProcessResult:
    if runner is run_cancellable_process:
        return runner(args, cancel_event=cancel_event)
    return runner(args)


def _require_success(result: ProcessResult, action: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(f"{action} failed; source may have no audio stream")
