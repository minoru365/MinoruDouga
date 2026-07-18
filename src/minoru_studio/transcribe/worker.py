from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Callable, Iterable, Sequence
from uuid import uuid4

from minoru_studio.timebase import seconds_to_milliseconds
from minoru_studio.transcribe.contracts import (
    SegmentResult,
    WordResult,
    WorkerRequest,
    WorkerResult,
    load_worker_request,
    save_worker_result,
)
from minoru_studio.transcribe.models import (
    REQUIRED_MODEL_FILES,
    model_directory,
    model_is_complete,
    require_model_capacity,
)


class ModelNotCachedError(RuntimeError):
    pass


class ModelDownloadError(RuntimeError):
    pass


def run_worker(
    request_path: str | Path,
    *,
    model_factory: Callable[..., object] | None = None,
    download_model_fn: Callable[..., object] | None = None,
) -> WorkerResult:
    request = load_worker_request(request_path)
    model_dir = _ensure_cached_model(request, download_model_fn=download_model_fn)
    factory = model_factory if model_factory is not None else _whisper_model_factory()
    model = factory(str(model_dir), device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        request.input_wav,
        language=None if request.language == "auto" else request.language,
        vad_filter=True,
        word_timestamps=True,
        beam_size=5,
    )
    result = _worker_result(request, info, segments)
    save_worker_result(request.output_json, result)
    return result


def _ensure_cached_model(
    request: WorkerRequest,
    *,
    download_model_fn: Callable[..., object] | None,
) -> Path:
    cache_dir = Path(request.model_cache_dir)
    final_dir = model_directory(cache_dir, request.model)
    if model_is_complete(cache_dir, request.model):
        return final_dir
    if not request.allow_model_download:
        raise ModelNotCachedError("model is not cached")

    require_model_capacity(cache_dir, request.model)
    staging = cache_dir / f".{request.model}.partial-{uuid4().hex}"
    downloader = download_model_fn if download_model_fn is not None else _download_model
    try:
        downloader(request.model, output_dir=str(staging))
    except Exception as exc:
        raise ModelDownloadError("model download failed") from exc
    if not _directory_is_complete(staging):
        raise ModelDownloadError("model download failed")
    if model_is_complete(cache_dir, request.model):
        return final_dir
    try:
        os.replace(staging, final_dir)
    except OSError as exc:
        if model_is_complete(cache_dir, request.model):
            return final_dir
        raise ModelDownloadError("model download failed") from exc
    return final_dir


def _directory_is_complete(directory: Path) -> bool:
    return directory.is_dir() and all((directory / name).is_file() for name in REQUIRED_MODEL_FILES)


def _whisper_model_factory() -> Callable[..., object]:
    from faster_whisper import WhisperModel

    return WhisperModel


def _download_model(model: str, *, output_dir: str) -> object:
    from faster_whisper.utils import download_model

    return download_model(model, output_dir=output_dir)


def _provider_version() -> str:
    from importlib.metadata import version

    return version("faster-whisper")


def _worker_result(
    request: WorkerRequest,
    info: object,
    provider_segments: Iterable[object],
) -> WorkerResult:
    segments = tuple(_segment_result(segment) for segment in provider_segments)
    return WorkerResult(
        schema_version=1,
        model=request.model,
        provider_version=_provider_version(),
        language=str(info.language),
        language_probability=float(info.language_probability),
        duration_ms=seconds_to_milliseconds(info.duration),
        duration_after_vad_ms=seconds_to_milliseconds(info.duration_after_vad),
        no_speech=not segments,
        segments=segments,
    )


def _segment_result(segment: object) -> SegmentResult:
    words = getattr(segment, "words", None) or ()
    return SegmentResult(
        start_ms=seconds_to_milliseconds(segment.start),
        end_ms=seconds_to_milliseconds(segment.end),
        text=str(segment.text),
        words=tuple(
            WordResult(
                start_ms=seconds_to_milliseconds(word.start),
                end_ms=seconds_to_milliseconds(word.end),
                text=str(word.word),
            )
            for word in words
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else list(argv)
    if len(arguments) != 1:
        print("inference worker failed", file=sys.stderr)
        return 1
    try:
        run_worker(arguments[0])
    except ModelNotCachedError:
        print("model is not cached", file=sys.stderr)
        return 1
    except ModelDownloadError:
        print("model download failed", file=sys.stderr)
        return 1
    except Exception:
        print("inference worker failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
