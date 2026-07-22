from __future__ import annotations

import json
import os
from collections.abc import Sequence
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

from minoru_studio.jobs.store import JobStore, fingerprint_artifact
from minoru_studio.narrate.models import DurationWarning, TimedUtterance, Utterance, WavInfo


def build_timeline(
    utterances: Sequence[Utterance], wavs: Sequence[tuple[Path, WavInfo]], *, silence_ms: int = 300,
) -> tuple[TimedUtterance, ...]:
    if len(utterances) != len(wavs) or not utterances:
        raise ValueError("utterances and WAVs must be nonempty and match")
    if type(silence_ms) is not int or silence_ms <= 0:
        raise ValueError("silence_ms must be a positive integer")
    cursor = Decimal(0); silence = Decimal(silence_ms) / Decimal(1_000); cues: list[TimedUtterance] = []
    for expected, (utterance, (wav, info)) in enumerate(zip(utterances, wavs, strict=True), 1):
        if utterance.index != expected or not isinstance(info, WavInfo):
            raise ValueError("invalid utterance/WAV sequence")
        start = _round_ms(cursor); cursor += info.duration_seconds; end = _round_ms(cursor)
        relative = f"utterances/{Path(wav).name}"
        try:
            cue = TimedUtterance(utterance.index, utterance.text, start, end, relative)
        except ValueError as exc:
            raise ValueError("timeline has nonpositive or overlapping cue") from exc
        if cues and cue.start_ms < cues[-1].end_ms:
            raise ValueError("timeline has nonpositive or overlapping cue")
        cues.append(cue); cursor += silence
    return tuple(cues)


def write_subtitles(outputs_dir: Path, cues: Sequence[TimedUtterance]) -> tuple[Path, Path]:
    outputs = Path(outputs_dir); srt = outputs / "subtitles.srt"; vtt = outputs / "subtitles.vtt"
    _validate_cues(cues)
    if srt.exists() or vtt.exists():
        raise ValueError("final artifact already exists")
    temporary_srt: Path | None = None; temporary_vtt: Path | None = None
    published: list[tuple[Path, Path]] = []
    try:
        temporary_srt = _stage_text(srt, _render_srt(cues))
        temporary_vtt = _stage_text(vtt, _render_vtt(cues))
        _publish_create_only(temporary_srt, srt); published.append((temporary_srt, srt))
        _publish_create_only(temporary_vtt, vtt); published.append((temporary_vtt, vtt))
    except BaseException:
        _rollback(published); raise
    finally:
        if temporary_srt is not None: temporary_srt.unlink(missing_ok=True)
        if temporary_vtt is not None: temporary_vtt.unlink(missing_ok=True)
    return srt, vtt


def read_duration_warning(job_dir: Path) -> DurationWarning | None:
    path = Path(job_dir) / "work" / "duration-warning.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if set(payload) != {"source_duration_ms", "narration_duration_ms"}:
            raise ValueError
        return DurationWarning(payload["source_duration_ms"], payload["narration_duration_ms"])
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("invalid duration warning") from None


def artifacts_valid(job_dir: Path, *, include_preview: bool) -> bool:
    try:
        root = Path(job_dir).resolve(strict=True); outputs = (root / "outputs").resolve(strict=True)
        utterances = (outputs / "utterances").resolve(strict=True)
        if not utterances.is_relative_to(root): return False
        wavs = sorted(utterances.glob("utterance-*.wav"))
        if not wavs or [path.name for path in wavs] != [f"utterance-{index:04d}.wav" for index in range(1, len(wavs) + 1)]: return False
        names = ["narration.wav", "subtitles.srt", "subtitles.vtt"] + (["preview.mp4"] if include_preview else [])
        expected = [*wavs, *(outputs / name for name in names)]
        expected_kinds = {
            **{path.relative_to(root).as_posix(): "utterance-wav" for path in wavs},
            "outputs/narration.wav": "narration-wav",
            "outputs/subtitles.srt": "subtitles-srt",
            "outputs/subtitles.vtt": "subtitles-vtt",
            **({"outputs/preview.mp4": "preview-mp4"} if include_preview else {}),
        }
        if any(not path.is_file() or not path.resolve().is_relative_to(root) for path in expected): return False
        actual = {path.resolve() for path in outputs.rglob("*") if path.is_file()}
        if actual != {path.resolve() for path in expected}: return False
        manifest = JobStore().load(root, recover_interrupted=False)
        expected_paths = set(expected_kinds)
        if len(manifest.artifacts) != len(expected) or {item.path for item in manifest.artifacts} != expected_paths: return False
        for path in expected:
            matches = [item for item in manifest.artifacts if item.path == path.relative_to(root).as_posix()]
            if len(matches) != 1 or matches[0].kind != expected_kinds[matches[0].path]: return False
            if fingerprint_artifact(root, path, expected_kinds[matches[0].path]) != matches[0]: return False
    except (OSError, ValueError, TypeError):
        return False
    return True


def _round_ms(seconds: Decimal) -> int:
    return int((seconds * 1_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _validate_cues(cues: Sequence[TimedUtterance]) -> None:
    previous = -1
    for expected, cue in enumerate(cues, 1):
        if not isinstance(cue, TimedUtterance) or cue.index != expected or cue.start_ms < previous:
            raise ValueError("invalid subtitle cues")
        previous = cue.end_ms


def _render_srt(cues: Sequence[TimedUtterance]) -> str:
    return "\n".join(
        f"{cue.index}\n{_timestamp(cue.start_ms, ',')} --> {_timestamp(cue.end_ms, ',')}\n{cue.text}\n"
        for cue in cues
    )


def _render_vtt(cues: Sequence[TimedUtterance]) -> str:
    return "WEBVTT\n\n" + "\n".join(
        f"{_timestamp(cue.start_ms, '.')} --> {_timestamp(cue.end_ms, '.')}\n{cue.text}\n"
        for cue in cues
    )


def _timestamp(milliseconds: int, separator: str) -> str:
    hours, remaining = divmod(milliseconds, 3_600_000); minutes, remaining = divmod(remaining, 60_000); seconds, ms = divmod(remaining, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{ms:03d}"


def _stage_text(destination: Path, text: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True); temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8"); return temporary


def _publish_create_only(temporary: Path, destination: Path) -> None:
    try: os.link(temporary, destination)
    except FileExistsError: raise ValueError("final artifact already exists") from None


def _rollback(published: Sequence[tuple[Path, Path]]) -> None:
    for temporary, destination in reversed(published):
        try:
            if temporary.exists() and destination.exists() and os.path.samefile(temporary, destination): destination.unlink()
        except OSError: continue
