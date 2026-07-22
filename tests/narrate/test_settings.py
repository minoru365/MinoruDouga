import json

from minoru_studio.narrate.settings import load_settings, save_settings


def test_narrate_settings_save_only_form_fields(tmp_path):
    path = tmp_path / "config.json"

    save_settings({"input": "input.mp4", "script": "script.md", "name": "demo", "output_dir": "jobs", "preview": True, "secret": "no"}, path)

    assert load_settings(path) == {"input": "input.mp4", "script": "script.md", "name": "demo", "output_dir": "jobs", "preview": True}


def test_narrate_settings_preserve_other_config_sections(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"beat_sync": {"music": "song.wav"}, "transcribe": {"model": "small"}, "other": {"keep": True}}), encoding="utf-8")

    save_settings({"name": "narration"}, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "beat_sync": {"music": "song.wav"},
        "transcribe": {"model": "small"},
        "narrate": {"name": "narration"},
        "other": {"keep": True},
    }
