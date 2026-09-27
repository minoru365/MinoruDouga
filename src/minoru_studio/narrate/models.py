from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath


@dataclass(frozen=True, slots=True)
class NarrateRequest:
    input_path: Path
    script_path: Path
    name: str
    output_dir: Path
    preview: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (
            ("input_path", self.input_path),
            ("script_path", self.script_path),
            ("output_dir", self.output_dir),
        ):
            if not isinstance(value, Path):
                raise ValueError(f"{field_name} must be a Path")
        _require_nonblank_text("name", self.name)
        if type(self.preview) is not bool:
            raise ValueError("preview must be a bool")


@dataclass(frozen=True, slots=True)
class StoryboardRequest:
    """A descriptor-owned narration request.

    The descriptor deliberately owns both the narration and visual references so
    callers cannot accidentally pair an unrelated script with a visual sequence.
    """

    input_path: Path
    name: str
    output_dir: Path
    preview: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (("input_path", self.input_path), ("output_dir", self.output_dir)):
            if not isinstance(value, Path):
                raise ValueError(f"{field_name} must be a Path")
        _require_nonblank_text("name", self.name)
        if type(self.preview) is not bool:
            raise ValueError("preview must be a bool")


@dataclass(frozen=True, slots=True)
class VideoInfo:
    duration_ms: int
    width: int
    height: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("duration_ms", self.duration_ms),
            ("width", self.width),
            ("height", self.height),
        ):
            _require_positive_integer(field_name, value)


@dataclass(frozen=True, slots=True)
class ImageInfo:
    width: int
    height: int

    def __post_init__(self) -> None:
        _require_positive_integer("width", self.width)
        _require_positive_integer("height", self.height)


@dataclass(frozen=True, slots=True)
class Utterance:
    index: int
    text: str

    def __post_init__(self) -> None:
        _require_positive_integer("index", self.index)
        _require_nonblank_text("text", self.text)


@dataclass(frozen=True, slots=True)
class WavInfo:
    duration_seconds: Decimal
    sample_rate: int
    channels: int
    sample_width: int

    def __post_init__(self) -> None:
        if type(self.duration_seconds) is not Decimal or not self.duration_seconds.is_finite() or self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be a positive finite Decimal")
        for field_name, value in (
            ("sample_rate", self.sample_rate),
            ("channels", self.channels),
            ("sample_width", self.sample_width),
        ):
            _require_positive_integer(field_name, value)


@dataclass(frozen=True, slots=True)
class TimedUtterance:
    index: int
    text: str
    start_ms: int
    end_ms: int
    wav_path: str

    def __post_init__(self) -> None:
        _require_positive_integer("index", self.index)
        _require_nonblank_text("text", self.text)
        _require_nonnegative_integer("start_ms", self.start_ms)
        _require_nonnegative_integer("end_ms", self.end_ms)
        if self.start_ms >= self.end_ms:
            raise ValueError("start_ms must be less than end_ms")
        _require_relative_posix_path("wav_path", self.wav_path)


@dataclass(frozen=True, slots=True)
class VoicevoxProvenance:
    engine_version: str
    speaker_name: str
    style_name: str
    speaker_id: int

    def __post_init__(self) -> None:
        _require_nonblank_text("engine_version", self.engine_version)
        _require_nonblank_text("speaker_name", self.speaker_name)
        _require_nonblank_text("style_name", self.style_name)
        _require_positive_integer("speaker_id", self.speaker_id)


@dataclass(frozen=True, slots=True)
class DurationWarning:
    source_duration_ms: int
    narration_duration_ms: int

    def __post_init__(self) -> None:
        _require_positive_integer("source_duration_ms", self.source_duration_ms)
        _require_positive_integer("narration_duration_ms", self.narration_duration_ms)


def _require_positive_integer(name: str, value: object) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _require_nonnegative_integer(name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_nonblank_text(name: str, value: object) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be nonblank text")


def _require_relative_posix_path(name: str, value: object) -> None:
    if type(value) is not str or not value or "\\" in value:
        raise ValueError(f"{name} must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be a relative POSIX path without '..'")
