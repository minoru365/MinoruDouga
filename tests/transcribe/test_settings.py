import json

from minoru_studio.transcribe.settings import load_settings, save_settings


def test_transcribe_settings_round_trip_only_allowed_keys(tmp_path):
    path = tmp_path / "config.json"
    save_settings(
        {
            "input": "video.mp4",
            "name": "demo",
            "output_dir": "jobs",
            "model": "small",
            "language": "ja",
            "normalize": True,
            "denoise": False,
            "preview": True,
            "allow_model_download": True,
            "authorization": "never-save",
            "transcript": "never-save",
            "cache_dir": "never-save",
        },
        path,
    )

    assert load_settings(path) == {
        "input": "video.mp4",
        "name": "demo",
        "output_dir": "jobs",
        "model": "small",
        "language": "ja",
        "normalize": True,
        "denoise": False,
        "preview": True,
    }
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "beat_sync": {},
        "transcribe": load_settings(path)
    }


def test_transcribe_save_preserves_latest_beat_sync_section(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"beat_sync": {"music": "song.wav", "secret": "drop"}}),
        encoding="utf-8",
    )

    save_settings({"model": "medium", "authorization": "drop"}, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "beat_sync": {"music": "song.wav", "secret": "drop"},
        "transcribe": {"model": "medium"},
    }


def test_transcribe_save_preserves_script_draft_and_unknown_sections(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"script_draft": {"name": "draft"}, "other": {"keep": True}}),
        encoding="utf-8",
    )

    save_settings({"model": "medium"}, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "script_draft": {"name": "draft"},
        "transcribe": {"model": "medium"},
        "other": {"keep": True},
    }
