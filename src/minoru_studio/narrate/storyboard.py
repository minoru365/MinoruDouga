"""Strict, local-only storyboard parsing and cue association."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Utterance
from .script import segment_narration
from uuid import uuid4
import hmac
from minoru_studio.beat_sync.media import AUDIO_EXTENSIONS
from minoru_studio.jobs.store import fingerprint_file


_IMAGE_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"})
_VIDEO_SUFFIXES = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"})
_PROTOCOL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_MAX_MUSIC_TRACKS = 8
_DEFAULT_MUSIC_GAIN_DB = -18.0
_DEFAULT_CROSSFADE_MS = 1500


@dataclass(frozen=True, slots=True)
class StoryboardClip:
    id: str
    kind: str
    source: Path
    narration: str
    trim_start_ms: int | None = None
    trim_end_ms: int | None = None
    music: str | None = None


@dataclass(frozen=True, slots=True)
class MusicTrack:
    id: str
    source: Path
    gain_db: float


@dataclass(frozen=True, slots=True)
class StoryboardMusic:
    tracks: tuple[MusicTrack, ...]
    default: str
    crossfade_ms: int

    def track(self, identifier: str) -> MusicTrack:
        return next(track for track in self.tracks if track.id == identifier)


@dataclass(frozen=True, slots=True)
class Storyboard:
    descriptor: Path
    clips: tuple[StoryboardClip, ...]
    music: StoryboardMusic | None = None

    @property
    def sources(self) -> tuple[Path, ...]:
        seen: set[Path] = set()
        return tuple(clip.source for clip in self.clips if not (clip.source in seen or seen.add(clip.source)))

    @property
    def input_paths(self) -> tuple[Path, ...]:
        """Visual sources in first-use order, then music sources in track order."""
        paths = list(self.sources)
        for track in self.music.tracks if self.music is not None else ():
            if track.source not in paths:
                paths.append(track.source)
        return tuple(paths)

    def track_for(self, clip: StoryboardClip) -> str | None:
        if self.music is None:
            return None
        return clip.music or self.music.default


@dataclass(frozen=True, slots=True)
class ClipUtterances:
    clip: StoryboardClip
    utterances: tuple[Utterance, ...]


def parse_storyboard(path: Path, *, source_base: Path | None = None) -> Storyboard:
    descriptor = Path(path).resolve(strict=True)
    if descriptor.suffix.casefold() != ".json":
        raise ValueError("storyboard must have a .json suffix")
    try:
        payload = json.loads(descriptor.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("invalid storyboard descriptor") from None
    if not isinstance(payload, dict) or not {"version", "clips"} <= set(payload) <= {"version", "clips", "music"} or payload["version"] != 1 or not isinstance(payload["clips"], list) or not payload["clips"]:
        raise ValueError("invalid storyboard descriptor")
    base = Path(source_base).resolve(strict=True) if source_base is not None else descriptor.parent
    clips = tuple(_clip(item, base) for item in payload["clips"])
    if len({clip.id for clip in clips}) != len(clips):
        raise ValueError("invalid storyboard descriptor")
    music = _music(payload["music"], base) if "music" in payload else None
    declared = {track.id for track in music.tracks} if music is not None else set()
    if any(clip.music is not None and clip.music not in declared for clip in clips):
        raise ValueError("invalid storyboard descriptor")
    return Storyboard(descriptor, clips, music)


def storyboard_utterances(storyboard: Storyboard) -> tuple[ClipUtterances, ...]:
    if not isinstance(storyboard, Storyboard):
        raise ValueError("invalid storyboard")
    cursor = 1
    result: list[ClipUtterances] = []
    for clip in storyboard.clips:
        pieces = segment_narration(clip.narration)
        if not pieces:
            raise ValueError("invalid storyboard descriptor")
        utterances = tuple(Utterance(cursor + offset, text) for offset, text in enumerate(pieces))
        cursor += len(utterances)
        result.append(ClipUtterances(clip, utterances))
    return tuple(result)


def snapshot_storyboard(source: Path, inputs_dir: Path) -> Path:
    """Byte-snapshot a validated descriptor with create-only publication."""
    source_path = Path(source).resolve(strict=True)
    parse_storyboard(source_path)
    destination = Path(inputs_dir) / "storyboard.json"
    temporary = destination.with_name(f".storyboard-{uuid4().hex}.tmp")
    try:
        with source_path.open("rb") as original, temporary.open("xb") as staged:
            while block := original.read(1024 * 1024):
                staged.write(block)
            staged.flush(); os.fsync(staged.fileno())
        if not hmac.compare_digest(fingerprint_file(source_path).sha256, fingerprint_file(temporary).sha256):
            raise ValueError("staged storyboard hash does not match source")
        os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def is_supported_source(kind: str, source: Path) -> bool:
    return source.suffix.casefold() in (_IMAGE_SUFFIXES if kind == "image" else _VIDEO_SUFFIXES if kind == "video" else frozenset())


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _music(value: object, base: Path) -> StoryboardMusic:
    if not isinstance(value, dict) or not {"tracks", "default"} <= set(value) <= {"tracks", "default", "crossfade_ms"}:
        raise ValueError("invalid storyboard descriptor")
    raw_tracks, default = value["tracks"], value["default"]
    crossfade = value.get("crossfade_ms", _DEFAULT_CROSSFADE_MS)
    if not isinstance(raw_tracks, list) or not 1 <= len(raw_tracks) <= _MAX_MUSIC_TRACKS or type(crossfade) is not int or not 0 <= crossfade <= 5000:
        raise ValueError("invalid storyboard descriptor")
    tracks = tuple(_track(item, base) for item in raw_tracks)
    if len({track.id for track in tracks}) != len(tracks) or default not in {track.id for track in tracks}:
        raise ValueError("invalid storyboard descriptor")
    return StoryboardMusic(tracks, default, crossfade)


def _track(value: object, base: Path) -> MusicTrack:
    if not isinstance(value, dict) or not {"id", "source"} <= set(value) <= {"id", "source", "gain_db"}:
        raise ValueError("invalid storyboard descriptor")
    identifier, raw_source = value["id"], value["source"]
    gain = value.get("gain_db", _DEFAULT_MUSIC_GAIN_DB)
    if not isinstance(identifier, str) or not identifier.strip() or type(gain) not in {int, float} or not -40 <= gain <= 0:
        raise ValueError("invalid storyboard descriptor")
    source = _local_source(raw_source, base)
    if source.suffix.casefold() not in AUDIO_EXTENSIONS:
        raise ValueError("invalid storyboard descriptor")
    return MusicTrack(identifier, source, float(gain))


def _local_source(raw_source: object, base: Path) -> Path:
    if not isinstance(raw_source, str) or not raw_source.strip():
        raise ValueError("invalid storyboard descriptor")
    raw_path = Path(raw_source)
    if (_PROTOCOL.match(raw_source) and not raw_path.drive) or raw_source.startswith(("//", "\\\\")):
        raise ValueError("invalid storyboard descriptor")
    try:
        source = raw_path.resolve(strict=True) if raw_path.is_absolute() else (base / raw_path).resolve(strict=True)
    except OSError:
        raise ValueError("invalid storyboard descriptor") from None
    if not source.is_file():
        raise ValueError("invalid storyboard descriptor")
    return source


def _clip(value: object, base: Path) -> StoryboardClip:
    if not isinstance(value, dict):
        raise ValueError("invalid storyboard descriptor")
    keys = {"id", "kind", "source", "narration", "trim_start_ms", "trim_end_ms", "music"}
    if not set(value).issubset(keys) or not {"id", "kind", "source", "narration"}.issubset(value):
        raise ValueError("invalid storyboard descriptor")
    identifier, kind, raw_source, narration = (value["id"], value["kind"], value["source"], value["narration"])
    if not all(isinstance(item, str) and item.strip() for item in (identifier, kind, raw_source, narration)) or kind not in {"image", "video"}:
        raise ValueError("invalid storyboard descriptor")
    music = value.get("music")
    if "music" in value and (not isinstance(music, str) or not music.strip()):
        raise ValueError("invalid storyboard descriptor")
    source = _local_source(raw_source, base)
    if not is_supported_source(kind, source):
        raise ValueError("invalid storyboard descriptor")
    start, end = value.get("trim_start_ms"), value.get("trim_end_ms")
    if kind == "image" and ("trim_start_ms" in value or "trim_end_ms" in value):
        raise ValueError("invalid storyboard descriptor")
    if kind == "video":
        if start is not None and (type(start) is not int or start < 0):
            raise ValueError("invalid storyboard descriptor")
        if end is not None and (type(end) is not int or end <= 0):
            raise ValueError("invalid storyboard descriptor")
        if start is not None and end is not None and start >= end:
            raise ValueError("invalid storyboard descriptor")
    return StoryboardClip(identifier, kind, source, narration, start, end, music)
