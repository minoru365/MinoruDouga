from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SCENE_THRESHOLD = 0.30
INTERVAL_MS = 5_000
MERGE_TOLERANCE_MS = 100
MAX_FRAME_EDGE = 1_280
FRAME_FORMAT = "png"

FrameReason = Literal["scene", "interval"]
_FRAME_REASONS = frozenset(("scene", "interval"))


@dataclass(frozen=True, slots=True)
class ScriptDraftRequest:
    input_path: Path
    name: str
    output_dir: Path


@dataclass(frozen=True, slots=True)
class VideoInfo:
    duration_ms: int
    width: int
    height: int

    def __post_init__(self) -> None:
        for name, value in (
            ("duration_ms", self.duration_ms),
            ("width", self.width),
            ("height", self.height),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class FrameCandidate:
    time_ms: int
    source_path: Path
    reason: FrameReason

    def __post_init__(self) -> None:
        _require_nonnegative_integer("time_ms", self.time_ms)
        if not isinstance(self.reason, str) or self.reason not in _FRAME_REASONS:
            raise ValueError(f"invalid frame reason: {self.reason}")


@dataclass(frozen=True, slots=True)
class FrameIndexEntry:
    index: int
    time_ms: int
    image_path: str
    reasons: tuple[FrameReason, ...]

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index <= 0:
            raise ValueError("index must be a positive integer")
        _require_nonnegative_integer("time_ms", self.time_ms)
        if (
            not isinstance(self.reasons, tuple)
            or not self.reasons
            or any(
                not isinstance(reason, str) or reason not in _FRAME_REASONS
                for reason in self.reasons
            )
        ):
            raise ValueError("reasons must contain only scene or interval")


def _require_nonnegative_integer(name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
