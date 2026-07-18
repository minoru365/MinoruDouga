import pytest

from resolve_adapter.minoru_studio_resolve.placement import (
    corrected_source_length,
    convert_cut_points,
    fit_steps,
    milliseconds_to_frame,
    parse_rate,
    source_frames_available,
    source_window,
    typical_still_target,
)


def test_fractional_rates_and_df_suffix_are_exact():
    assert parse_rate("23.976") == (24000, 1001)
    assert parse_rate("29.97 DF") == (30000, 1001)
    assert milliseconds_to_frame(1_000, (24000, 1001)) == 24


def test_integral_rate_with_resolve_decimal_suffix_is_exact():
    assert parse_rate("24.0") == (24, 1)


def test_duplicate_frame_conversion_is_rejected():
    with pytest.raises(ValueError, match="strictly increasing"):
        convert_cut_points([0, 1, 1000], (24, 1))


def test_marks_are_converted_to_exclusive_end():
    assert source_window({"video": {"in": 10, "out": 19}}, 100) == (10, 20)
    assert source_window({}, 100) == (0, 100)


def test_still_target_excludes_outro_and_uses_first_mode_tie():
    points = [0, 12, 24, 36, 96]
    assert typical_still_target(points, 1) == 12


def test_short_source_reduces_requested_beats():
    points = [0, 12, 24, 36]
    assert fit_steps(points, 0, 3, available_timeline_frames=25) == 2


def test_source_availability_converts_to_timeline_frames():
    assert source_frames_available((10, 40), (60, 1), (30, 1)) == 15


def test_corrected_source_length_moves_once_toward_target():
    assert corrected_source_length(30, 15, 14, (30, 1), (30, 1)) == 31
    assert corrected_source_length(30, 15, 16, (30, 1), (30, 1)) == 29
