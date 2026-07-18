from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import shutil

from minoru_studio.transcribe.media import MediaInfo
from minoru_studio.transcribe.models import (
    MODEL_SPECS,
    default_model_cache_dir,
    model_is_complete,
    require_model_capacity,
)
from minoru_studio.transcribe.service import TranscribeRequest


@dataclass(frozen=True, slots=True)
class TranscribeFormValues:
    input_path: str
    name: str
    output_dir: str
    model: str
    language: str
    normalize: bool
    denoise: bool
    preview: bool

    def to_request(
        self,
        *,
        media_info: MediaInfo | None = None,
    ) -> TranscribeRequest:
        input_path = _required(self.input_path, "input")
        name = _required(self.name, "name")
        output_dir = _required(self.output_dir, "output directory")
        if self.model not in MODEL_SPECS:
            raise ValueError("unsupported model")
        language = normalize_language(self.language)
        if any(type(value) is not bool for value in (self.normalize, self.denoise, self.preview)):
            raise ValueError("transcription options must be booleans")
        source = Path(input_path)
        if self.preview and media_info is not None and not media_info.has_video:
            raise ValueError("preview requires a video input")
        return TranscribeRequest(
            input_path=source,
            name=name,
            output_dir=Path(output_dir),
            model=self.model,
            language=language,
            normalize=self.normalize,
            denoise=self.denoise,
            preview=self.preview,
        )


@dataclass(frozen=True, slots=True)
class ModelPrompt:
    cached: bool
    model: str
    estimated_download_bytes: int
    required_free_bytes: int
    free_bytes: int
    cache_dir: str


def model_prompt(
    model: str,
    *,
    cache_dir: Path | None = None,
    model_complete: Callable[[Path, str], bool] = model_is_complete,
    require_capacity: Callable[[Path, str], int] = require_model_capacity,
) -> ModelPrompt:
    try:
        spec = MODEL_SPECS[model]
    except KeyError as exc:
        raise ValueError("unsupported model") from exc
    cache = Path(cache_dir) if cache_dir is not None else default_model_cache_dir()
    cached = model_complete(cache, model)
    free_bytes = (
        shutil.disk_usage(cache).free
        if cached
        else require_capacity(cache, model)
    )
    return ModelPrompt(
        cached=cached,
        model=spec.name,
        estimated_download_bytes=spec.estimated_download_bytes,
        required_free_bytes=spec.required_free_bytes,
        free_bytes=free_bytes,
        cache_dir=str(cache),
    )


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value.strip()


def normalize_language(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("unsupported language")
    language = value.strip().casefold()
    if language == "auto":
        return language
    if len(language) in (2, 3) and language.isascii() and language.isalpha():
        return language
    raise ValueError("unsupported language")
