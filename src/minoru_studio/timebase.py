from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
import math


@dataclass(frozen=True, slots=True)
class FrameRate:
    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if self.numerator <= 0 or self.denominator <= 0:
            raise ValueError("frame-rate numerator and denominator must be positive")


_KNOWN_RATES = {
    "23.976": FrameRate(24000, 1001),
    "24": FrameRate(24, 1),
    "25": FrameRate(25, 1),
    "29.97": FrameRate(30000, 1001),
    "30": FrameRate(30, 1),
    "50": FrameRate(50, 1),
    "59.94": FrameRate(60000, 1001),
    "60": FrameRate(60, 1),
}


def parse_frame_rate(value: str) -> FrameRate:
    try:
        return _KNOWN_RATES[value.strip()]
    except KeyError as exc:
        raise ValueError(f"unsupported frame rate: {value}") from exc


def seconds_to_milliseconds(value: float) -> int:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError("time values must be finite and non-negative")
    milliseconds = Decimal(str(numeric)) * 1_000
    return int(milliseconds.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _round_nonnegative(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def milliseconds_to_frame(time_ms: int, rate: FrameRate) -> int:
    if time_ms < 0:
        raise ValueError("time_ms must be non-negative")
    return _round_nonnegative(
        time_ms * rate.numerator,
        1000 * rate.denominator,
    )


def frame_to_milliseconds(frame: int, rate: FrameRate) -> int:
    if frame < 0:
        raise ValueError("frame must be non-negative")
    return _round_nonnegative(
        frame * 1000 * rate.denominator,
        rate.numerator,
    )
