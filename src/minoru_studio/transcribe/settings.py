from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


ALLOWED_KEYS = {
    "input",
    "name",
    "output_dir",
    "model",
    "language",
    "normalize",
    "denoise",
    "preview",
}


def default_settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return root / "MinoruStudio" / "config.json"


def load_settings(path: str | Path | None = None) -> dict[str, object]:
    payload = _read(path)
    section = payload.get("transcribe")
    if not isinstance(section, dict):
        return {}
    return _allowed(section)


def save_settings(values: dict[str, object], path: str | Path | None = None) -> None:
    target = Path(path) if path else default_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    latest = _read(target)
    beat_sync = latest.get("beat_sync")
    if not isinstance(beat_sync, dict):
        beat_sync = {
            key: latest[key]
            for key in _BEAT_SYNC_KEYS
            if key in latest
        }
    payload = {
        "beat_sync": beat_sync,
        "transcribe": _allowed(values),
    }
    _write(target, payload)


_BEAT_SYNC_KEYS = {
    "music",
    "media_dir",
    "every_n",
    "order",
    "timeline_name",
    "name",
    "output_dir",
}


def _allowed(values: dict[str, object]) -> dict[str, object]:
    return {key: values[key] for key in ALLOWED_KEYS if key in values}


def _read(path: str | Path | None) -> dict[str, object]:
    target = Path(path) if path else default_settings_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(target: Path, payload: dict[str, object]) -> None:
    temporary = target.parent / f".config-{uuid4()}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
