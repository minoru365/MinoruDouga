import sys
from pathlib import Path

import pytest


ADAPTER_ROOT = Path(__file__).parents[2] / "resolve_adapter"
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from minoru_studio.beat_sync.models import (  # noqa: E402
    BeatAnalysis,
    BeatSyncPlan,
    BeatSyncSettings,
    MaterialKind,
    MaterialPlan,
    save_plan,
)
from minoru_studio.jobs.model import JobMode, JobStatus  # noqa: E402
from minoru_studio.jobs.store import (  # noqa: E402
    JobStore,
    fingerprint_artifact,
)


def build_prepared_job(tmp_path, kinds):
    music = tmp_path / "song.wav"
    music.write_bytes(b"audio")
    paths = []
    for index, kind in enumerate(kinds, start=1):
        suffix = ".jpg" if kind is MaterialKind.PHOTO else ".mov"
        path = tmp_path / ("material-{0}{1}".format(index, suffix))
        path.write_bytes(kind.value.encode("ascii"))
        paths.append(path)
    store = JobStore()
    job_dir = store.create(
        tmp_path / "jobs",
        "demo",
        JobMode.BEAT_SYNC,
        [music] + paths,
    )
    manifest = store.load(job_dir, recover_interrupted=False)
    plan = BeatSyncPlan(
        1,
        manifest.job_id,
        0,
        BeatAnalysis(
            2_000,
            120.0,
            (500, 1_000, 1_500),
            (0, 500, 1_000, 1_500, 2_000),
        ),
        BeatSyncSettings("auto", 1, "asc", "Demo"),
        tuple(
            MaterialPlan(index + 1, kind, index)
            for index, kind in enumerate(kinds)
        ),
    )
    plan_path = job_dir / "outputs" / "beat-sync-plan.json"
    save_plan(plan_path, plan)
    artifact = fingerprint_artifact(job_dir, plan_path, "beat-sync-plan")

    def finish(latest):
        latest.status = JobStatus.SUCCEEDED
        latest.artifacts = [artifact]

    store.update(job_dir, finish)
    return job_dir


def build_prepared_transcribe_job(tmp_path, suffix=".mp4"):
    source = tmp_path / ("source" + suffix)
    source.write_bytes(b"video")
    store = JobStore()
    job_dir = store.create(
        tmp_path / "jobs",
        "transcribe demo",
        JobMode.TRANSCRIBE,
        [source],
    )
    records = []
    for kind, name in (
        ("transcript-txt", "transcript.txt"),
        ("subtitles-srt", "subtitles.srt"),
        ("subtitles-vtt", "subtitles.vtt"),
    ):
        path = job_dir / "outputs" / name
        path.write_bytes(kind.encode("ascii"))
        records.append(fingerprint_artifact(job_dir, path, kind))

    def finish(latest):
        latest.status = JobStatus.SUCCEEDED
        latest.artifacts = records

    store.update(job_dir, finish)
    return job_dir


def build_prepared_narrate_job(tmp_path):
    video = tmp_path / "source.mp4"
    script = tmp_path / "script.txt"
    video.write_bytes(b"video")
    script.write_bytes(b"approved script")
    store = JobStore()
    job_dir = store.create(
        tmp_path / "jobs",
        "narrate demo",
        JobMode.NARRATE,
        [video, script],
    )
    records = []
    for kind, name in (
        ("narration-wav", "narration.wav"),
        ("subtitles-srt", "subtitles.srt"),
        ("subtitles-vtt", "subtitles.vtt"),
    ):
        path = job_dir / "outputs" / name
        path.write_bytes(kind.encode("ascii"))
        records.append(fingerprint_artifact(job_dir, path, kind))

    def finish(latest):
        latest.status = JobStatus.SUCCEEDED
        latest.artifacts = records

    store.update(job_dir, finish)
    return job_dir


@pytest.fixture
def prepared_photo_job(tmp_path):
    return build_prepared_job(tmp_path, (MaterialKind.PHOTO,))


@pytest.fixture
def prepared_mixed_job(tmp_path):
    return build_prepared_job(
        tmp_path,
        (MaterialKind.PHOTO, MaterialKind.VIDEO),
    )


@pytest.fixture
def prepared_transcribe_job(tmp_path):
    return build_prepared_transcribe_job(tmp_path)


@pytest.fixture
def prepared_narrate_job(tmp_path):
    return build_prepared_narrate_job(tmp_path)
