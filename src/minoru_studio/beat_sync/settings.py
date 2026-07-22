from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


ALLOWED_KEYS = {
    "music",
    "media_dir",
    "every_n",
    "order",
    "timeline_name",
    "name",
    "output_dir",
}
_LEGACY_KEYS = ALLOWED_KEYS


def default_settings_path():
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return root / "MinoruStudio" / "config.json"


def load_settings(path=None):
    payload = _read(path)
    section = payload.get("beat_sync")
    if isinstance(section, dict):
        return _allowed(section)
    return _allowed(payload)


def save_settings(values, path=None):
    target = Path(path) if path else default_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    latest = _read(target)
    if not isinstance(latest.get("beat_sync"), dict):
        for key in _LEGACY_KEYS:
            latest.pop(key, None)
    latest["beat_sync"] = _allowed(values)
    _write(target, latest)


def _allowed(values):
    return {key: values[key] for key in ALLOWED_KEYS if key in values}


def _read(path):
    target = Path(path) if path else default_settings_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(target, payload):
    temporary = target.parent / f".config-{uuid4()}.tmp"
    try:
        temporary.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
