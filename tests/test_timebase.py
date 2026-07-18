import pytest

from minoru_studio.timebase import (
    FrameRate,
    frame_to_milliseconds,
    milliseconds_to_frame,
    parse_frame_rate,
)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("24", FrameRate(24, 1)),
        ("30", FrameRate(30, 1)),
        ("60", FrameRate(60, 1)),
        ("23.976", FrameRate(24000, 1001)),
        ("29.97", FrameRate(30000, 1001)),
    ],
)
def test_parse_frame_rate(label, expected):
    assert parse_frame_rate(label) == expected


@pytest.mark.parametrize(
    ("time_ms", "rate", "expected_frame"),
    [
        (0, FrameRate(24), 0),
        (502, FrameRate(30), 15),
        (1_000, FrameRate(60), 60),
        (1_000, FrameRate(24000, 1001), 24),
        (1_000, FrameRate(30000, 1001), 30),
    ],
)
def test_milliseconds_to_frame(time_ms, rate, expected_frame):
    assert milliseconds_to_frame(time_ms, rate) == expected_frame


def test_frame_round_trip_is_within_half_frame():
    rate = FrameRate(30000, 1001)
    source_ms = 18_342
    frame = milliseconds_to_frame(source_ms, rate)
    restored_ms = frame_to_milliseconds(frame, rate)
    assert abs(restored_ms - source_ms) <= 17


@pytest.mark.parametrize("value", ["0", "-24", "abc", "23.98"])
def test_unsupported_frame_rate_is_rejected(value):
    with pytest.raises(ValueError):
        parse_frame_rate(value)


def test_negative_time_is_rejected():
    with pytest.raises(ValueError):
        milliseconds_to_frame(-1, FrameRate(24))
