from pathlib import Path

import pytest

from minoru_studio.transcribe.gui_state import TranscribeFormValues, model_prompt
from minoru_studio.transcribe.media import MediaInfo


def test_form_values_build_a_transcribe_request():
    values = TranscribeFormValues(
        input_path="input.mp4",
        name="demo",
        output_dir="jobs",
        model="small",
        language="ja",
        normalize=True,
        denoise=False,
        preview=False,
    )

    assert values.to_request().input_path == Path("input.mp4")
    assert values.to_request().output_dir == Path("jobs")
    assert values.to_request().normalize is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_path", " "),
        ("name", " "),
        ("output_dir", " "),
        ("model", "large"),
        ("language", "en"),
    ],
)
def test_form_values_reject_invalid_required_fields(field, value):
    data = dict(
        input_path="input.mp4", name="demo", output_dir="jobs", model="small",
        language="ja", normalize=False, denoise=False, preview=False,
    )
    data[field] = value

    with pytest.raises(ValueError):
        TranscribeFormValues(**data).to_request()


def test_preview_rejects_a_probed_audio_only_input():
    values = TranscribeFormValues(
        "input.m4a", "demo", "jobs", "small", "ja", False, False, True
    )

    with pytest.raises(ValueError, match="video"):
        values.to_request(media_info=MediaInfo(1_000, True, False))


def test_model_prompt_contains_capacity_and_no_authorization_state(tmp_path):
    prompt = model_prompt(
        "small",
        cache_dir=tmp_path / "models",
        model_complete=lambda cache, model: False,
        require_capacity=lambda cache, model: 2_000_000_000,
    )

    assert prompt.cached is False
    assert prompt.model == "small"
    assert prompt.estimated_download_bytes == 500_000_000
    assert prompt.required_free_bytes == 1_000_000_000
    assert prompt.free_bytes == 2_000_000_000
    assert prompt.cache_dir == str(tmp_path / "models")
    assert "authorization" not in prompt.__dataclass_fields__
