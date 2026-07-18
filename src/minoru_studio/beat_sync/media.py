from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from minoru_studio.beat_sync.models import MaterialKind


AUDIO_EXTENSIONS = frozenset({".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aac"})
PHOTO_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".dng"}
)
VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".m4v", ".mxf", ".avi", ".mkv", ".braw", ".mts", ".m2ts"}
)


@dataclass(frozen=True, slots=True)
class MaterialSource:
    path: Path
    kind: MaterialKind


def validate_music_file(path: str | Path) -> Path:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"music input is not a file: {resolved}")
    if resolved.suffix.casefold() not in AUDIO_EXTENSIONS:
        raise ValueError(f"unsupported music extension: {resolved.suffix}")
    return resolved


def discover_materials(directory: str | Path) -> list[MaterialSource]:
    root = Path(directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"media directory is not a directory: {root}")
    materials = []
    for path in root.iterdir():
        if not path.is_file():
            continue
        suffix = path.suffix.casefold()
        if suffix in PHOTO_EXTENSIONS:
            materials.append(MaterialSource(path.resolve(), MaterialKind.PHOTO))
        elif suffix in VIDEO_EXTENSIONS:
            materials.append(MaterialSource(path.resolve(), MaterialKind.VIDEO))
    materials.sort(key=lambda item: (item.path.name.casefold(), str(item.path).casefold()))
    if not materials:
        raise ValueError(f"no supported visual media found: {root}")
    return materials


def order_materials(
    materials: Iterable[MaterialSource],
    mode: str,
    shuffler: Callable[[list[MaterialSource]], None] | None = None,
) -> list[MaterialSource]:
    ordered = sorted(
        materials,
        key=lambda item: (item.path.name.casefold(), str(item.path).casefold()),
    )
    if mode == "asc":
        return ordered
    if mode != "random":
        raise ValueError("order mode must be asc or random")
    (shuffler or random.SystemRandom().shuffle)(ordered)
    return ordered
