from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


WORKER_SCHEMA_VERSION = 1

_MODELS = frozenset(("small", "medium"))
_DEVICES = frozenset(("cpu",))
_COMPUTE_TYPES = frozenset(("int8",))


class ContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    schema_version: int
    input_wav: str
    output_json: str
    model: str
    language: str
    device: str
    compute_type: str
    vad_filter: bool
    word_timestamps: bool
    model_cache_dir: str
    allow_model_download: bool


@dataclass(frozen=True, slots=True)
class WordResult:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class SegmentResult:
    start_ms: int
    end_ms: int
    text: str
    words: tuple[WordResult, ...]


@dataclass(frozen=True, slots=True)
class WorkerResult:
    schema_version: int
    model: str
    provider_version: str
    language: str
    language_probability: float
    duration_ms: int
    duration_after_vad_ms: int
    no_speech: bool
    segments: tuple[SegmentResult, ...]


def save_worker_request(path: str | Path, request: WorkerRequest) -> None:
    _validate_worker_request(request)
    _save_json(path, asdict(request))


def load_worker_request(path: str | Path) -> WorkerRequest:
    data = _load_json(path)
    _require_fields(data, WorkerRequest, "worker request")
    request = WorkerRequest(**data)
    _validate_worker_request(request)
    return request


def save_worker_result(path: str | Path, result: WorkerResult) -> None:
    _validate_worker_result(result)
    _save_json(path, asdict(result))


def load_worker_result(path: str | Path) -> WorkerResult:
    data = _load_json(path)
    _require_fields(data, WorkerResult, "worker result")
    try:
        segments = tuple(_parse_segment(value) for value in data["segments"])
        result = WorkerResult(
            schema_version=data["schema_version"],
            model=data["model"],
            provider_version=data["provider_version"],
            language=data["language"],
            language_probability=data["language_probability"],
            duration_ms=data["duration_ms"],
            duration_after_vad_ms=data["duration_after_vad_ms"],
            no_speech=data["no_speech"],
            segments=segments,
        )
    except (KeyError, TypeError) as exc:
        raise ContractError("invalid worker result") from exc
    _validate_worker_result(result)
    return result


def _parse_segment(value: Any) -> SegmentResult:
    if not isinstance(value, Mapping):
        raise ContractError("segment must be an object")
    _require_fields(value, SegmentResult, "segment")
    words_value = value["words"]
    if not isinstance(words_value, list):
        raise ContractError("segment words must be an array")
    words = tuple(_parse_word(word) for word in words_value)
    return SegmentResult(
        start_ms=value["start_ms"],
        end_ms=value["end_ms"],
        text=value["text"],
        words=words,
    )


def _parse_word(value: Any) -> WordResult:
    if not isinstance(value, Mapping):
        raise ContractError("word must be an object")
    _require_fields(value, WordResult, "word")
    return WordResult(
        start_ms=value["start_ms"],
        end_ms=value["end_ms"],
        text=value["text"],
    )


def _validate_worker_request(request: WorkerRequest) -> None:
    if not isinstance(request, WorkerRequest):
        raise ContractError("worker request must be a WorkerRequest")
    _validate_schema_version(request.schema_version)
    for name in ("input_wav", "output_json", "model_cache_dir"):
        _validate_absolute_path(name, getattr(request, name))
    _validate_choice("model", request.model, _MODELS)
    _validate_nonblank_string("language", request.language)
    _validate_choice("device", request.device, _DEVICES)
    _validate_choice("compute_type", request.compute_type, _COMPUTE_TYPES)
    for name in ("vad_filter", "word_timestamps", "allow_model_download"):
        _validate_bool(name, getattr(request, name))


def _validate_worker_result(result: WorkerResult) -> None:
    if not isinstance(result, WorkerResult):
        raise ContractError("worker result must be a WorkerResult")
    _validate_schema_version(result.schema_version)
    _validate_choice("model", result.model, _MODELS)
    _validate_nonblank_string("provider_version", result.provider_version)
    _validate_nonblank_string("language", result.language)
    _validate_probability(result.language_probability)
    _validate_milliseconds("duration_ms", result.duration_ms)
    _validate_milliseconds("duration_after_vad_ms", result.duration_after_vad_ms)
    if result.duration_after_vad_ms > result.duration_ms:
        raise ContractError("duration_after_vad_ms must not exceed duration_ms")
    _validate_bool("no_speech", result.no_speech)
    if not isinstance(result.segments, tuple):
        raise ContractError("segments must be a tuple")
    if result.no_speech and result.segments:
        raise ContractError("no_speech results cannot contain segments")

    previous_segment_end = 0
    for segment in result.segments:
        _validate_segment(segment, result.duration_ms, previous_segment_end)
        previous_segment_end = segment.end_ms


def _validate_segment(segment: SegmentResult, duration_ms: int, previous_end: int) -> None:
    if not isinstance(segment, SegmentResult):
        raise ContractError("segment must be a SegmentResult")
    _validate_time_range("segment", segment.start_ms, segment.end_ms, duration_ms)
    if segment.start_ms < previous_end:
        raise ContractError("segments must be ordered and non-overlapping")
    _validate_nonblank_string("segment text", segment.text)
    if not isinstance(segment.words, tuple):
        raise ContractError("segment words must be a tuple")

    previous_word_end = segment.start_ms
    for word in segment.words:
        if not isinstance(word, WordResult):
            raise ContractError("word must be a WordResult")
        _validate_time_range("word", word.start_ms, word.end_ms, duration_ms)
        if word.start_ms < segment.start_ms or word.end_ms > segment.end_ms:
            raise ContractError("word times must be within their segment")
        if word.start_ms < previous_word_end:
            raise ContractError("words must be ordered and non-overlapping")
        _validate_nonblank_string("word text", word.text)
        previous_word_end = word.end_ms


def _validate_schema_version(value: Any) -> None:
    if not _is_integer(value) or value != WORKER_SCHEMA_VERSION:
        raise ContractError(f"unsupported worker schema version: {value!r}")


def _validate_absolute_path(name: str, value: Any) -> None:
    _validate_nonblank_string(name, value)
    if not Path(value).is_absolute():
        raise ContractError(f"{name} must be an absolute path")


def _validate_choice(name: str, value: Any, allowed: frozenset[str]) -> None:
    _validate_nonblank_string(name, value)
    if value not in allowed:
        raise ContractError(f"unsupported {name}: {value}")


def _validate_nonblank_string(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a non-blank string")


def _validate_bool(name: str, value: Any) -> None:
    if type(value) is not bool:
        raise ContractError(f"{name} must be a boolean")


def _validate_probability(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("language_probability must be a finite number")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ContractError("language_probability must be between zero and one")


def _validate_milliseconds(name: str, value: Any) -> None:
    if not _is_integer(value) or value < 0:
        raise ContractError(f"{name} must be a non-negative integer millisecond")


def _validate_time_range(name: str, start_ms: Any, end_ms: Any, duration_ms: int) -> None:
    _validate_milliseconds(f"{name} start_ms", start_ms)
    _validate_milliseconds(f"{name} end_ms", end_ms)
    if end_ms < start_ms:
        raise ContractError(f"{name} end_ms must not precede start_ms")
    if end_ms > duration_ms:
        raise ContractError(f"{name} times must not exceed duration_ms")


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_fields(data: Any, cls: type[Any], label: str) -> None:
    if not isinstance(data, Mapping):
        raise ContractError(f"{label} must be an object")
    expected = {field.name for field in fields(cls)}
    actual = set(data)
    if actual != expected:
        raise ContractError(f"invalid {label} fields")


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ContractError("invalid worker JSON") from exc
    if not isinstance(data, dict):
        raise ContractError("worker JSON must be an object")
    return data


def _save_json(path: str | Path, data: Mapping[str, Any]) -> None:
    destination = Path(path)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
