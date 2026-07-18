from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from uuid import uuid4

from minoru_studio.transcribe.contracts import SegmentResult, WorkerResult


MAX_LINE_CODEPOINTS = 21
MAX_CUE_CODEPOINTS = 42
STRONG_BREAKS = frozenset("。！？!?")
SOFT_BREAKS = frozenset("、，,；;：:")


class SubtitleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Cue:
    start_ms: int
    end_ms: int
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TimedCharacter:
    character: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class _DraftCue:
    text: str
    start_ms: int
    end_ms: int


def build_cues(result: WorkerResult) -> tuple[Cue, ...]:
    """Build positive, ordered subtitle cues from a validated worker result."""
    drafts: list[_DraftCue] = []
    for segment in result.segments:
        drafts.extend(_build_segment_drafts(segment))
    repaired = _repair_intervals(drafts, result.duration_ms)
    return tuple(
        Cue(draft.start_ms, draft.end_ms, _wrap_lines(draft.text))
        for draft in repaired
    )


def render_transcript(result: WorkerResult) -> str:
    lines = [segment.text.strip() for segment in result.segments]
    non_empty = [line for line in lines if line]
    return "" if not non_empty else "\n".join(non_empty) + "\n"


def render_srt(result: WorkerResult) -> str:
    cues = build_cues(result)
    return _render_srt_cues(cues)


def render_vtt(result: WorkerResult) -> str:
    cues = build_cues(result)
    return _render_vtt_cues(cues)


def write_artifacts(job_dir: str | Path, result: WorkerResult) -> tuple[Path, Path, Path]:
    directory = Path(job_dir)
    destinations = (
        directory / "transcript.txt",
        directory / "subtitles.srt",
        directory / "subtitles.vtt",
    )
    cues = build_cues(result)
    contents = (
        render_transcript(result),
        _render_srt_cues(cues),
        _render_vtt_cues(cues),
    )
    if _parse_srt(contents[1]) != cues or _parse_vtt(contents[2]) != cues:
        raise SubtitleError("generated subtitles did not validate")

    temporary_paths: list[Path] = []
    try:
        for destination, content in zip(destinations, contents, strict=True):
            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary_paths.append(temporary)
        for temporary, destination in zip(temporary_paths, destinations, strict=True):
            os.replace(temporary, destination)
    finally:
        for temporary in temporary_paths:
            if temporary.exists():
                temporary.unlink()
    return destinations


def _build_segment_drafts(segment: SegmentResult) -> list[_DraftCue]:
    characters = _word_characters(segment)
    if characters is None:
        characters = _timed_characters(
            segment.text, segment.start_ms, segment.end_ms, non_space_only=True
        )
    return _split_characters(characters)


def _word_characters(segment: SegmentResult) -> list[_TimedCharacter] | None:
    if not segment.words or any(word.end_ms <= word.start_ms for word in segment.words):
        return None
    word_text = "".join(word.text for word in segment.words)
    trimmed_word_text = word_text.strip()
    trimmed_segment_text = segment.text.strip()
    if trimmed_word_text != trimmed_segment_text:
        return None

    word_characters = [
        character
        for word in segment.words
        for character in _timed_characters(word.text, word.start_ms, word.end_ms)
    ]
    first = next(
        (index for index, character in enumerate(word_characters) if not character.character.isspace()),
        None,
    )
    last = next(
        (
            index
            for index in range(len(word_characters) - 1, -1, -1)
            if not word_characters[index].character.isspace()
        ),
        None,
    )
    if first is None or last is None:
        return None
    core = word_characters[first : last + 1]
    if "".join(character.character for character in core) != trimmed_segment_text:
        return None

    leading_length = len(segment.text) - len(segment.text.lstrip())
    trailing_length = len(segment.text) - len(segment.text.rstrip())
    return [
        *(_TimedCharacter(character, core[0].start_ms, core[0].start_ms) for character in segment.text[:leading_length]),
        *core,
        *(
            _TimedCharacter(character, core[-1].end_ms, core[-1].end_ms)
            for character in segment.text[len(segment.text) - trailing_length :]
        ),
    ]


def _timed_characters(
    text: str, start_ms: int, end_ms: int, *, non_space_only: bool = False
) -> list[_TimedCharacter]:
    timed_positions = [
        index for index, character in enumerate(text) if not non_space_only or not character.isspace()
    ]
    if not timed_positions:
        return []
    duration = end_ms - start_ms
    base, remainder = divmod(duration, len(timed_positions))
    intervals: dict[int, tuple[int, int]] = {}
    current = start_ms
    for order, index in enumerate(timed_positions):
        next_current = current + base + (1 if order < remainder else 0)
        intervals[index] = (current, next_current)
        current = next_current

    characters: list[_TimedCharacter] = []
    current = start_ms
    for index, character in enumerate(text):
        interval = intervals.get(index)
        if interval is not None:
            current = interval[1]
            characters.append(_TimedCharacter(character, interval[0], interval[1]))
        else:
            characters.append(_TimedCharacter(character, current, current))
    return characters


def _split_characters(characters: list[_TimedCharacter]) -> list[_DraftCue]:
    drafts: list[_DraftCue] = []
    buffer: list[_TimedCharacter] = []
    for character in characters:
        buffer.append(character)
        if character.character in STRONG_BREAKS:
            drafts.append(_draft_from(buffer))
            buffer = []
        elif len(buffer) == MAX_CUE_CODEPOINTS:
            split_at = _preferred_break(buffer)
            drafts.append(_draft_from(buffer[:split_at]))
            buffer = buffer[split_at:]
    if buffer:
        drafts.append(_draft_from(buffer))
    return drafts


def _preferred_break(characters: list[_TimedCharacter]) -> int:
    for allowed in (STRONG_BREAKS, SOFT_BREAKS):
        for index in range(len(characters) - 1, -1, -1):
            if characters[index].character in allowed:
                return index + 1
    return MAX_CUE_CODEPOINTS


def _draft_from(characters: list[_TimedCharacter]) -> _DraftCue:
    return _DraftCue(
        "".join(character.character for character in characters),
        characters[0].start_ms,
        characters[-1].end_ms,
    )


def _repair_intervals(drafts: list[_DraftCue], duration_ms: int) -> list[_DraftCue]:
    repaired: list[_DraftCue] = []
    index = 0
    while index < len(drafts):
        draft = drafts[index]
        start_ms = max(draft.start_ms, repaired[-1].end_ms if repaired else 0)
        end_ms = max(draft.end_ms, start_ms)
        if end_ms > start_ms:
            repaired.append(_DraftCue(draft.text, start_ms, end_ms))
            index += 1
            continue

        if repaired and len(repaired[-1].text) + len(draft.text) <= MAX_CUE_CODEPOINTS:
            repaired[-1].text += draft.text
            repaired[-1].end_ms = max(repaired[-1].end_ms, end_ms)
            index += 1
            continue

        if index + 1 < len(drafts) and len(draft.text) + len(drafts[index + 1].text) <= MAX_CUE_CODEPOINTS:
            following = drafts[index + 1]
            drafts[index + 1] = _DraftCue(
                draft.text + following.text,
                start_ms,
                max(end_ms, following.end_ms),
            )
            index += 1
            continue

        previous_end = repaired[-1].end_ms if repaired else 0
        if start_ms - 1 >= previous_end:
            repaired.append(_DraftCue(draft.text, start_ms - 1, start_ms))
            index += 1
            continue

        next_start = drafts[index + 1].start_ms if index + 1 < len(drafts) else duration_ms
        if start_ms + 1 <= min(next_start, duration_ms):
            repaired.append(_DraftCue(draft.text, start_ms, start_ms + 1))
            index += 1
            continue
        raise SubtitleError("no positive ordered subtitle interval is available")
    return repaired


def _wrap_lines(text: str) -> tuple[str, ...]:
    lines = tuple(
        text[offset : offset + MAX_LINE_CODEPOINTS]
        for offset in range(0, len(text), MAX_LINE_CODEPOINTS)
    )
    if not lines or len(lines) > 2:
        raise SubtitleError("subtitle cue cannot be wrapped into two lines")
    return lines


def _render_srt_cues(cues: tuple[Cue, ...]) -> str:
    blocks = [
        "\n".join(
            (str(index), f"{_format_time(cue.start_ms, ',')} --> {_format_time(cue.end_ms, ',')}", *cue.lines)
        )
        for index, cue in enumerate(cues, start=1)
    ]
    return "" if not blocks else "\n\n".join(blocks) + "\n"


def _render_vtt_cues(cues: tuple[Cue, ...]) -> str:
    blocks = [
        "\n".join(
            (f"{_format_time(cue.start_ms, '.')} --> {_format_time(cue.end_ms, '.')}", *cue.lines)
        )
        for cue in cues
    ]
    return "WEBVTT\n\n" + ("\n\n".join(blocks) + "\n" if blocks else "")


def _format_time(milliseconds: int, separator: str) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{milliseconds:03d}"


_TIMESTAMP = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{2}):(\d{2}):(\d{2})[,.](\d{3})$")


def _parse_srt(content: str) -> tuple[Cue, ...]:
    if not content:
        return ()
    blocks = content.rstrip("\n").split("\n\n")
    cues: list[Cue] = []
    for expected_index, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(expected_index):
            raise SubtitleError("invalid generated SRT")
        start_ms, end_ms = _parse_timestamp_line(lines[1])
        cues.append(Cue(start_ms, end_ms, tuple(lines[2:])))
    return tuple(cues)


def _parse_vtt(content: str) -> tuple[Cue, ...]:
    if content == "WEBVTT\n\n":
        return ()
    if not content.startswith("WEBVTT\n\n"):
        raise SubtitleError("invalid generated VTT")
    blocks = content[len("WEBVTT\n\n") :].rstrip("\n").split("\n\n")
    cues: list[Cue] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 2:
            raise SubtitleError("invalid generated VTT")
        start_ms, end_ms = _parse_timestamp_line(lines[0])
        cues.append(Cue(start_ms, end_ms, tuple(lines[1:])))
    return tuple(cues)


def _parse_timestamp_line(value: str) -> tuple[int, int]:
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise SubtitleError("invalid generated subtitle timestamp")
    values = tuple(int(value) for value in match.groups())
    start_ms = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1_000 + values[3]
    end_ms = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1_000 + values[7]
    return start_ms, end_ms
