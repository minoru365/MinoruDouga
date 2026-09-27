"""Durable, collaborator-injected narration job orchestration."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from uuid import uuid4

from minoru_studio.jobs.lock import JobLockedError
from minoru_studio.jobs.model import ArtifactRecord, InputRef, JobManifest, JobMode, JobStatus, StepRecord, StepStatus
from minoru_studio.jobs.store import JobStore, fingerprint_artifact, fingerprint_file
from minoru_studio.logging_utils import configure_job_logger
from minoru_studio.processes import ProcessCancelledError
from minoru_studio.transcribe.media import FontChoice, MediaInfo, is_supported_japanese_font, probe_media, read_media_tool_versions, resolve_japanese_font

from .artifacts import artifacts_valid, build_timeline, write_subtitles
from .media import concat_wavs, inspect_wav, probe_image, probe_video, publish_wav, render_preview
from .models import ImageInfo, NarrateRequest, StoryboardRequest, Utterance, VideoInfo, VoicevoxProvenance, WavInfo
from .script import parse_script, snapshot_script, snapshot_valid
from .storyboard import ClipUtterances, Storyboard, parse_storyboard, storyboard_utterances
from .visual_media import plan_music_runs, render_visual_preview
from .voicevox import VoicevoxClient, VoicevoxSynthesisError, VoicevoxUnavailable


_STEPS = ("probe-input", "parse-script", "synthesize-utterances", "concat-audio", "render-artifacts")
_PREVIEW = "render-preview"
_SPEAKER = "ずんだもん"
_STYLE = "ノーマル"


class NarrateFailed(RuntimeError):
    """A durable, content-free narration failure."""
    def __init__(self, job_dir: Path, category: str) -> None:
        self.job_dir, self.category = Path(job_dir), category
        super().__init__(f"{category}: {self.job_dir}")


class StoryboardMusicRequiresPreview(ValueError):
    """Storyboard music is mixed only into preview.mp4, so it needs -Preview."""
    def __init__(self) -> None:
        super().__init__("storyboard music requires -Preview")


class NarrateInterrupted(RuntimeError):
    def __init__(self, job_dir: Path) -> None:
        self.job_dir = Path(job_dir)
        super().__init__(f"narration interrupted: {self.job_dir}")


class _InputInvalid(ValueError): pass
class _UnsafePath(_InputInvalid): pass
class _OutputInvalid(ValueError): pass
class _LoggingFailure(RuntimeError): pass
class _NoopLogger:
    def info(self, *args: object, **kwargs: object) -> None: pass
    def error(self, *args: object, **kwargs: object) -> None: pass


class NarrateService:
    """Create and resume narration jobs without logging script content."""

    def __init__(
        self, *, store: JobStore | None = None,
        probe: Callable[[Path], VideoInfo] = probe_video,
        image_probe: Callable[[Path], ImageInfo] = probe_image,
        music_probe: Callable[[Path], MediaInfo] = probe_media,
        parse: Callable[[Path], tuple[Utterance, ...]] = parse_script,
        voicevox: VoicevoxClient | object | None = None,
        publish_wav: Callable[[bytes, Path], WavInfo] = publish_wav,
        inspect_wav: Callable[[Path], WavInfo] = inspect_wav,
        concat: Callable[..., WavInfo] = concat_wavs,
        subtitles: Callable[[Path, Sequence[Any]], tuple[Path, Path]] = write_subtitles,
        preview_renderer: Callable[..., Path] = render_preview,
        visual_preview_renderer: Callable[..., Path] = render_visual_preview,
        artifacts_validator: Callable[..., bool] = artifacts_valid,
        font_resolver: Callable[[], FontChoice] = resolve_japanese_font,
        tool_versions: Callable[[], object] = read_media_tool_versions,
        logger_factory: Callable[[Path], logging.Logger] = configure_job_logger,
    ) -> None:
        self._store = store or JobStore(); self._probe = probe; self._image_probe = image_probe; self._music_probe = music_probe; self._parse = parse
        self._voicevox = voicevox or VoicevoxClient(); self._publish_wav = publish_wav
        self._inspect_wav = inspect_wav; self._concat = concat; self._subtitles = subtitles
        self._preview_renderer = preview_renderer; self._visual_preview_renderer = visual_preview_renderer; self._artifacts_validator = artifacts_validator
        self._font_resolver = font_resolver; self._tool_versions = tool_versions; self._logger_factory = logger_factory

    def create_and_run(self, request: NarrateRequest | StoryboardRequest, *, cancel_event: object | None = None, progress: Callable[[str], object] | None = None) -> Path:
        self._validate_request(request)
        settings = self._settings(request)
        inputs = (request.input_path, request.script_path) if isinstance(request, NarrateRequest) else (request.input_path, *parse_storyboard(request.input_path).input_paths)
        job_dir = self._store.create(request.output_dir, request.name, JobMode.NARRATE, inputs, settings)
        try:
            self._claim(job_dir, settings, {JobStatus.PENDING})
        except _InputInvalid:
            self._mark_failed(job_dir, None, "input validation", None); raise NarrateFailed(job_dir, "input validation") from None
        except JobLockedError:
            raise RuntimeError("narration job is already running") from None
        return self._execute(job_dir, request, cancel_event, progress)

    def resume(self, job_dir: Path, *, cancel_event: object | None = None, progress: Callable[[str], object] | None = None) -> Path:
        job_dir = Path(job_dir).resolve(strict=True); manifest = self._store.load(job_dir, recover_interrupted=False)
        if manifest.mode is not JobMode.NARRATE: raise RuntimeError("job is not a narration job")
        if manifest.status is JobStatus.RUNNING: raise RuntimeError("narration job is already running")
        if manifest.status not in {JobStatus.PENDING, JobStatus.FAILED, JobStatus.INTERRUPTED}: raise RuntimeError("narration job cannot be resumed from its current status")
        try:
            request = self._request_from_manifest(manifest, job_dir); self._claim(job_dir, self._settings(request), {JobStatus.PENDING, JobStatus.FAILED, JobStatus.INTERRUPTED})
        except _InputInvalid:
            self._mark_failed(job_dir, None, "input validation", None); raise NarrateFailed(job_dir, "input validation") from None
        except JobLockedError:
            raise RuntimeError("narration job is already running") from None
        return self._execute(job_dir, request, cancel_event, progress)

    def _execute(self, job_dir: Path, request: NarrateRequest | StoryboardRequest, cancel_event: object | None, progress: Callable[[str], object] | None) -> Path:
        logger: logging.Logger | _NoopLogger = _NoopLogger(); current: str | None = None; category = "input validation"
        try:
            try: logger = self._logger_factory(job_dir)
            except Exception as exc: raise _LoggingFailure() from exc
            context, start = self._reconcile(job_dir, request)
            steps = self._steps(request.preview)
            for current in steps[start:]:
                category = self._category(current)
                self._run_step(job_dir, current, lambda name=current: self._operation(name, job_dir, request, context, cancel_event), cancel_event, progress, logger)
            self._validate_inputs(self._store.load(job_dir, recover_interrupted=False))
            expected_utterances = len(self._require_utterances(context))
            valid = self._artifacts_validator(job_dir, include_preview=request.preview, **({"expected_utterances": expected_utterances} if self._is_visual_request(request) else {}))
            if not valid: raise _OutputInvalid()
            self._store.update(job_dir, lambda manifest: (setattr(manifest, "status", JobStatus.SUCCEEDED), setattr(manifest, "last_error", None)))
            logger.info("job status=succeeded"); return job_dir
        except (ProcessCancelledError, KeyboardInterrupt, InterruptedError):
            self._mark_interrupted(job_dir, current); logger.info("job status=interrupted step=%s", current or "none")
            raise NarrateInterrupted(job_dir) from None
        except Exception as exc:
            failure = "job logging" if isinstance(exc, _LoggingFailure) else "output validation" if isinstance(exc, _OutputInvalid) else "VOICEVOX unavailable" if isinstance(exc, VoicevoxUnavailable) else "VOICEVOX synthesis" if isinstance(exc, VoicevoxSynthesisError) else "input validation" if isinstance(exc, _UnsafePath) else (category if current is not None else "input validation") if isinstance(exc, _InputInvalid) else category
            self._mark_failed(job_dir, current, failure, None); logger.error("step=%s category=%s exception=%s", current or "none", failure, type(exc).__name__)
            raise NarrateFailed(job_dir, failure) from None

    def _operation(self, name: str, job: Path, request: NarrateRequest | StoryboardRequest, context: dict[str, Any], cancel_event: object | None) -> None:
        if name == "probe-input": self._probe_step(job, request, context)
        elif name == "parse-script": self._parse_step(job, request, context)
        elif name == "synthesize-utterances": self._synthesis_step(job, context, cancel_event)
        elif name == "concat-audio": self._concat_step(job, context, cancel_event)
        elif name == "render-artifacts": self._artifacts_step(job, context)
        else: self._preview_step(job, request, context, cancel_event)

    def _probe_step(self, job: Path, request: NarrateRequest | StoryboardRequest, context: dict[str, Any]) -> None:
        if self._is_visual_request(request):
            self._visual_probe_step(job, request, context); return
        video = self._probe(request.input_path)
        if not isinstance(video, VideoInfo): raise _InputInvalid()
        payload: dict[str, Any] = {"duration_ms": video.duration_ms, "width": video.width, "height": video.height}
        if request.preview:
            try: font = self._font_resolver()
            except Exception as exc: raise _InputInvalid() from exc
            if not is_supported_japanese_font(font): raise _InputInvalid()
            ref = fingerprint_file(font.file.resolve(strict=True)); payload["preview_font"] = {"family": font.family, "path": ref.path, "sha256": ref.sha256, "size": ref.size, "mtime_ns": ref.mtime_ns}; context["font"] = font
        self._write_json(job, job / "work" / "video-info.json", payload)
        versions = self._tool_versions(); ffmpeg, ffprobe = getattr(versions, "ffmpeg", None), getattr(versions, "ffprobe", None)
        if not self._nonblank(ffmpeg) or not self._nonblank(ffprobe): raise _InputInvalid()
        self._store.update(job, lambda manifest: manifest.tools.update({"ffmpeg": ffmpeg, "ffprobe": ffprobe, "narrate-video-info-sha256": fingerprint_file(job / "work" / "video-info.json").sha256}))
        if not self._load_video(job, context, request.preview): raise _InputInvalid()

    def _parse_step(self, job: Path, request: NarrateRequest | StoryboardRequest, context: dict[str, Any]) -> None:
        if isinstance(request, StoryboardRequest):
            self._storyboard_parse_step(job, request, context); return
        expected = self._script_ref(job)
        snapshot = job / "inputs" / f"script{request.script_path.suffix.casefold()}"
        if not snapshot.exists(): snapshot_script(request.script_path, job / "inputs")
        if not snapshot_valid(snapshot, expected.sha256): raise _InputInvalid()
        utterances = self._parse(snapshot)
        if not self._valid_utterances(utterances): raise _InputInvalid()
        self._write_json(job, job / "work" / "utterances.json", {"script_sha256": expected.sha256, "utterances": [asdict(item) for item in utterances]})
        self._store.update(job, lambda manifest: manifest.tools.__setitem__("narrate-utterances-sha256", fingerprint_file(job / "work" / "utterances.json").sha256))
        context["utterances"] = utterances; context["script_sha256"] = expected.sha256

    def _synthesis_step(self, job: Path, context: dict[str, Any], cancel_event: object | None) -> None:
        self._validate_inputs(self._store.load(job, recover_interrupted=False))
        utterances = self._require_utterances(context); script_hash = context["script_sha256"]
        provenance = self._voicevox.preflight(cancel_event=cancel_event)
        if not isinstance(provenance, VoicevoxProvenance) or provenance.speaker_name != _SPEAKER or provenance.style_name != _STYLE: raise VoicevoxUnavailable()
        previous = self._provenance_payload(job)
        reuse = self._provenance_reusable(previous, provenance, script_hash, utterances)
        if not reuse:
            for staged_path in self._staged_wavs(job, utterances):
                staged_path.unlink(missing_ok=True)
        staged: list[Path] = []
        for utterance in utterances:
            self._raise_if_cancelled(cancel_event); path = job / "work" / "utterances" / f"utterance-{utterance.index:04d}.wav"
            reusable_wav = reuse and self._staged_hash_matches(previous, utterance.index, path) and self._valid_wav(path)
            if not reusable_wav:
                path.unlink(missing_ok=True)
                self._publish_wav(self._voicevox.synthesize(utterance.text, provenance.speaker_id, cancel_event=cancel_event), path)
            if not self._valid_wav(path): raise VoicevoxSynthesisError()
            staged.append(path)
        self._write_json(job, job / "work" / "voicevox-provenance.json", {**asdict(provenance), "script_sha256": script_hash, "utterance_indices": [item.index for item in utterances], "staged_wavs": [{"index": path_index, "sha256": fingerprint_file(path).sha256} for path_index, path in enumerate(staged, 1)]})
        self._store.update(job, lambda manifest: manifest.tools.__setitem__("narrate-voicevox-provenance-sha256", fingerprint_file(job / "work" / "voicevox-provenance.json").sha256))
        records: list[ArtifactRecord] = []
        for path in staged:
            output = job / "outputs" / "utterances" / path.name; self._safe_target(job, output, "outputs"); output.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path, output)
            except FileExistsError:
                try:
                    equivalent = os.path.samefile(path, output) or fingerprint_file(path).sha256 == fingerprint_file(output).sha256
                    if not equivalent:
                        raise _OutputInvalid()
                except OSError as exc:
                    raise _OutputInvalid() from exc
            records.append(fingerprint_artifact(job, output, "utterance-wav"))
        self._record(job, records)

    def _concat_step(self, job: Path, context: dict[str, Any], cancel_event: object | None) -> None:
        wavs = self._staged_wavs(job, self._require_utterances(context))
        destination = job / "outputs" / "narration.wav"
        staged = self._temporary_work_path(job, ".narration.wav")
        try:
            reported = self._concat(wavs, staged, silence_ms=300, cancel_event=cancel_event)
            if not isinstance(reported, WavInfo) or not isinstance(self._inspect_wav(staged), WavInfo): raise _InputInvalid()
            self._publish_output(job, staged, destination, "narration-wav")
            context["narration"] = self._inspect_wav(destination)
            if not isinstance(context["narration"], WavInfo): raise _InputInvalid()
            self._record(job, [fingerprint_artifact(job, destination, "narration-wav")])
        finally:
            staged.unlink(missing_ok=True)

    def _artifacts_step(self, job: Path, context: dict[str, Any]) -> None:
        utterances = self._require_utterances(context); wavs = [(path, self._inspect_wav(path)) for path in self._staged_wavs(job, utterances)]
        cues = build_timeline(utterances, wavs, silence_ms=300); narration = self._inspect_wav(job / "outputs" / "narration.wav")
        expected = sum((info.duration_seconds for _, info in wavs), Decimal(0)) + Decimal("0.3") * (len(wavs) - 1)
        if abs(self._ms(narration.duration_seconds) - self._ms(expected)) > 1: raise _InputInvalid()
        staged_dir = job / "work" / f".artifacts-{uuid4().hex}"; self._safe_target(job, staged_dir, "work"); staged_dir.mkdir()
        try:
            srt, vtt = self._subtitles(staged_dir, cues)
            if tuple(map(Path, (srt, vtt))) != (staged_dir / "subtitles.srt", staged_dir / "subtitles.vtt"): raise _InputInvalid()
            final_srt, final_vtt = job / "outputs" / "subtitles.srt", job / "outputs" / "subtitles.vtt"
            self._publish_output(job, srt, final_srt, "subtitles-srt"); self._publish_output(job, vtt, final_vtt, "subtitles-vtt")
        finally:
            for path in staged_dir.glob("*"): path.unlink(missing_ok=True)
            staged_dir.rmdir()
        if self._visual_context(context):
            self._record(job, [fingerprint_artifact(job, final_srt, "subtitles-srt"), fingerprint_artifact(job, final_vtt, "subtitles-vtt")])
            self._write_visual_plan(job, context, cues)
            return
        video = self._require_video(context); warning = job / "work" / "duration-warning.json"; self._safe_target(job, warning, "work")
        if self._ms(narration.duration_seconds) > video.duration_ms: self._write_json(job, warning, {"source_duration_ms": video.duration_ms, "narration_duration_ms": self._ms(narration.duration_seconds)})
        else: warning.unlink(missing_ok=True)
        self._record(job, [fingerprint_artifact(job, final_srt, "subtitles-srt"), fingerprint_artifact(job, final_vtt, "subtitles-vtt")])

    def _preview_step(self, job: Path, request: NarrateRequest | StoryboardRequest, context: dict[str, Any], cancel_event: object | None) -> None:
        narration = self._inspect_wav(job / "outputs" / "narration.wav"); target = job / "outputs" / "preview.mp4"; staged = self._temporary_work_path(job, ".preview.mp4")
        try:
            self._validate_inputs(self._store.load(job, recover_interrupted=False))
            if self._visual_context(context):
                music = self._load_music_runs(job, context)
                self._visual_preview_renderer(self._load_visual_plan(job, context), job / "outputs" / "narration.wav", job / "outputs" / "subtitles.srt", staged, self._ms(narration.duration_seconds), font=context.get("font"), cancel_event=cancel_event, **({"music": music} if music is not None else {}))
            else:
                self._preview_renderer(request.input_path, job / "outputs" / "narration.wav", job / "outputs" / "subtitles.srt", staged, self._require_video(context), self._ms(narration.duration_seconds), font=context.get("font"), cancel_event=cancel_event)
            self._publish_output(job, staged, target, "preview-mp4")
        finally:
            staged.unlink(missing_ok=True)
        self._record(job, [fingerprint_artifact(job, target, "preview-mp4")])

    def _visual_probe_step(self, job: Path, request: NarrateRequest | StoryboardRequest, context: dict[str, Any]) -> None:
        self._validate_inputs(self._store.load(job, recover_interrupted=False))
        if isinstance(request, StoryboardRequest):
            storyboard = parse_storyboard(request.input_path)
            clips = storyboard.clips
            variant = "storyboard"
            music = storyboard.music
        else:
            if not self._is_image(request.input_path):
                raise _InputInvalid()
            from .storyboard import StoryboardClip
            clips = (StoryboardClip("still", "image", request.input_path.resolve(strict=True), "still"),)
            variant = "still"
            music = None
        source_info: dict[Path, dict[str, Any]] = {}
        for clip in clips:
            if clip.source not in source_info:
                if clip.kind == "image":
                    info = self._image_probe(clip.source)
                    if not isinstance(info, ImageInfo):
                        raise _InputInvalid()
                    source_info[clip.source] = {"kind": "image", "source": str(clip.source), "width": info.width, "height": info.height}
                else:
                    info = self._probe(clip.source)
                    if not isinstance(info, VideoInfo):
                        raise _InputInvalid()
                    source_info[clip.source] = {"kind": "video", "source": str(clip.source), "width": info.width, "height": info.height, "duration_ms": info.duration_ms}
            if clip.kind == "video":
                start, end = clip.trim_start_ms or 0, clip.trim_end_ms or source_info[clip.source]["duration_ms"]
                if start >= end or end > source_info[clip.source]["duration_ms"]:
                    raise _InputInvalid()
        payload: dict[str, Any] = {"version": 1, "variant": variant, "sources": list(source_info.values())}
        if music is not None:
            music_sources: list[dict[str, Any]] = []
            for track in music.tracks:
                if any(item["source"] == str(track.source) for item in music_sources): continue
                info = self._music_probe(track.source)
                if not isinstance(info, MediaInfo) or not info.has_audio: raise _InputInvalid()
                music_sources.append({"source": str(track.source), "duration_ms": info.duration_ms})
            payload["music_sources"] = music_sources
        if request.preview:
            try: font = self._font_resolver()
            except Exception as exc: raise _InputInvalid() from exc
            if not is_supported_japanese_font(font): raise _InputInvalid()
            ref = fingerprint_file(font.file.resolve(strict=True)); payload["preview_font"] = {"family": font.family, "path": ref.path, "sha256": ref.sha256, "size": ref.size, "mtime_ns": ref.mtime_ns}; context["font"] = font
        self._write_json(job, job / "work" / "visual-input.json", payload)
        versions = self._tool_versions(); ffmpeg, ffprobe = getattr(versions, "ffmpeg", None), getattr(versions, "ffprobe", None)
        if not self._nonblank(ffmpeg) or not self._nonblank(ffprobe): raise _InputInvalid()
        self._store.update(job, lambda manifest: manifest.tools.update({"ffmpeg": ffmpeg, "ffprobe": ffprobe, "narrate-visual-input-sha256": fingerprint_file(job / "work" / "visual-input.json").sha256}))
        context["visual"] = {"variant": variant, "clips": clips, "sources": source_info, "music": music}

    def _storyboard_parse_step(self, job: Path, request: StoryboardRequest, context: dict[str, Any]) -> None:
        from .storyboard import snapshot_storyboard
        descriptor = self._descriptor_ref(job)
        snapshot = job / "inputs" / "storyboard.json"
        if not snapshot.exists(): snapshot_storyboard(request.input_path, job / "inputs")
        if not snapshot_valid(snapshot, descriptor.sha256): raise _InputInvalid()
        storyboard = parse_storyboard(snapshot, source_base=request.input_path.resolve(strict=True).parent)
        mapped = storyboard_utterances(storyboard)
        utterances = tuple(item for group in mapped for item in group.utterances)
        if not self._valid_utterances(utterances): raise _InputInvalid()
        self._write_json(job, job / "work" / "utterances.json", {"descriptor_sha256": descriptor.sha256, "utterances": [asdict(item) for item in utterances], "clips": [{"id": group.clip.id, "utterance_indices": [item.index for item in group.utterances]} for group in mapped]})
        self._store.update(job, lambda manifest: manifest.tools.__setitem__("narrate-utterances-sha256", fingerprint_file(job / "work" / "utterances.json").sha256))
        context["utterances"], context["script_sha256"], context["clip_utterances"] = utterances, descriptor.sha256, mapped

    def _load_visual_input(self, job: Path, context: dict[str, Any], request: NarrateRequest | StoryboardRequest) -> bool:
        try:
            if not self._state_fingerprint_valid(job, job / "work" / "visual-input.json", "narrate-visual-input-sha256"): return False
            payload = json.loads((job / "work" / "visual-input.json").read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("variant") not in {"still", "storyboard"} or not isinstance(payload.get("sources"), list): return False
            if request.preview:
                font = payload.get("preview_font")
                if not isinstance(font, dict) or set(font) != {"family", "path", "sha256", "size", "mtime_ns"}: return False
                ref = fingerprint_file(Path(font["path"]));
                if (ref.sha256, ref.size, ref.mtime_ns) != (font["sha256"], font["size"], font["mtime_ns"]): return False
                context["font"] = FontChoice(font["family"], Path(font["path"]))
            music = None
            if isinstance(request, StoryboardRequest):
                storyboard = parse_storyboard(request.input_path); clips, music = storyboard.clips, storyboard.music
            else:
                from .storyboard import StoryboardClip
                clips = (StoryboardClip("still", "image", request.input_path.resolve(strict=True), "still"),)
            probed_music = {item["source"] for item in payload.get("music_sources", [])}
            if probed_music != ({str(track.source) for track in music.tracks} if music is not None else set()): return False
            context["visual"] = {"variant": payload["variant"], "clips": clips, "sources": {Path(item["source"]): item for item in payload["sources"]}, "music": music}
            return True
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError): return False

    def _write_visual_plan(self, job: Path, context: dict[str, Any], cues: Sequence[Any]) -> None:
        visual = context.get("visual"); mapped = context.get("clip_utterances")
        if not isinstance(visual, dict): raise _InputInvalid()
        if mapped is None:
            from .storyboard import ClipUtterances
            mapped = (ClipUtterances(visual["clips"][0], tuple(cues)),)
        cue_by_index = {cue.index: cue for cue in cues}
        plan: list[dict[str, Any]] = []
        for number, group in enumerate(mapped):
            indices = [item.index for item in group.utterances]
            first, last = cue_by_index[indices[0]], cue_by_index[indices[-1]]
            end = cue_by_index[mapped[number + 1].utterances[0].index].start_ms if number + 1 < len(mapped) else last.end_ms
            clip = group.clip; source = visual["sources"].get(clip.source)
            if not isinstance(source, dict): raise _InputInvalid()
            item: dict[str, Any] = {"id": clip.id, "kind": clip.kind, "source": str(clip.source), "source_sha256": fingerprint_file(clip.source).sha256, "start_ms": first.start_ms, "end_ms": end, "utterance_start": indices[0], "utterance_end": indices[-1]}
            if clip.kind == "video": item.update({"trim_start_ms": clip.trim_start_ms or 0, "trim_end_ms": clip.trim_end_ms or source["duration_ms"]})
            plan.append(item)
        payload: dict[str, Any] = {"version": 1, "clips": plan}
        music = visual.get("music")
        if music is not None:
            tracks = [group.clip.music or music.default for group in mapped]
            runs = plan_music_runs([(track, item["start_ms"], item["end_ms"]) for track, item in zip(tracks, plan)], crossfade_ms=music.crossfade_ms)
            for run in runs:
                track = music.track(run["track"])
                run.update({"source": str(track.source), "source_sha256": fingerprint_file(track.source).sha256, "gain_db": track.gain_db})
            payload["music"] = {"crossfade_ms": music.crossfade_ms, "runs": list(runs)}
        path = job / "work" / "visual-plan.json"
        if path.exists():
            if not self._load_visual_plan(job, context): raise _OutputInvalid()
            return
        self._write_json(job, path, payload)
        self._store.update(job, lambda manifest: manifest.tools.__setitem__("narrate-visual-plan-sha256", fingerprint_file(path).sha256))

    def _load_visual_plan(self, job: Path, context: dict[str, Any]) -> list[dict[str, object]]:
        try:
            path = job / "work" / "visual-plan.json"
            if not self._state_fingerprint_valid(job, path, "narrate-visual-plan-sha256"): raise ValueError
            payload = json.loads(path.read_text(encoding="utf-8")); clips = payload.get("clips") if isinstance(payload, dict) and payload.get("version") == 1 else None
            if not isinstance(clips, list) or not clips: raise ValueError
            for clip in clips:
                if not isinstance(clip, dict) or set(clip) - {"id", "kind", "source", "source_sha256", "start_ms", "end_ms", "utterance_start", "utterance_end", "trim_start_ms", "trim_end_ms"}: raise ValueError
                if fingerprint_file(Path(clip["source"])).sha256 != clip["source_sha256"]: raise ValueError
            return clips
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise _InputInvalid() from None

    def _load_music_runs(self, job: Path, context: dict[str, Any]) -> list[dict[str, object]] | None:
        try:
            path = job / "work" / "visual-plan.json"
            if not self._state_fingerprint_valid(job, path, "narrate-visual-plan-sha256"): raise ValueError
            music = json.loads(path.read_text(encoding="utf-8")).get("music")
            declared = context["visual"].get("music") is not None
            if music is None:
                if declared: raise ValueError
                return None
            runs = music["runs"] if declared and isinstance(music, dict) and set(music) == {"crossfade_ms", "runs"} else None
            if not isinstance(runs, list) or not runs: raise ValueError
            for run in runs:
                if not isinstance(run, dict) or set(run) != {"track", "source", "source_sha256", "gain_db", "start_ms", "end_ms", "fade_in_ms", "fade_out_ms"}: raise ValueError
                if fingerprint_file(Path(run["source"])).sha256 != run["source_sha256"]: raise ValueError
            return runs
        except (OSError, ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
            raise _InputInvalid() from None

    def _reconcile(self, job: Path, request: NarrateRequest | StoryboardRequest) -> tuple[dict[str, Any], int]:
        self._safe_roots(job); context: dict[str, Any] = {}; steps = self._steps(request.preview); manifest = self._store.load(job, recover_interrupted=False)
        validators = {"probe-input": lambda: self._load_visual_input(job, context, request) if self._is_visual_request(request) else self._load_video(job, context, request.preview), "parse-script": lambda: self._load_utterances(job, context), "synthesize-utterances": lambda: self._load_synthesis(job, context), "concat-audio": lambda: self._valid_wav(job / "outputs" / "narration.wav") and self._artifact_valid(job, job / "outputs" / "narration.wav", "narration-wav"), "render-artifacts": lambda: self._render_artifacts_valid(job, context), "render-preview": lambda: self._preview_artifact_valid(job, context)}
        for index, name in enumerate(steps):
            record = manifest.steps.get(name)
            valid = validators[name]()
            if record is None or record.status is not StepStatus.SUCCEEDED or not valid:
                if self._is_visual_request(request) and record is not None and record.status is StepStatus.SUCCEEDED and name in {"probe-input", "parse-script", "render-artifacts"}:
                    # Visual descriptors, normalized associations and plans are
                    # evidence, not disposable cache: never silently adopt edits.
                    raise _InputInvalid()
                if record is not None and record.status is StepStatus.SUCCEEDED and self._invalid_producing_output(job, name, context):
                    self._store.update(job, lambda latest: self._reset_from(latest, steps, index))
                    raise _OutputInvalid()
                if record is not None and record.status is StepStatus.SUCCEEDED and name in {"render-artifacts", "render-preview"} and any((job / "outputs").rglob("*")):
                    raise _OutputInvalid()
                self._store.update(job, lambda latest: self._reset_from(latest, steps, index)); return context, index
        return context, len(steps)

    def _run_step(self, job: Path, name: str, operation: Callable[[], None], cancel_event: object | None, progress: Callable[[str], object] | None, logger: logging.Logger | _NoopLogger) -> None:
        self._store.update(job, lambda manifest: setattr(manifest, "steps", {**manifest.steps, name: StepRecord(status=StepStatus.RUNNING, started_at=self._now())})); logger.info("step=%s status=running", name)
        self._raise_if_cancelled(cancel_event)
        if progress is not None: progress(name)
        operation(); self._raise_if_cancelled(cancel_event)
        def succeed(manifest: JobManifest) -> None:
            previous = manifest.steps[name]; manifest.steps[name] = StepRecord(status=StepStatus.SUCCEEDED, started_at=previous.started_at, finished_at=self._now(), exit_code=0)
        self._store.update(job, succeed); logger.info("step=%s status=succeeded", name)

    def _claim(self, job: Path, expected: dict[str, Any], allowed: set[JobStatus]) -> None:
        def claim(manifest: JobManifest) -> None:
            if manifest.status not in allowed or manifest.mode is not JobMode.NARRATE: raise _InputInvalid()
            self._validate_inputs(manifest); self._safe_roots(job)
            if not self._settings_match(manifest.settings, expected): raise _InputInvalid()
            manifest.status, manifest.last_error = JobStatus.RUNNING, None
        self._store.update(job, claim)

    def _load_video(self, job: Path, context: dict[str, Any], preview: bool) -> bool:
        try:
            if not self._state_fingerprint_valid(job, job / "work" / "video-info.json", "narrate-video-info-sha256"): return False
            data = json.loads((job / "work" / "video-info.json").read_text(encoding="utf-8")); keys = {"duration_ms", "width", "height"} | ({"preview_font"} if preview else set())
            if set(data) != keys: return False
            context["video"] = VideoInfo(data["duration_ms"], data["width"], data["height"])
            if preview:
                font = data["preview_font"]; ref = fingerprint_file(Path(font["path"]))
                if set(font) != {"family", "path", "sha256", "size", "mtime_ns"} or ref.sha256 != font["sha256"] or ref.size != font["size"] or ref.mtime_ns != font["mtime_ns"]: return False
                context["font"] = FontChoice(font["family"], Path(font["path"]))
                if not is_supported_japanese_font(context["font"]): return False
            return True
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError): return False

    def _load_utterances(self, job: Path, context: dict[str, Any]) -> bool:
        try:
            if not self._state_fingerprint_valid(job, job / "work" / "utterances.json", "narrate-utterances-sha256"): return False
            data = json.loads((job / "work" / "utterances.json").read_text(encoding="utf-8")); visual = self._is_visual_manifest(job)
            expected = self._descriptor_ref(job).sha256 if visual and self._is_storyboard_manifest(job) else self._script_ref(job).sha256
            expected_keys = {"descriptor_sha256", "utterances", "clips"} if visual and self._is_storyboard_manifest(job) else {"script_sha256", "utterances"}
            digest_key = "descriptor_sha256" if visual and self._is_storyboard_manifest(job) else "script_sha256"
            if set(data) != expected_keys or data[digest_key] != expected or not isinstance(data["utterances"], list): return False
            utterances = tuple(Utterance(**item) for item in data["utterances"])
            if not self._valid_utterances(utterances): return False
            if visual and self._is_storyboard_manifest(job):
                if not snapshot_valid(job / "inputs" / "storyboard.json", expected): return False
                storyboard = parse_storyboard(Path(self._descriptor_ref(job).path))
                mapped = storyboard_utterances(storyboard)
                if data["clips"] != [{"id": group.clip.id, "utterance_indices": [item.index for item in group.utterances]} for group in mapped]: return False
                context["clip_utterances"] = mapped
            context["utterances"], context["script_sha256"] = utterances, expected; return True
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError): return False

    def _load_synthesis(self, job: Path, context: dict[str, Any]) -> bool:
        try:
            utterances = self._require_utterances(context); data = self._provenance_payload(job)
            if data is None or not self._state_fingerprint_valid(job, job / "work" / "voicevox-provenance.json", "narrate-voicevox-provenance-sha256"): return False
            current = self._voicevox.preflight()
            if not isinstance(current, VoicevoxProvenance) or not self._provenance_reusable(data, current, context["script_sha256"], utterances): return False
            return all(self._staged_hash_matches(data, utterance.index, path) and self._valid_wav(path) and self._artifact_valid(job, job / "outputs" / "utterances" / path.name, "utterance-wav") for utterance, path in zip(utterances, self._staged_wavs(job, utterances), strict=True))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError): return False

    def _load_provenance(self, job: Path) -> VoicevoxProvenance | None:
        try:
            data = self._provenance_payload(job)
            if data is None: return None
            return VoicevoxProvenance(data["engine_version"], data["speaker_name"], data["style_name"], data["speaker_id"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError): return None

    @staticmethod
    def _provenance_payload(job: Path) -> dict[str, Any] | None:
        try:
            data = json.loads((job / "work" / "voicevox-provenance.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _provenance_reusable(data: dict[str, Any] | None, provenance: VoicevoxProvenance | None, script_sha256: object, utterances: Sequence[Utterance]) -> bool:
        if data is None or provenance is None or not isinstance(script_sha256, str): return False
        expected = {**asdict(provenance), "script_sha256": script_sha256, "utterance_indices": [item.index for item in utterances], "staged_wavs": [{"index": item.index, "sha256": ""} for item in utterances]}
        if set(data) != set(expected) or any(data[key] != expected[key] for key in ("engine_version", "speaker_name", "style_name", "speaker_id", "script_sha256", "utterance_indices")): return False
        staged = data["staged_wavs"]
        if not isinstance(staged, list) or len(staged) != len(utterances): return False
        return all(isinstance(item, dict) and set(item) == {"index", "sha256"} and item["index"] == utterance.index and isinstance(item["sha256"], str) and len(item["sha256"]) == 64 for item, utterance in zip(staged, utterances, strict=True))

    @staticmethod
    def _staged_hash_matches(data: dict[str, Any] | None, index: int, path: Path) -> bool:
        try:
            if data is None or not isinstance(data.get("staged_wavs"), list): return False
            matching = [item for item in data["staged_wavs"] if isinstance(item, dict) and item.get("index") == index]
            return len(matching) == 1 and isinstance(matching[0].get("sha256"), str) and fingerprint_file(path).sha256 == matching[0]["sha256"]
        except (OSError, ValueError):
            return False

    def _state_fingerprint_valid(self, job: Path, path: Path, key: str) -> bool:
        try:
            manifest = self._store.load(job, recover_interrupted=False); expected = manifest.tools.get(key)
            return isinstance(expected, str) and fingerprint_file(path).sha256 == expected
        except (OSError, ValueError):
            return False

    def _artifact_valid(self, job: Path, path: Path, kind: str) -> bool:
        try:
            relative = Path(path).resolve(strict=True).relative_to(Path(job).resolve(strict=True)).as_posix()
            manifest = self._store.load(job, recover_interrupted=False)
            records = [item for item in manifest.artifacts if item.path == relative and item.kind == kind]
            return len(records) == 1 and fingerprint_artifact(job, path, kind) == records[0]
        except (OSError, ValueError):
            return False

    def _render_artifacts_valid(self, job: Path, context: dict[str, Any]) -> bool:
        extra = {"expected_utterances": len(self._require_utterances(context))} if self._visual_context(context) else {}
        try: valid = self._artifacts_validator(job, include_preview=False, **extra)
        except (TypeError, _InputInvalid): return False
        if self._visual_context(context):
            try: self._load_visual_plan(job, context)
            except _InputInvalid: return False
        return valid and self._artifact_valid(job, job / "outputs" / "subtitles.srt", "subtitles-srt") and self._artifact_valid(job, job / "outputs" / "subtitles.vtt", "subtitles-vtt")

    def _preview_artifact_valid(self, job: Path, context: dict[str, Any]) -> bool:
        extra = {"expected_utterances": len(self._require_utterances(context))} if self._visual_context(context) else {}
        try: valid = self._artifacts_validator(job, include_preview=True, **extra)
        except (TypeError, _InputInvalid): return False
        return valid and self._render_artifacts_valid(job, context) and self._artifact_valid(job, job / "outputs" / "preview.mp4", "preview-mp4")

    def _publish_output(self, job: Path, staged: Path, destination: Path, kind: str) -> None:
        self._safe_target(job, staged, "work"); self._safe_target(job, destination, "outputs")
        try:
            os.link(staged, destination)
        except FileExistsError:
            try:
                unchanged = fingerprint_file(staged).sha256 == fingerprint_file(destination).sha256
            except (OSError, ValueError):
                unchanged = False
            if not unchanged and not self._artifact_valid(job, destination, kind): raise _OutputInvalid() from None

    def _invalid_producing_output(self, job: Path, name: str, context: dict[str, Any]) -> bool:
        if name == "synthesize-utterances":
            try:
                return any(path.exists() and not self._artifact_valid(job, job / "outputs" / "utterances" / path.name, "utterance-wav") for path in self._staged_wavs(job, self._require_utterances(context)))
            except _InputInvalid:
                return False
        if name == "concat-audio":
            path = job / "outputs" / "narration.wav"
            return path.exists() and not self._artifact_valid(job, path, "narration-wav")
        return False

    def _record(self, job: Path, records: Sequence[ArtifactRecord]) -> None:
        def record(manifest: JobManifest) -> None:
            paths = {item.path for item in records}; manifest.artifacts = [item for item in manifest.artifacts if item.path not in paths]; manifest.artifacts.extend(records)
        self._store.update(job, record)

    def _reset_from(self, manifest: JobManifest, steps: tuple[str, ...], index: int) -> None:
        for name in steps[index:]: manifest.steps.pop(name, None)
        if "synthesize-utterances" in steps[index:]: manifest.artifacts = []
        elif "concat-audio" in steps[index:]: manifest.artifacts = [item for item in manifest.artifacts if item.kind == "utterance-wav"]
        elif "render-artifacts" in steps[index:]: manifest.artifacts = [item for item in manifest.artifacts if item.kind in {"utterance-wav", "narration-wav"}]

    def _mark_failed(self, job: Path, current: str | None, category: str, exit_code: int | None) -> None:
        def fail(manifest: JobManifest) -> None:
            manifest.status, manifest.last_error = JobStatus.FAILED, category
            if current is not None:
                prior = manifest.steps.get(current); manifest.steps[current] = StepRecord(status=StepStatus.FAILED, started_at=prior.started_at if prior else self._now(), finished_at=self._now(), exit_code=exit_code, error=category)
        self._store.update(job, fail)

    def _mark_interrupted(self, job: Path, current: str | None) -> None:
        def interrupt(manifest: JobManifest) -> None:
            manifest.status = JobStatus.INTERRUPTED
            if current is not None:
                prior = manifest.steps.get(current); manifest.steps[current] = StepRecord(status=StepStatus.INTERRUPTED, started_at=prior.started_at if prior else self._now(), finished_at=self._now())
        self._store.update(job, interrupt)

    @staticmethod
    def _settings(request: NarrateRequest | StoryboardRequest) -> dict[str, Any]:
        if isinstance(request, StoryboardRequest):
            return {"speaker_name": _SPEAKER, "style_name": _STYLE, "speed_scale": 1.0, "silence_ms": 300, "max_utterance_codepoints": 60, "input_kind": "storyboard", "preview": request.preview}
        if NarrateService._is_image(request.input_path):
            return {"speaker_name": _SPEAKER, "style_name": _STYLE, "speed_scale": 1.0, "silence_ms": 300, "max_utterance_codepoints": 60, "script_format": request.script_path.suffix.casefold(), "input_kind": "image", "preview": request.preview}
        return {"speaker_name": _SPEAKER, "style_name": _STYLE, "speed_scale": 1.0, "silence_ms": 300, "max_utterance_codepoints": 60, "script_format": request.script_path.suffix.casefold(), "preview": request.preview}
    @staticmethod
    def _settings_match(actual: object, expected: dict[str, Any]) -> bool:
        return isinstance(actual, dict) and set(actual) == set(expected) and all(type(actual[key]) is type(expected[key]) and actual[key] == expected[key] for key in expected)
    @staticmethod
    def _steps(preview: bool) -> tuple[str, ...]: return (*_STEPS, *((_PREVIEW,) if preview else ()))
    @staticmethod
    def _category(name: str) -> str: return {"probe-input": "input validation", "parse-script": "script parsing", "synthesize-utterances": "VOICEVOX synthesis", "concat-audio": "FFmpeg", "render-artifacts": "artifact rendering", "render-preview": "preview"}[name]
    @staticmethod
    def _now() -> str: return datetime.now(UTC).isoformat()
    @staticmethod
    def _ms(seconds: Decimal) -> int: return int((seconds * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    @staticmethod
    def _nonblank(value: object) -> bool: return isinstance(value, str) and bool(value.strip())
    @staticmethod
    def _valid_utterances(value: object) -> bool: return isinstance(value, tuple) and bool(value) and all(isinstance(item, Utterance) and item.index == index for index, item in enumerate(value, 1))
    def _valid_wav(self, path: Path) -> bool:
        try: return isinstance(self._inspect_wav(path), WavInfo)
        except (OSError, ValueError, RuntimeError): return False
    @staticmethod
    def _raise_if_cancelled(event: object | None) -> None:
        if event is not None and bool(getattr(event, "is_set")()): raise ProcessCancelledError("narration")
    @staticmethod
    def _require_video(context: dict[str, Any]) -> VideoInfo:
        if not isinstance(context.get("video"), VideoInfo): raise _InputInvalid()
        return context["video"]
    @staticmethod
    def _require_utterances(context: dict[str, Any]) -> tuple[Utterance, ...]:
        if not NarrateService._valid_utterances(context.get("utterances")): raise _InputInvalid()
        return context["utterances"]
    @staticmethod
    def _is_image(path: Path) -> bool:
        return Path(path).suffix.casefold() in {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
    @staticmethod
    def _is_visual_request(request: NarrateRequest | StoryboardRequest) -> bool:
        return isinstance(request, StoryboardRequest) or (isinstance(request, NarrateRequest) and NarrateService._is_image(request.input_path))
    @staticmethod
    def _visual_context(context: dict[str, Any]) -> bool:
        return isinstance(context.get("visual"), dict)
    def _is_storyboard_manifest(self, job: Path) -> bool:
        try: return self._store.load(job, recover_interrupted=False).settings.get("input_kind") == "storyboard"
        except (OSError, ValueError): return False
    def _is_visual_manifest(self, job: Path) -> bool:
        try: return self._store.load(job, recover_interrupted=False).settings.get("input_kind") in {"image", "storyboard"}
        except (OSError, ValueError): return False
    def _staged_wavs(self, job: Path, utterances: Sequence[Utterance]) -> list[Path]:
        paths = [job / "work" / "utterances" / f"utterance-{item.index:04d}.wav" for item in utterances]
        for path in paths: self._safe_target(job, path, "work")
        return paths
    def _write_json(self, job: Path, path: Path, payload: dict[str, Any]) -> None:
        self._safe_target(job, path, "work"); temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp"); self._safe_target(job, temporary, "work")
        path.parent.mkdir(parents=True, exist_ok=True)
        try: temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"); os.replace(temporary, path)
        finally: temporary.unlink(missing_ok=True)
    @staticmethod
    def _require_absent(*paths: Path) -> None:
        if any(path.exists() for path in paths): raise _OutputInvalid()
    def _temporary_work_path(self, job: Path, suffix: str) -> Path:
        path = job / "work" / f".{uuid4().hex}{suffix}"; self._safe_target(job, path, "work"); return path
    @staticmethod
    def _safe_roots(job: Path) -> None:
        root = Path(job).resolve(strict=True)
        for name in ("inputs", "work", "outputs"):
            child = (root / name).resolve(strict=True)
            if child == root or not child.is_relative_to(root): raise _InputInvalid()
    @staticmethod
    def _safe_target(job: Path, path: Path, root_name: str) -> None:
        root = (Path(job) / root_name).resolve(strict=True)
        candidate = Path(path)
        parent = candidate.parent.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        if not parent.is_relative_to(root) or not resolved_candidate.is_relative_to(root): raise _UnsafePath()
        if candidate.exists() and not candidate.resolve(strict=True).is_relative_to(root): raise _UnsafePath()
    def _script_ref(self, job: Path) -> InputRef:
        manifest = self._store.load(job, recover_interrupted=False)
        if len(manifest.inputs) != 2: raise _InputInvalid()
        return manifest.inputs[1]
    def _descriptor_ref(self, job: Path) -> InputRef:
        manifest = self._store.load(job, recover_interrupted=False)
        if not manifest.inputs: raise _InputInvalid()
        return manifest.inputs[0]
    def _request_from_manifest(self, manifest: JobManifest, job: Path) -> NarrateRequest | StoryboardRequest:
        try:
            preview = manifest.settings["preview"]
            if type(preview) is not bool: raise ValueError
            if manifest.settings.get("input_kind") == "storyboard":
                request = StoryboardRequest(Path(manifest.inputs[0].path), manifest.name, job.parent, preview)
            else:
                if len(manifest.inputs) != 2: raise ValueError
                request = NarrateRequest(Path(manifest.inputs[0].path), Path(manifest.inputs[1].path), manifest.name, job.parent, preview)
            self._validate_request(request); return request
        except (KeyError, TypeError, ValueError): raise _InputInvalid() from None
    @staticmethod
    def _validate_request(request: NarrateRequest | StoryboardRequest) -> None:
        if isinstance(request, StoryboardRequest):
            if request.input_path.suffix.casefold() != ".json": raise ValueError("invalid narration request")
            if parse_storyboard(request.input_path).music is not None and not request.preview: raise StoryboardMusicRequiresPreview()
            return
        if not isinstance(request, NarrateRequest) or request.script_path.suffix.casefold() not in {".txt", ".md"}: raise ValueError("invalid narration request")
    @staticmethod
    def _validate_inputs(manifest: JobManifest) -> None:
        storyboard = manifest.settings.get("input_kind") == "storyboard"
        if (storyboard and len(manifest.inputs) < 2) or (not storyboard and len(manifest.inputs) != 2): raise _InputInvalid()
        try:
            if any(fingerprint_file(Path(item.path)) != item for item in manifest.inputs): raise _InputInvalid()
        except (OSError, ValueError): raise _InputInvalid() from None
