from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from minoru_studio.beat_sync.analyzer import LibrosaBeatAnalyzer
from minoru_studio.beat_sync.media import (
    MaterialSource,
    discover_materials,
    order_materials,
    validate_music_file,
)
from minoru_studio.beat_sync.models import MaterialKind, save_plan
from minoru_studio.beat_sync.plan import build_plan
from minoru_studio.jobs.model import JobMode, JobStatus, StepRecord, StepStatus
from minoru_studio.jobs.store import (
    JobStore,
    fingerprint_artifact,
    fingerprint_file,
)
from minoru_studio.logging_utils import configure_job_logger


@dataclass(frozen=True, slots=True)
class BeatSyncRequest:
    music: Path
    media_dir: Path
    every_n_requested: str | int
    order_mode: str
    timeline_name: str
    name: str
    output_dir: Path


class PreparationFailed(RuntimeError):
    def __init__(self, job_dir, cause):
        self.job_dir = Path(job_dir)
        self.cause = cause
        super().__init__(f"beat-sync preparation failed in {job_dir}: {cause}")


def _now():
    return datetime.now(UTC).isoformat()


class BeatSyncService:
    def __init__(self, store=None, analyzer=None, shuffler=None):
        self.store = store or JobStore()
        self.analyzer = analyzer or LibrosaBeatAnalyzer()
        self.shuffler = shuffler

    def _create_pending(self, request):
        music = validate_music_file(request.music)
        materials = order_materials(
            discover_materials(request.media_dir),
            request.order_mode,
            self.shuffler,
        )
        timeline_name = request.timeline_name.strip()
        if not timeline_name:
            raise ValueError("timeline name must not be empty")
        return self.store.create(
            request.output_dir,
            request.name,
            JobMode.BEAT_SYNC,
            [music, *(item.path for item in materials)],
            {
                "every_n_requested": request.every_n_requested,
                "order_mode": request.order_mode,
                "timeline_name": timeline_name,
                "material_kinds": [item.kind.value for item in materials],
            },
        )

    def create_and_prepare(self, request):
        return self._prepare(self._create_pending(request))

    def resume(self, job_dir):
        job_dir = Path(job_dir).resolve(strict=True)
        manifest = self.store.load(job_dir, recover_interrupted=True)
        if manifest.mode is not JobMode.BEAT_SYNC:
            raise ValueError("job mode is not beat-sync")
        if manifest.status not in {JobStatus.PENDING, JobStatus.INTERRUPTED}:
            raise ValueError(f"job cannot resume from {manifest.status.value}")
        for expected in manifest.inputs:
            if fingerprint_file(Path(expected.path)) != expected:
                raise ValueError(f"input changed: {expected.path}")
        return self._prepare(job_dir)

    def _prepare(self, job_dir):
        logger = configure_job_logger(job_dir)

        def start(manifest):
            manifest.status = JobStatus.RUNNING
            manifest.last_error = None
            manifest.steps["prepare"] = StepRecord(
                status=StepStatus.RUNNING,
                started_at=_now(),
            )

        self.store.update(job_dir, start)
        try:
            manifest = self.store.load(job_dir, recover_interrupted=False)
            analysis = self.analyzer.analyze(Path(manifest.inputs[0].path))
            materials = [
                MaterialSource(
                    Path(manifest.inputs[index + 1].path),
                    MaterialKind(kind),
                )
                for index, kind in enumerate(manifest.settings["material_kinds"])
            ]
            plan = build_plan(
                manifest.job_id,
                analysis,
                materials,
                manifest.settings["every_n_requested"],
                manifest.settings["order_mode"],
                manifest.settings["timeline_name"],
            )
            plan_path = job_dir / "outputs" / "beat-sync-plan.json"
            save_plan(plan_path, plan)
            artifact = fingerprint_artifact(
                job_dir,
                plan_path,
                "beat-sync-plan",
            )

            def succeed(latest):
                latest.status = JobStatus.SUCCEEDED
                latest.last_error = None
                latest.artifacts = [
                    item
                    for item in latest.artifacts
                    if item.kind != "beat-sync-plan"
                ] + [artifact]
                step = latest.steps["prepare"]
                step.status = StepStatus.SUCCEEDED
                step.finished_at = _now()
                step.exit_code = 0
                step.error = None

            self.store.update(job_dir, succeed)
            logger.info("beat-sync plan prepared: %s", plan_path)
            return Path(job_dir)
        except Exception as exc:
            logger.exception("beat-sync preparation failed")

            def fail(latest):
                latest.status = JobStatus.FAILED
                latest.last_error = str(exc)
                step = latest.steps.get("prepare") or StepRecord()
                latest.steps["prepare"] = step
                step.status = StepStatus.FAILED
                step.finished_at = _now()
                step.exit_code = 1
                step.error = str(exc)

            self.store.update(job_dir, fail)
            raise PreparationFailed(job_dir, exc) from exc
