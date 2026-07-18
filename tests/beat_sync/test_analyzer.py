import pytest

from minoru_studio.beat_sync.analyzer import (
    AnalysisError,
    LibrosaBeatAnalyzer,
    normalize_analysis,
    seconds_to_milliseconds,
)


def test_seconds_round_to_integer_milliseconds():
    assert seconds_to_milliseconds(0.5024) == 502
    assert seconds_to_milliseconds(0.5025) == 503


def test_seconds_wrapper_converts_timebase_errors_to_analysis_errors():
    with pytest.raises(AnalysisError, match="finite and non-negative"):
        seconds_to_milliseconds(float("nan"))


def test_normalization_deduplicates_and_keeps_exact_end():
    result = normalize_analysis(
        2.04,
        120.0,
        (0.1, 0.5, 0.5004, 1.0, 1.95),
    )
    assert result.beats_ms == (100, 500, 1_000, 1_950)
    assert result.cut_points_ms == (0, 500, 1_000, 2_040)


def test_insufficient_beats_fail():
    with pytest.raises(AnalysisError, match="at least two"):
        normalize_analysis(2.0, 120.0, (0.5,))


def test_librosa_backend_is_injectable(tmp_path):
    class FakeLibrosa:
        def load(self, path, sr, mono):
            return [0.0], 48_000

        def get_duration(self, y, sr):
            return 2.0

        class beat:
            @staticmethod
            def beat_track(y, sr, units):
                return [120.0], [0.5, 1.0, 1.5]

    class FakeNumpy:
        @staticmethod
        def atleast_1d(value):
            return value

    song = tmp_path / "song.wav"
    song.write_bytes(b"audio")
    result = LibrosaBeatAnalyzer(FakeLibrosa(), FakeNumpy()).analyze(song)
    assert result.duration_ms == 2_000
