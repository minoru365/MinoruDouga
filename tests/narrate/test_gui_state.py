from pathlib import Path

import pytest

from minoru_studio.narrate.gui_state import NarrateFormValues


def test_form_values_trim_fields_and_build_request():
    request = NarrateFormValues(" input.mp4 ", " script.md ", " demo ", " jobs ", True).to_request()

    assert request.input_path == Path("input.mp4")
    assert request.script_path == Path("script.md")
    assert request.name == "demo"
    assert request.output_dir == Path("jobs")
    assert request.preview is True


@pytest.mark.parametrize("field", ("input_path", "script_path", "name", "output_dir"))
def test_form_values_reject_blank_required_fields(field):
    values = {"input_path": "input.mp4", "script_path": "script.md", "name": "demo", "output_dir": "jobs", "preview": False}
    values[field] = " "

    with pytest.raises(ValueError):
        NarrateFormValues(**values).to_request()


@pytest.mark.parametrize("preview", (0, 1, "true", None))
def test_form_values_require_a_real_boolean_preview(preview):
    with pytest.raises(ValueError):
        NarrateFormValues("input.mp4", "script.md", "demo", "jobs", preview).to_request()
