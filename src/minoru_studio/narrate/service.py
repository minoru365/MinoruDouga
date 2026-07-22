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
from minoru_studio.transcribe.media import FontChoice, is_supported_japanese_font, read_media_tool_versions, resolve_japanese_font

from .artifacts import artifacts_valid, build_timeline, write_subtitles
from .media import concat_wavs, inspect_wav, probe_video, publish_wav, render_preview
from .models import NarrateRequest, Utterance, VideoInfo, VoicevoxProvenance, WavInfo
from .script import parse_script, snapshot_script, snapshot_valid
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


class NarrateInterrupted(RuntimeError):
    def __init__(self, job_dir: Path) -> None:
        self.job_dir = Path(job_dir)
        super().__init__(f"narration interrupted: {self.job_dir}")


class _InputInvalid(ValueError): pass
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
        parse: Callable[[Path], tuple[Utterance, ...]] = parse_script,
        voicevox: VoicevoxClient | object | None = None,
        publish_wav: Callable[[bytes, Path], WavInfo] = publish_wav,
        inspect_wav: Callable[[Path], WavInfo] = inspect_wav,
        concat: Callable[..., WavInfo] = concat_wavs,
        subtitles: Callable[[Path, Sequence[Any]], tuple[Path, Path]] = write_subtitles,
        preview_renderer: Callable[..., Path] = render_preview,
        artifacts_validator: Callable[..., bool] = artifacts_valid,
        font_resolver: Callable[[], FontChoice] = resolve_japanese_font,
        tool_versions: Callable[[], object] = read_media_tool_versions,
        logger_factory: Callable[[Path], logging.Logger] = configure_job_logger,
    ) -> None:
        self._store = store or JobStore(); self._probe = probe; self._parse = parse
        self._voicevox = voicevox or VoicevoxClient(); self._publish_wav = publish_wav
        self._inspect_wav = inspect_wav; self._concat = concat; self._subtitles = subtitles
        self._preview_renderer = preview_renderer; self._artifacts_validator = artifacts_validator
        self._font_resolver = font_resolver; self._tool_versions = tool_versions; self._logger_factory = logger_factory

    def create_and_run(self, request: NarrateRequest, *, cancel_event: object | None = None, progress: Callable[[str], object] | None = None) -> Path:
        self._validate_request(request)
        settings = self._settings(request)
        job_dir = self._store.create(request.output_dir, request.name, JobMode.NARRATE, (request.input_path, request.script_path), settings)
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

    def _execute(self, job_dir: Path, request: NarrateRequest, cancel_event: object | None, progress: Callable[[str], object] | None) -> Path:
        logger: logging.Logger | _NoopLogger = _NoopLogger(); current: str | None = None; category = "input validation"
        try:
            try: logger = self._logger_factory(job_dir)
            except Exception as exc: raise _LoggingFailure() from exc
            context, start = self._reconcile(job_dir, request)
            steps = self._steps(request.preview)
            for current in steps[start:]:
                category = self._category(current)
                self._run_step(job_dir, current, lambda name=current: self._operation(name, job_dir, request, context, cancel_event), cancel_event, progress, logger)
            if not self._artifacts_validator(job_dir, include_preview=request.preview): raise _OutputInvalid()
            self._store.update(job_dir, lambda manifest: (setattr(manifest, "status", JobStatus.SUCCEEDED), setattr(manifest, "last_error", None)))
            logger.info("job status=succeeded"); return job_dir
        except (ProcessCancelledError, KeyboardInterrupt, InterruptedError):
            self._mark_interrupted(job_dir, current); logger.info("job status=interrupted step=%s", current or "none")
            raise NarrateInterrupted(job_dir) from None
        except Exception as exc:
            failure = "job logging" if isinstance(exc, _LoggingFailure) else "output validation" if isinstance(exc, _OutputInvalid) else "VOICEVOX unavailable" if isinstance(exc, VoicevoxUnavailable) else "VOICEVOX synthesis" if isinstance(exc, VoicevoxSynthesisError) else "input validation" if isinstance(exc, _InputInvalid) else category
            self._mark_failed(job_dir, current, failure, None); logger.error("step=%s category=%s exception=%s", current or "none", failure, type(exc).__name__)
            raise NarrateFailed(job_dir, failure) from None

    def _operation(self, name: str, job: Path, request: NarrateRequest, context: dict[str, Any], cancel_event: object | None) -> None:
        if name == "probe-input": self._probe_step(job, request, context)
        elif name == "parse-script": self._parse_step(job, request, context)
        elif name == "synthesize-utterances": self._synthesis_step(job, context, cancel_event)
        elif name == "concat-audio": self._concat_step(job, context, cancel_event)
        elif name == "render-artifacts": self._artifacts_step(job, context)
        else: self._preview_step(job, request, context, cancel_event)

    def _probe_step(self, job: Path, request: NarrateRequest, context: dict[str, Any]) -> None:
        video = self._probe(request.input_path)
        if not isinstance(video, VideoInfo): raise _InputInvalid()
        payload: dict[str, Any] = {"duration_ms": video.duration_ms, "width": video.width, "height": video.height}
        if request.preview:
            try: font = self._font_resolver()
            except Exception as exc: raise _InputInvalid() from exc
            if not is_supported_japanese_font(font): raise _InputInvalid()
            ref = fingerprint_file(font.file.resolve(strict=True)); payload["preview_font"] = {"family": font.family, "path": ref.path, "sha256": ref.sha256, "size": ref.size, "mtime_ns": ref.mtime_ns}; context["font"] = font
        self._write_json(job / "work" / "video-info.json", payload)
        if not self._load_video(job, context, request.preview): raise _InputInvalid()
        versions = self._tool_versions(); ffmpeg, ffprobe = getattr(versions, "ffmpeg", None), getattr(versions, "ffprobe", None)
        if not self._nonblank(ffmpeg) or not self._nonblank(ffprobe): raise _InputInvalid()
        self._store.update(job, lambda manifest: manifest.tools.update({"ffmpeg": ffmpeg, "ffprobe": ffprobe}))

    def _parse_step(self, job: Path, request: NarrateRequest, context: dict[str, Any]) -> None:
        expected = self._script_ref(job)
        snapshot = job / "inputs" / f"script{request.script_path.suffix.casefold()}"
        if not snapshot.exists(): snapshot_script(request.script_path, job / "inputs")
        if not snapshot_valid(snapshot, expected.sha256): raise _InputInvalid()
        utterances = self._parse(snapshot)
        if not self._valid_utterances(utterances): raise _InputInvalid()
        self._write_json(job / "work" / "utterances.json", {"script_sha256": expected.sha256, "utterances": [asdict(item) for item in utterances]})
        context["utterances"] = utterances; context["script_sha256"] = expected.sha256

    def _synthesis_step(self, job: Path, context: dict[str, Any], cancel_event: object | None) -> None:
        utterances = self._require_utterances(context); script_hash = context["script_sha256"]
        provenance = self._voicevox.preflight(cancel_event=cancel_event)
        if not isinstance(provenance, VoicevoxProvenance) or provenance.speaker_name != _SPEAKER or provenance.style_name != _STYLE: raise VoicevoxUnavailable()
        old = self._load_provenance(job); reuse = old == provenance
        if not reuse:
            for staged_path in (job / "work" / "utterances").glob("utterance-*.wav"):
                staged_path.unlink(missing_ok=True)
        self._write_json(job / "work" / "voicevox-provenance.json", {**asdict(provenance), "script_sha256": script_hash})
        staged: list[Path] = []
        for utterance in utterances:
            self._raise_if_cancelled(cancel_event); path = job / "work" / "utterances" / f"utterance-{utterance.index:04d}.wav"
            if not (reuse and self._valid_wav(path)): self._publish_wav(self._voicevox.synthesize(utterance.text, provenance.speaker_id, cancel_event=cancel_event), path)
            if not self._valid_wav(path): raise VoicevoxSynthesisError()
            staged.append(path)
        records: list[ArtifactRecord] = []
        for path in staged:
            output = job / "outputs" / "utterances" / path.name; output.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path, output)
            except FileExistsError:
                try:
                    if not os.path.samefile(path, output):
                        raise _OutputInvalid()
                except OSError as exc:
                    raise _OutputInvalid() from exc
            records.append(fingerprint_artifact(job, output, "utterance-wav"))
        self._record(job, records)

    def _concat_step(self, job: Path, context: dict[str, Any], cancel_event: object | None) -> None:
        wavs = self._staged_wavs(job, self._require_utterances(context))
        context["narration"] = self._concat(wavs, job / "outputs" / "narration.wav", silence_ms=300, cancel_event=cancel_event)
        if not isinstance(context["narration"], WavInfo): raise _InputInvalid()
        self._record(job, [fingerprint_artifact(job, job / "outputs" / "narration.wav", "narration-wav")])

    def _artifacts_step(self, job: Path, context: dict[str, Any]) -> None:
        utterances = self._require_utterances(context); wavs = [(path, self._inspect_wav(path)) for path in self._staged_wavs(job, utterances)]
        cues = build_timeline(utterances, wavs, silence_ms=300); narration = self._inspect_wav(job / "outputs" / "narration.wav")
        expected = sum((info.duration_seconds for _, info in wavs), Decimal(0)) + Decimal("0.3") * (len(wavs) - 1)
        if abs(self._ms(narration.duration_seconds) - self._ms(expected)) > 1: raise _InputInvalid()
        srt, vtt = self._subtitles(job / "outputs", cues)
        if tuple(map(Path, (srt, vtt))) != (job / "outputs" / "subtitles.srt", job / "outputs" / "subtitles.vtt"): raise _InputInvalid()
        video = self._require_video(context); warning = job / "work" / "duration-warning.json"
        if self._ms(narration.duration_seconds) > video.duration_ms: self._write_json(warning, {"source_duration_ms": video.duration_ms, "narration_duration_ms": self._ms(narration.duration_seconds)})
        else: warning.unlink(missing_ok=True)
        self._record(job, [fingerprint_artifact(job, srt, "subtitles-srt"), fingerprint_artifact(job, vtt, "subtitles-vtt")])

    def _preview_step(self, job: Path, request: NarrateRequest, context: dict[str, Any], cancel_event: object | None) -> None:
        narration = self._inspect_wav(job / "outputs" / "narration.wav"); target = job / "outputs" / "preview.mp4"
        self._preview_renderer(request.input_path, job / "outputs" / "narration.wav", job / "outputs" / "subtitles.srt", target, self._require_video(context), self._ms(narration.duration_seconds), font=context.get("font"), cancel_event=cancel_event)
        self._record(job, [fingerprint_artifact(job, target, "preview-mp4")])

    def _reconcile(self, job: Path, request: NarrateRequest) -> tuple[dict[str, Any], int]:
        self._safe_roots(job); context: dict[str, Any] = {}; steps = self._steps(request.preview); manifest = self._store.load(job, recover_interrupted=False)
        validators = {"probe-input": lambda: self._load_video(job, context, request.preview), "parse-script": lambda: self._load_utterances(job, context), "synthesize-utterances": lambda: self._load_synthesis(job, context), "concat-audio": lambda: self._valid_wav(job / "outputs" / "narration.wav"), "render-artifacts": lambda: self._artifacts_validator(job, include_preview=False), "render-preview": lambda: self._artifacts_validator(job, include_preview=True)}
        for index, name in enumerate(steps):
            record = manifest.steps.get(name)
            valid = validators[name]()
            if record is None or record.status is not StepStatus.SUCCEEDED or not valid:
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
            data = json.loads((job / "work" / "utterances.json").read_text(encoding="utf-8")); expected = self._script_ref(job).sha256
            if set(data) != {"script_sha256", "utterances"} or data["script_sha256"] != expected or not isinstance(data["utterances"], list): return False
            utterances = tuple(Utterance(**item) for item in data["utterances"])
            if not self._valid_utterances(utterances): return False
            context["utterances"], context["script_sha256"] = utterances, expected; return True
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError): return False

    def _load_synthesis(self, job: Path, context: dict[str, Any]) -> bool:
        try:
            utterances = self._require_utterances(context); prov = self._load_provenance(job)
            if prov is None: return False
            data = json.loads((job / "work" / "voicevox-provenance.json").read_text(encoding="utf-8"))
            if data.get("script_sha256") != context["script_sha256"]: return False
            return all(self._valid_wav(path) for path in self._staged_wavs(job, utterances))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError): return False

    def _load_provenance(self, job: Path) -> VoicevoxProvenance | None:
        try:
            data = json.loads((job / "work" / "voicevox-provenance.json").read_text(encoding="utf-8")); return VoicevoxProvenance(data["engine_version"], data["speaker_name"], data["style_name"], data["speaker_id"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError): return None

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
    def _settings(request: NarrateRequest) -> dict[str, Any]: return {"speaker_name": _SPEAKER, "style_name": _STYLE, "speed_scale": 1.0, "silence_ms": 300, "max_utterance_codepoints": 60, "script_format": request.script_path.suffix.casefold(), "preview": request.preview}
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
    def _staged_wavs(job: Path, utterances: Sequence[Utterance]) -> list[Path]: return [job / "work" / "utterances" / f"utterance-{item.index:04d}.wav" for item in utterances]
    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try: temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"); os.replace(temporary, path)
        finally: temporary.unlink(missing_ok=True)
    @staticmethod
    def _safe_roots(job: Path) -> None:
        root = Path(job).resolve(strict=True)
        for name in ("inputs", "work", "outputs"):
            child = (root / name).resolve(strict=True)
            if child == root or not child.is_relative_to(root): raise _InputInvalid()
    def _script_ref(self, job: Path) -> InputRef:
        manifest = self._store.load(job, recover_interrupted=False)
        if len(manifest.inputs) != 2: raise _InputInvalid()
        return manifest.inputs[1]
    def _request_from_manifest(self, manifest: JobManifest, job: Path) -> NarrateRequest:
        try:
            if len(manifest.inputs) != 2: raise ValueError
            request = NarrateRequest(Path(manifest.inputs[0].path), Path(manifest.inputs[1].path), manifest.name, job.parent, manifest.settings["preview"])
            self._validate_request(request); return request
        except (KeyError, TypeError, ValueError): raise _InputInvalid() from None
    @staticmethod
    def _validate_request(request: NarrateRequest) -> None:
        if not isinstance(request, NarrateRequest) or request.script_path.suffix.casefold() not in {".txt", ".md"}: raise ValueError("invalid narration request")
    @staticmethod
    def _validate_inputs(manifest: JobManifest) -> None:
        if len(manifest.inputs) != 2: raise _InputInvalid()
        try:
            if any(fingerprint_file(Path(item.path)) != item for item in manifest.inputs): raise _InputInvalid()
        except (OSError, ValueError): raise _InputInvalid() from None
