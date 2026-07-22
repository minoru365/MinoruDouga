from __future__ import annotations

import hmac
import os
import re
from pathlib import Path
from uuid import uuid4

from minoru_studio.jobs.store import fingerprint_file
from minoru_studio.narrate.models import Utterance


_MAX_UTTERANCE_LENGTH = 60
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LINK_ONLY = re.compile(r"^(?:!\[\]\([^)]*\)|\[\]\([^)]*\)|\[[^]]+\]\([^)]*\))$")
_EXCLUDED_PREFIXES = ("画面の説明", "操作")
_TERMINATORS = frozenset("。！？.!?")


def parse_script(path: Path) -> tuple[Utterance, ...]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in {".txt", ".md"}:
        raise ValueError("script must have a .txt or .md suffix")
    lines = source.read_text(encoding="utf-8").splitlines()
    content = [_normalize_text_lines(lines)] if suffix == ".txt" else _narration_lines(lines)
    utterance_texts = [piece for line in content for piece in _split_line(line)]
    if not utterance_texts:
        raise ValueError("script contains no narration")
    return tuple(Utterance(index, text) for index, text in enumerate(utterance_texts, 1))


def snapshot_script(source: Path, inputs_dir: Path) -> Path:
    source_path = Path(source)
    suffix = source_path.suffix.lower()
    if suffix not in {".txt", ".md"}:
        raise ValueError("script must have a .txt or .md suffix")
    source_path.read_text(encoding="utf-8")
    destination_dir = Path(inputs_dir)
    destination = destination_dir / f"script{suffix}"
    temporary = destination_dir / f".script-{uuid4().hex}.tmp"
    try:
        with source_path.open("rb") as input_handle, temporary.open("xb") as output_handle:
            while block := input_handle.read(1024 * 1024):
                output_handle.write(block)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        source_hash = fingerprint_file(source_path).sha256
        staged_hash = fingerprint_file(temporary).sha256
        if not hmac.compare_digest(source_hash, staged_hash):
            raise ValueError("staged script hash does not match source")
        os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def snapshot_valid(snapshot: Path, expected_sha256: str) -> bool:
    try:
        return hmac.compare_digest(fingerprint_file(Path(snapshot)).sha256, expected_sha256)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return False


def _narration_lines(lines: list[str]) -> list[str]:
    active_level: int | None = None
    selected: list[str] = []
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if active_level is not None and level <= active_level:
                active_level = None
            if title == "ナレーション" and active_level is None:
                active_level = level
            continue
        if active_level is not None:
            selected.append(line)
    return selected


def _normalize_text_lines(lines: list[str]) -> str:
    return "\n".join(line.strip() for line in lines if line.strip())


def _split_line(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped or _is_excluded(stripped):
        return []
    sentences: list[str] = []
    start = 0
    for index, character in enumerate(stripped):
        if character in _TERMINATORS:
            sentences.append(stripped[start : index + 1].strip())
            start = index + 1
            while start < len(stripped) and stripped[start].isspace():
                start += 1
    if remaining := stripped[start:].strip():
        sentences.append(remaining)
    return [chunk for sentence in sentences for chunk in _split_length(sentence)]


def _is_excluded(line: str) -> bool:
    return line.startswith(_EXCLUDED_PREFIXES) or bool(_LINK_ONLY.fullmatch(line))


def _split_length(text: str) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while len(remaining) > _MAX_UTTERANCE_LENGTH:
        boundary = max(
            (index for index, character in enumerate(remaining[: _MAX_UTTERANCE_LENGTH + 1]) if character.isspace()),
            default=-1,
        )
        if boundary > 0:
            chunks.append(remaining[:boundary].rstrip())
            remaining = remaining[boundary:].lstrip()
        else:
            chunks.append(remaining[:_MAX_UTTERANCE_LENGTH])
            remaining = remaining[_MAX_UTTERANCE_LENGTH:]
    if remaining:
        chunks.append(remaining)
    return chunks
