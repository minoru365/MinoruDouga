from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from minoru_studio.narrate.models import (
    DurationWarning,
    NarrateRequest,
    TimedUtterance,
    Utterance,
    VideoInfo,
    VoicevoxProvenance,
    WavInfo,
)


def test_narrate_request_is_immutable_and_accepts_valid_values(tmp_path: Path):
    request = NarrateRequest(
        input_path=tmp_path / "input.mp4",
        script_path=tmp_path / "script.txt",
        name="Narration",
        output_dir=tmp_path / "output",
    )

    assert request.preview is False
    with pytest.raises(FrozenInstanceError):
        request.name = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("input_path", "input.mp4"),
        ("script_path", "script.txt"),
        ("output_dir", "output"),
        ("name", " "),
        ("preview", 1),
    ],
)
def test_narrate_request_rejects_invalid_field_types(
    tmp_path: Path, field: str, value: object,
):
    values: dict[str, object] = {
        "input_path": tmp_path / "input.mp4",
        "script_path": tmp_path / "script.txt",
        "name": "Narration",
        "output_dir": tmp_path / "output",
        "preview": False,
    }
    values[field] = value

    with pytest.raises(ValueError):
        NarrateRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["duration_ms", "width", "height"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_video_info_requires_strict_positive_integers(field: str, value: object):
    values: dict[str, object] = {"duration_ms": 1000, "width": 1920, "height": 1080}
    values[field] = value

    with pytest.raises(ValueError):
        VideoInfo(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("index", [0, -1, True, 1.5])
def test_utterance_requires_one_based_integer_index(index: object):
    with pytest.raises(ValueError):
        Utterance(index=index, text="Hello")  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["", " ", 1])
def test_utterance_requires_nonblank_text(text: object):
    with pytest.raises(ValueError):
        Utterance(index=1, text=text)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["sample_rate", "channels", "sample_width"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_wav_info_requires_strictly_positive_shape(field: str, value: object):
    values: dict[str, object] = {
        "duration_seconds": Decimal("1.25"),
        "sample_rate": 24000,
        "channels": 1,
        "sample_width": 2,
    }
    values[field] = value

    with pytest.raises(ValueError):
        WavInfo(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "duration", [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), 1.0],
)
def test_wav_info_requires_positive_finite_decimal_duration(duration: object):
    with pytest.raises(ValueError):
        WavInfo(duration, 24000, 1, 2)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("start_ms", "end_ms", "wav_path"),
    [(-1, 100, "audio/001.wav"), (100, 100, "audio/001.wav"), (100, 99, "audio/001.wav"),
     (True, 100, "audio/001.wav"), (0, 100, "/audio/001.wav"),
     (0, 100, "audio\\001.wav"), (0, 100, "../audio/001.wav")],
)
def test_timed_utterance_requires_valid_range_and_relative_posix_path(
    start_ms: object, end_ms: object, wav_path: object,
):
    with pytest.raises(ValueError):
        TimedUtterance(1, "Hello", start_ms, end_ms, wav_path)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("engine_version", "speaker_name", "style_name", "speaker_id"),
    [("", "Speaker", "Style", 1), ("1.0", " ", "Style", 1),
     ("1.0", "Speaker", 1, 1), ("1.0", "Speaker", "Style", 0),
     ("1.0", "Speaker", "Style", True)],
)
def test_voicevox_provenance_requires_exact_valid_field_types(
    engine_version: object, speaker_name: object, style_name: object, speaker_id: object,
):
    with pytest.raises(ValueError):
        VoicevoxProvenance(engine_version, speaker_name, style_name, speaker_id)  # type: ignore[arg-type]


@pytest.mark.parametrize("values", [(0, 1), (1, 0), (True, 1), (1, True), (1.0, 1)])
def test_duration_warning_requires_strict_positive_integer_durations(values: tuple[object, object]):
    with pytest.raises(ValueError):
        DurationWarning(*values)  # type: ignore[arg-type]
