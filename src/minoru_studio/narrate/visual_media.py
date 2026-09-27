"""FFmpeg rendering for cue-derived local visual sequences."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

from minoru_studio.processes import ProcessResult, run_cancellable_process
from minoru_studio.transcribe.media import FontChoice, _escape_filter_path, is_supported_japanese_font, resolve_japanese_font

from .media import _publish_create_only, _require_success, validate_preview

Runner = Callable[..., ProcessResult]


def cumulative_frame_boundaries(boundaries_ms: Sequence[int], *, fps: int = 30) -> tuple[int, ...]:
    if fps <= 0 or not boundaries_ms or boundaries_ms[0] != 0 or any(type(value) is not int or value < 0 for value in boundaries_ms):
        raise ValueError("invalid visual boundaries")
    frames = tuple(round(value * fps / 1000) for value in boundaries_ms)
    if any(right <= left for left, right in zip(frames, frames[1:])):
        raise ValueError("visual intervals do not reach a frame")
    return frames


def plan_music_runs(
    assignments: Sequence[tuple[str, int, int]], *, crossfade_ms: int, final_fade_ms: int = 2000,
) -> tuple[dict[str, object], ...]:
    """Merge per-clip track assignments into runs with boundary-centred crossfades.

    Each run restarts its track. The fade at a boundary is limited by both
    neighbouring runs, so one run's fade-in and fade-out never overlap.
    """
    if not assignments or assignments[0][1] != 0:
        raise ValueError("music assignments must start at zero")
    merged: list[list[object]] = []
    for track, start, end in assignments:
        if type(start) is not int or type(end) is not int or end <= start:
            raise ValueError("invalid music assignment")
        if merged and merged[-1][2] != start:
            raise ValueError("music assignments must be contiguous")
        if merged and merged[-1][0] == track:
            merged[-1][2] = end
        else:
            merged.append([track, start, end])
    fades = [
        min(crossfade_ms, left[2] - left[1], right[2] - right[1])  # type: ignore[operator]
        for left, right in zip(merged, merged[1:])
    ]
    runs: list[dict[str, object]] = []
    for index, (track, start, end) in enumerate(merged):
        fade_in = fades[index - 1] if index else 0
        fade_out = fades[index] if index < len(fades) else 0
        run_start = start - fade_in // 2  # type: ignore[operator]
        run_end = end + fade_out - fade_out // 2  # type: ignore[operator]
        if index == len(merged) - 1:
            fade_out = min(final_fade_ms, run_end - run_start - fade_in)
        runs.append({"track": track, "start_ms": run_start, "end_ms": run_end, "fade_in_ms": fade_in, "fade_out_ms": fade_out})
    return tuple(runs)


def music_mix_arguments(
    runs: Sequence[dict[str, object]], *, narration_input: int, first_input: int,
) -> tuple[list[str], str]:
    """Build looping music inputs and a graph mixing them under unit-gain narration."""
    inputs: list[str] = []
    filters = [f"[{narration_input}:a]{_AUDIO_FORMAT}[narration]"]
    for index, run in enumerate(runs):
        start, end, fade_in, fade_out = (run["start_ms"], run["end_ms"], run["fade_in_ms"], run["fade_out_ms"])
        length = end - start  # type: ignore[operator]
        inputs.extend(["-stream_loop", "-1", "-t", f"{length / 1000:.3f}", "-i", str(run["source"])])
        chain = [_AUDIO_FORMAT, f"volume={float(run['gain_db']):.1f}dB"]  # type: ignore[arg-type]
        if fade_in:
            chain.append(f"afade=t=in:st=0:d={fade_in / 1000:.3f}")  # type: ignore[operator]
        if fade_out:
            chain.append(f"afade=t=out:st={(length - fade_out) / 1000:.3f}:d={fade_out / 1000:.3f}")  # type: ignore[operator]
        chain.append(f"adelay={start}:all=1")
        filters.append(f"[{first_input + index}:a]{','.join(chain)}[music{index}]")
    labels = "".join(f"[music{index}]" for index in range(len(runs)))
    filters.append(f"[narration]{labels}amix=inputs={len(runs) + 1}:normalize=0:duration=first,alimiter=limit=0.97[outa]")
    return inputs, ";".join(filters)


_AUDIO_FORMAT = "aformat=sample_rates=48000:channel_layouts=stereo"


def render_visual_preview(
    clips: Sequence[dict[str, object]], narration: Path, subtitles: Path, destination: Path, narration_ms: int, *,
    font: FontChoice | None = None, runner: Runner = run_cancellable_process, cancel_event: object | None = None,
    music: Sequence[dict[str, object]] | None = None,
) -> Path:
    """Render still/video clips into one muted 1280x720@30 sequence and add narration and optional music."""
    if type(narration_ms) is not int or narration_ms <= 0 or not clips:
        raise ValueError("invalid visual preview")
    selected = font if font is not None else resolve_japanese_font()
    if not is_supported_japanese_font(selected):
        raise ValueError("preview font is invalid")
    inputs: list[str] = []; filters: list[str] = []
    for index, clip in enumerate(clips):
        kind, source, start, end = (clip.get("kind"), clip.get("source"), clip.get("start_ms"), clip.get("end_ms"))
        if kind not in {"image", "video"} or not isinstance(source, str) or type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise ValueError("invalid visual plan")
        duration = (end - start) / 1000
        if kind == "image":
            inputs.extend(["-loop", "1", "-framerate", "30", "-t", f"{duration:.3f}", "-i", source])
            visual = f"[{index}:v]trim=duration={duration:.3f}"
        else:
            trim_start = clip.get("trim_start_ms", 0)
            if type(trim_start) is not int or trim_start < 0:
                raise ValueError("invalid visual plan")
            inputs.extend(["-ss", f"{trim_start / 1000:.3f}", "-i", source])
            visual = f"[{index}:v]trim=duration={duration:.3f},tpad=stop_mode=clone:stop_duration={duration:.3f}"
        filters.append(f"{visual},setpts=PTS-STARTPTS,fps=30,scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1[v{index}]")
    joined = "".join(f"[v{index}]" for index in range(len(clips)))
    subtitle = f"subtitles=filename='{_escape_filter_path(Path(subtitles))}':fontsdir='{_escape_filter_path(selected.file.parent)}':force_style=FontName={selected.family}"
    filters.append(f"{joined}concat=n={len(clips)}:v=1:a=0,{subtitle}[outv]")
    output = Path(destination); output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.{uuid4().hex}.mp4")
    audio_map = f"{len(clips)}:a:0"; music_inputs: list[str] = []
    if music:
        music_inputs, music_graph = music_mix_arguments(music, narration_input=len(clips), first_input=len(clips) + 1)
        filters.append(music_graph); audio_map = "[outa]"
    try:
        args = ["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs, "-i", str(narration), *music_inputs, "-filter_complex", ";".join(filters), "-map", "[outv]", "-map", audio_map, "-t", f"{narration_ms / 1000:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(temporary)]
        _require_success(runner(args, cancel_event=cancel_event), "FFmpeg visual preview rendering")
        validate_preview(temporary, narration_ms, runner=runner)
        _publish_create_only(temporary, output)
        return output
    finally:
        temporary.unlink(missing_ok=True)
