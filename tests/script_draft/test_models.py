from pathlib import Path

import pytest

from minoru_studio.script_draft.models import (
    FRAME_FORMAT,
    INTERVAL_MS,
    MAX_FRAME_EDGE,
    MERGE_TOLERANCE_MS,
    SCENE_THRESHOLD,
    FrameCandidate,
    FrameIndexEntry,
    ScriptDraftRequest,
    VideoInfo,
)


def test_script_draft_request_is_immutable():
    request = ScriptDraftRequest(Path("input.mp4"), "demo", Path("jobs"))

    with pytest.raises(AttributeError):
        request.name = "other"  # type: ignore[misc]


def test_video_info_requires_positive_native_integer_values():
    assert VideoInfo(duration_ms=1, width=1, height=1).duration_ms == 1

    for field, value in (
        ("duration_ms", 0),
        ("width", 0),
        ("height", 0),
        ("duration_ms", True),
        ("width", 1.0),
        ("height", "1"),
    ):
        values = {"duration_ms": 1, "width": 1, "height": 1}
        values[field] = value
        with pytest.raises(ValueError):
            VideoInfo(**values)


def test_frame_candidate_requires_a_non_negative_native_integer_time_and_legal_reason():
    assert FrameCandidate(0, Path("frame.png"), "scene").reason == "scene"

    for time_ms in (-1, True, 0.0):
        with pytest.raises(ValueError):
            FrameCandidate(time_ms, Path("frame.png"), "scene")
    with pytest.raises(ValueError):
        FrameCandidate(0, Path("frame.png"), "other")  # type: ignore[arg-type]


def test_frame_index_entry_defers_image_path_validation_but_rejects_invalid_values():
    entry = FrameIndexEntry(1, 0, "outside.jpg", ("scene",))
    assert entry.image_path == "outside.jpg"

    for index, time_ms, reasons in (
        (0, 0, ("scene",)),
        (True, 0, ("scene",)),
        (1, -1, ("scene",)),
        (1, True, ("scene",)),
        (1, 0, ()),
        (1, 0, ("other",)),
    ):
        with pytest.raises(ValueError):
            FrameIndexEntry(index, time_ms, "frame.png", reasons)  # type: ignore[arg-type]


def test_script_draft_fixed_settings_are_shared_contract_values():
    assert (
        SCENE_THRESHOLD,
        INTERVAL_MS,
        MERGE_TOLERANCE_MS,
        MAX_FRAME_EDGE,
        FRAME_FORMAT,
    ) == (0.30, 5_000, 100, 1_280, "png")
