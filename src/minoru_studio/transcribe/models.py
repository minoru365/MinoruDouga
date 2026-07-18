from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    estimated_download_bytes: int
    required_free_bytes: int


MODEL_SPECS = {
    "small": ModelSpec("small", 500_000_000, 1_000_000_000),
    "medium": ModelSpec("medium", 1_500_000_000, 3_000_000_000),
}
REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json")


class ModelCapacityError(RuntimeError):
    pass


def default_model_cache_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data is None:
        local_app_data = str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "MinoruStudio" / "models" / "faster-whisper"


def model_directory(cache_dir: str | Path, model: str) -> Path:
    _model_spec(model)
    return Path(cache_dir) / model


def model_is_complete(cache_dir: str | Path, model: str) -> bool:
    directory = model_directory(cache_dir, model)
    return directory.is_dir() and all((directory / name).is_file() for name in REQUIRED_MODEL_FILES)


def require_model_capacity(cache_dir: str | Path, model: str) -> int:
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(directory).free
    spec = _model_spec(model)
    if free_bytes < spec.required_free_bytes:
        raise ModelCapacityError("insufficient free space for model download")
    return free_bytes


def _model_spec(model: str) -> ModelSpec:
    try:
        return MODEL_SPECS[model]
    except KeyError as exc:
        raise ValueError(f"unsupported model: {model}") from exc
