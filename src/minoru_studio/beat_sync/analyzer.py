from __future__ import annotations

import math
from pathlib import Path

from minoru_studio.beat_sync.models import BeatAnalysis
from minoru_studio.timebase import seconds_to_milliseconds as _seconds_to_milliseconds


class AnalysisError(RuntimeError):
    pass


def seconds_to_milliseconds(value: float) -> int:
    try:
        return _seconds_to_milliseconds(value)
    except ValueError as exc:
        raise AnalysisError(str(exc)) from exc


def normalize_analysis(
    duration_seconds: float,
    bpm: float,
    beat_seconds,
    minimum_cut_ms: int = 150,
) -> BeatAnalysis:
    duration_seconds = float(duration_seconds)
    duration_ms = seconds_to_milliseconds(duration_seconds)
    if duration_ms < minimum_cut_ms:
        raise AnalysisError("audio is shorter than minimum_cut_ms")
    bpm = float(bpm)
    if not math.isfinite(bpm) or bpm <= 0:
        raise AnalysisError("bpm must be finite and positive")
    beats_ms = tuple(
        sorted(
            {
                seconds_to_milliseconds(value)
                for value in beat_seconds
                if math.isfinite(float(value)) and 0 < float(value) < duration_seconds
            }
        )
    )
    if len(beats_ms) < 2:
        raise AnalysisError("beat analysis requires at least two usable beats")
    cut_points = [0]
    for beat_ms in beats_ms:
        if beat_ms - cut_points[-1] >= minimum_cut_ms:
            cut_points.append(beat_ms)
    if len(cut_points) < 3:
        raise AnalysisError("beat analysis requires at least two usable cut beats")
    if duration_ms - cut_points[-1] < minimum_cut_ms:
        if len(cut_points) == 1:
            raise AnalysisError("audio has no usable cut interval")
        cut_points[-1] = duration_ms
    else:
        cut_points.append(duration_ms)
    if len(cut_points) < 4:
        raise AnalysisError("beat analysis requires at least two internal cut points")
    return BeatAnalysis(
        duration_ms,
        bpm,
        beats_ms,
        tuple(cut_points),
        minimum_cut_ms,
    )


class LibrosaBeatAnalyzer:
    def __init__(self, librosa_module=None, numpy_module=None):
        self._librosa = librosa_module
        self._numpy = numpy_module

    def analyze(self, path: str | Path) -> BeatAnalysis:
        try:
            if self._librosa is None:
                import librosa

                self._librosa = librosa
            if self._numpy is None:
                import numpy

                self._numpy = numpy
            y, sample_rate = self._librosa.load(str(Path(path)), sr=None, mono=True)
            duration = self._librosa.get_duration(y=y, sr=sample_rate)
            tempo, beats = self._librosa.beat.beat_track(
                y=y,
                sr=sample_rate,
                units="time",
            )
            bpm = float(self._numpy.atleast_1d(tempo)[0])
            return normalize_analysis(duration, bpm, (float(value) for value in beats))
        except AnalysisError:
            raise
        except Exception as exc:
            raise AnalysisError(f"cannot analyze BGM: {exc}") from exc
