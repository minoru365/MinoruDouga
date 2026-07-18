import re


KNOWN_RATES = {
    "23.976": (24000, 1001),
    "24": (24, 1),
    "25": (25, 1),
    "29.97": (30000, 1001),
    "30": (30, 1),
    "50": (50, 1),
    "59.94": (60000, 1001),
    "60": (60, 1),
}


def _round_nonnegative(numerator, denominator):
    return (numerator + denominator // 2) // denominator


def parse_rate(value):
    label = re.sub(
        r"\s+DF$",
        "",
        str(value).strip(),
        flags=re.IGNORECASE,
    )
    if label not in KNOWN_RATES:
        raise ValueError("unsupported frame rate: {0}".format(value))
    return KNOWN_RATES[label]


def milliseconds_to_frame(time_ms, rate):
    if isinstance(time_ms, bool) or not isinstance(time_ms, int) or time_ms < 0:
        raise ValueError("time_ms must be non-negative integer")
    return _round_nonnegative(time_ms * rate[0], 1000 * rate[1])


def timeline_to_source_frames(timeline_frames, timeline_rate, source_rate):
    return max(
        1,
        _round_nonnegative(
            timeline_frames * source_rate[0] * timeline_rate[1],
            source_rate[1] * timeline_rate[0],
        ),
    )


def convert_cut_points(points_ms, rate):
    points = [milliseconds_to_frame(value, rate) for value in points_ms]
    if any(left >= right for left, right in zip(points, points[1:])):
        raise ValueError("frame cut points must be strictly increasing")
    return points


def source_window(marks, total_frames):
    video = (marks or {}).get("video") or (marks or {}).get("audio") or {}
    start = max(0, min(int(video.get("in", 0)), total_frames - 1))
    end = max(
        start + 1,
        min(int(video.get("out", total_frames - 1)) + 1, total_frames),
    )
    return start, end


def typical_still_target(points, every_n):
    interval_count = len(points) - 1
    if interval_count > every_n:
        starts = range(max(1, interval_count - every_n))
        lengths = [
            points[index + every_n] - points[index]
            for index in starts
        ]
    else:
        lengths = [points[-1] - points[0]]
    counts = {}
    for length in lengths:
        counts[length] = counts.get(length, 0) + 1
    return max(lengths, key=lambda value: counts[value])


def fit_steps(points, start_index, requested, available_timeline_frames):
    steps = min(requested, len(points) - 1 - start_index)
    while steps >= 1:
        if (
            points[start_index + steps] - points[start_index]
            <= available_timeline_frames
        ):
            return steps
        steps -= 1
    return 0
