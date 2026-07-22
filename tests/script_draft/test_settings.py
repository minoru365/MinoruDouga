import json

from minoru_studio.script_draft.settings import load_settings, save_settings


def test_script_draft_settings_only_save_form_fields(tmp_path):
    path = tmp_path / "config.json"

    save_settings({"input": "input.mp4", "name": "demo", "output_dir": "jobs", "secret": "no"}, path)

    assert load_settings(path) == {"input": "input.mp4", "name": "demo", "output_dir": "jobs"}


def test_script_draft_save_preserves_other_sections_and_unknown_top_level_data(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"beat_sync": {"music": "song.wav"}, "transcribe": {"model": "small"}, "other": {"keep": True}}),
        encoding="utf-8",
    )

    save_settings({"name": "draft"}, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "beat_sync": {"music": "song.wav"},
        "transcribe": {"model": "small"},
        "script_draft": {"name": "draft"},
        "other": {"keep": True},
    }
