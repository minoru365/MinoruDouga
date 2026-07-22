from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


ALLOWED_KEYS = {"input", "name", "output_dir"}


def default_settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return root / "MinoruStudio" / "config.json"


def load_settings(path: str | Path | None = None) -> dict[str, object]:
    section = _read(path).get("script_draft")
    return _allowed(section) if isinstance(section, dict) else {}


def save_settings(values: dict[str, object], path: str | Path | None = None) -> None:
    target = Path(path) if path else default_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _read(target)
    payload["script_draft"] = _allowed(values)
    _write(target, payload)


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
