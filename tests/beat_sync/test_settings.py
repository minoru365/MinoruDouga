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
