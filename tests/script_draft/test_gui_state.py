from pathlib import Path

import pytest

from minoru_studio.script_draft.gui_state import ScriptDraftFormValues


def test_form_values_strip_values_and_build_request():
    request = ScriptDraftFormValues(" input.mp4 ", " demo ", " jobs ").to_request()

    assert request.input_path == Path("input.mp4")
    assert request.name == "demo"
    assert request.output_dir == Path("jobs")


@pytest.mark.parametrize("field", ("input_path", "name", "output_dir"))
def test_form_values_reject_blank_required_fields(field):
    values = {"input_path": "input.mp4", "name": "demo", "output_dir": "jobs"}
    values[field] = " "

    with pytest.raises(ValueError):
        ScriptDraftFormValues(**values).to_request()
