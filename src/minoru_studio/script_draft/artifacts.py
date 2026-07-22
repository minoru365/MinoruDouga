from __future__ import annotations

import json
import os
import shutil
import struct
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from minoru_studio.jobs.store import JobStore, fingerprint_artifact
from minoru_studio.script_draft.models import (
    FRAME_FORMAT,
    INTERVAL_MS,
    MAX_FRAME_EDGE,
    MERGE_TOLERANCE_MS,
    SCENE_THRESHOLD,
    FrameCandidate,
    FrameIndexEntry,
    VideoInfo,
)


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SCHEMA_VERSION = 1
_REASON_ORDER = ("scene", "interval")


def merge_candidates(
    candidates: Sequence[FrameCandidate],
) -> list[tuple[FrameCandidate, tuple[str, ...]]]:
    merged: list[tuple[FrameCandidate, tuple[str, ...], int]] = []
    for candidate in sorted(candidates, key=lambda item: (item.time_ms, item.reason)):
        if not merged or candidate.time_ms - merged[-1][2] > MERGE_TOLERANCE_MS:
            merged.append((candidate, (candidate.reason,), candidate.time_ms))
            continue
        previous, reasons, _ = merged[-1]
        selected = candidate if candidate.reason == "scene" else previous
        reason_set = set(reasons)
        reason_set.add(candidate.reason)
        merged[-1] = (
            selected,
            tuple(reason for reason in _REASON_ORDER if reason in reason_set),
            candidate.time_ms,
        )
    return [(candidate, reasons) for candidate, reasons, _ in merged]


def render_artifacts(
    outputs_dir: Path,
    info: VideoInfo,
    merged: Sequence[tuple[FrameCandidate, tuple[str, ...]]],
) -> list[FrameIndexEntry]:
    outputs = Path(outputs_dir)
    frames_dir = outputs / "frames"
    job_root = outputs.resolve().parent
    entries: list[FrameIndexEntry] = []
    for index, (candidate, reasons) in enumerate(merged, start=1):
        destination = frames_dir / f"frame-{index:04d}.{FRAME_FORMAT}"
        source = _validated_work_source(candidate.source_path, job_root)
        _validate_png_frame(source)
        normalized_reasons = _validated_reasons(reasons)
        entries.append(FrameIndexEntry(
            index=index,
            time_ms=candidate.time_ms,
            image_path=f"frames/{destination.name}",
            reasons=normalized_reasons,
        ))

    index_path = outputs / "frame-index.json"
    script_path = outputs / "script.md"
    destinations = [frames_dir / Path(entry.image_path).name for entry in entries]
    destinations.extend((index_path, script_path))
    if any(destination.exists() for destination in destinations):
        raise ValueError("final artifact already exists")

    frames_dir.mkdir(parents=True, exist_ok=True)
    temporary_paths: list[Path] = []
    published: list[tuple[Path, Path]] = []
    try:
        for entry, (candidate, _) in zip(entries, merged, strict=True):
            source = _validated_work_source(candidate.source_path, job_root)
            destination = frames_dir / Path(entry.image_path).name
            temporary = _stage_copy(source, destination)
            temporary_paths.append(temporary)
            _publish_create_only(temporary, destination)
            published.append((temporary, destination))
        index_temporary = _stage_text(index_path, _json_text(_index_payload(info, entries)))
        temporary_paths.append(index_temporary)
        _publish_create_only(index_temporary, index_path)
        published.append((index_temporary, index_path))
        script_temporary = _stage_text(script_path, _script_text(info, entries))
        temporary_paths.append(script_temporary)
        _publish_create_only(script_temporary, script_path)
        published.append((script_temporary, script_path))
    except BaseException:
        _rollback_published(published)
        raise
    finally:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)
    return entries


def artifacts_valid(job_dir: Path) -> bool:
    try:
        root = Path(job_dir).resolve(strict=True)
        outputs = (root / "outputs").resolve(strict=True)
        frames_dir = (outputs / "frames").resolve(strict=True)
        index_path = (outputs / "frame-index.json").resolve(strict=True)
        script_path = (outputs / "script.md").resolve(strict=True)
        if any(not path.is_relative_to(root) for path in (outputs, frames_dir, index_path, script_path)):
            return False
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        entries = _validated_index(payload, frames_dir)
        script = script_path.read_text(encoding="utf-8")
        expected_paths = [frames_dir / entry.image_path.removeprefix("frames/") for entry in entries]
        expected_paths.extend((index_path, script_path))
        expected_frame_paths = {path.relative_to(root).as_posix() for path in expected_paths[:-2]}
        actual_frame_paths = {
            path.resolve(strict=True).relative_to(root).as_posix()
            for path in frames_dir.glob(f"*.{FRAME_FORMAT}")
        }
        if actual_frame_paths != expected_frame_paths:
            return False
        if any(entry.image_path not in script for entry in entries):
            return False
        manifest = JobStore().load(root, recover_interrupted=False)
        expected_record_paths = {path.relative_to(root).as_posix() for path in expected_paths}
        if (
            len(manifest.artifacts) != len(expected_record_paths)
            or {record.path for record in manifest.artifacts} != expected_record_paths
        ):
            return False
        for path in expected_paths:
            matching = [record for record in manifest.artifacts if record.path == path.relative_to(root).as_posix()]
            if len(matching) != 1:
                return False
            if fingerprint_artifact(root, path, matching[0].kind) != matching[0]:
                return False
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return True


def _stage_copy(source: Path, destination: Path) -> Path:
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _stage_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _publish_create_only(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise ValueError("final artifact already exists") from None


def _rollback_published(published: Sequence[tuple[Path, Path]]) -> None:
    for temporary, destination in reversed(published):
        try:
            if temporary.exists() and destination.exists() and os.path.samefile(temporary, destination):
                destination.unlink()
        except OSError:
            continue


def _index_payload(info: VideoInfo, entries: Sequence[FrameIndexEntry]) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "duration_ms": info.duration_ms,
        "width": info.width,
        "height": info.height,
        "settings": {
            "scene_threshold": SCENE_THRESHOLD,
            "interval_ms": INTERVAL_MS,
            "merge_tolerance_ms": MERGE_TOLERANCE_MS,
            "max_frame_edge": MAX_FRAME_EDGE,
            "frame_format": FRAME_FORMAT,
        },
        "entries": [
            {
                "index": entry.index,
                "time_ms": entry.time_ms,
                "image_path": entry.image_path,
                "reasons": list(entry.reasons),
            }
            for entry in entries
        ],
    }


def _script_text(info: VideoInfo, entries: Sequence[FrameIndexEntry]) -> str:
    parts = [
        "# Script Draft\n\n",
        "| Source duration | Resolution |\n",
        "| --- | --- |\n",
        f"| {_format_time(info.duration_ms)} | {info.width} × {info.height} |\n\n",
        "This is a human-editable draft.\n",
    ]
    for entry in entries:
        parts.extend((
            f"\n## {_format_time(entry.time_ms)} — Frame {entry.index:04d}\n\n",
            f"![Frame {entry.index:04d}]({entry.image_path})\n\n",
            "### 画面の説明\n\n",
            "### 操作\n\n",
            "### ナレーション\n",
        ))
    return "".join(parts)


def _format_time(time_ms: int) -> str:
    milliseconds = time_ms % 1_000
    seconds = time_ms // 1_000
    hours, seconds = divmod(seconds, 3_600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _validated_index(payload: object, frames_dir: Path) -> list[FrameIndexEntry]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "duration_ms", "width", "height", "settings", "entries",
    }:
        raise ValueError("invalid frame index")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("invalid frame index")
    VideoInfo(payload["duration_ms"], payload["width"], payload["height"])
    if not _settings_valid(payload["settings"]):
        raise ValueError("invalid frame index")
    raw_entries = payload["entries"]
    if not isinstance(raw_entries, list):
        raise ValueError("invalid frame index")
    entries: list[FrameIndexEntry] = []
    previous_time = -1
    for expected_index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict) or set(raw) != {"index", "time_ms", "image_path", "reasons"}:
            raise ValueError("invalid frame index")
        reasons = raw["reasons"]
        if not isinstance(reasons, list):
            raise ValueError("invalid frame index")
        normalized_reasons = _validated_reasons(reasons)
        entry = FrameIndexEntry(raw["index"], raw["time_ms"], raw["image_path"], normalized_reasons)
        if (
            entry.index != expected_index
            or entry.image_path != f"frames/frame-{expected_index:04d}.{FRAME_FORMAT}"
            or entry.time_ms < previous_time
            or entry.time_ms > payload["duration_ms"]
        ):
            raise ValueError("invalid frame index")
        image = _resolve_frame_path(frames_dir, entry.image_path)
        _validate_png_frame(image)
        entries.append(entry)
        previous_time = entry.time_ms
    return entries


def _validated_reasons(reasons: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(reasons)
    if normalized not in (("scene",), ("interval",), _REASON_ORDER):
        raise ValueError("invalid frame reasons")
    return normalized


def _settings_valid(settings: object) -> bool:
    expected = _index_payload(VideoInfo(1, 1, 1), [])["settings"]
    return (
        isinstance(settings, dict)
        and set(settings) == set(expected)
        and type(settings["scene_threshold"]) is float
        and settings["scene_threshold"] == expected["scene_threshold"]
        and all(
            type(settings[name]) is int and settings[name] == expected[name]
            for name in ("interval_ms", "merge_tolerance_ms", "max_frame_edge")
        )
        and isinstance(settings["frame_format"], str)
        and settings["frame_format"] == expected["frame_format"]
    )


def _resolve_frame_path(frames_dir: Path, image_path: str) -> Path:
    relative = PurePosixPath(image_path)
    if (
        relative.is_absolute()
        or relative.parts[:1] != ("frames",)
        or len(relative.parts) != 2
        or relative.suffix.casefold() != f".{FRAME_FORMAT}"
    ):
        raise ValueError("invalid frame path")
    candidate = (frames_dir.parent / Path(*relative.parts)).resolve(strict=True)
    if not candidate.is_relative_to(frames_dir.resolve(strict=True)):
        raise ValueError("invalid frame path")
    return candidate


def _validated_work_source(source_path: Path, job_root: Path) -> Path:
    try:
        source = Path(source_path).resolve(strict=True)
    except OSError:
        raise ValueError("frame source is outside job work directory") from None
    work_dir = (job_root / "work").resolve()
    if not source.is_relative_to(work_dir):
        raise ValueError("frame source is outside job work directory")
    return source


def _validate_png_frame(path: Path) -> None:
    if path.suffix.casefold() != f".{FRAME_FORMAT}" or not path.is_file():
        raise ValueError("frame output is not a PNG file")
    try:
        with path.open("rb") as raw_file:
            header = raw_file.read(24)
        if header[:8] != _PNG_SIGNATURE or header[12:16] != b"IHDR":
            raise ValueError
        width, height = struct.unpack(">II", header[16:24])
    except (OSError, struct.error, ValueError):
        raise ValueError("frame output is not a PNG file") from None
    if width == 0 or height == 0 or width > MAX_FRAME_EDGE or height > MAX_FRAME_EDGE:
        raise ValueError("frame exceeds maximum edge")
