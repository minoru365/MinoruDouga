import json

from minoru_studio.beat_sync.settings import load_settings, save_settings


def test_settings_round_trip_only_allowed_keys(tmp_path):
    path = tmp_path / "config.json"
    save_settings(
        {
            "music": "song.wav",
            "media_dir": "media",
            "every_n": "auto",
            "order": "asc",
            "timeline_name": "Demo",
            "secret": "must-not-persist",
        },
        path,
    )
    assert load_settings(path) == {
        "music": "song.wav",
        "media_dir": "media",
        "every_n": "auto",
        "order": "asc",
        "timeline_name": "Demo",
    }


def test_legacy_flat_beat_sync_settings_load_and_migrate_without_losing_transcribe(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "music": "legacy.wav",
                "name": "legacy",
                "transcribe": {"model": "medium", "authorization": "drop"},
            }
        ),
        encoding="utf-8",
    )

    assert load_settings(path) == {"music": "legacy.wav", "name": "legacy"}
    save_settings({"music": "new.wav", "secret": "drop"}, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "beat_sync": {"music": "new.wav"},
        "transcribe": {"model": "medium", "authorization": "drop"},
    }
