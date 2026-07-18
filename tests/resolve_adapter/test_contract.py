import json
from pathlib import Path

import pytest

from minoru_studio.beat_sync.models import (
    BeatAnalysis,
    BeatSyncPlan,
    BeatSyncSettings,
    MaterialKind,
    MaterialPlan,
    save_plan,
)
from minoru_studio.jobs.model import JobMode, JobStatus
from minoru_studio.jobs.store import JobStore, fingerprint_artifact
from resolve_adapter.minoru_studio_resolve.contract import (
    ContractError,
    load_validated_job,
)


def prepared_job(tmp_path):
    music = tmp_path / "song.wav"
    photo = tmp_path / "photo.jpg"
    music.write_bytes(b"audio")
    photo.write_bytes(b"photo")
    store = JobStore()
    job_dir = store.create(
        tmp_path / "jobs",
        "demo",
        JobMode.BEAT_SYNC,
        [music, photo],
    )
    manifest = store.load(job_dir, recover_interrupted=False)
    plan = BeatSyncPlan(
        1,
        manifest.job_id,
        0,
        BeatAnalysis(2_000, 120.0, (500, 1_000), (0, 500, 1_000, 2_000)),
        BeatSyncSettings("auto", 1, "asc", "Demo"),
        (MaterialPlan(1, MaterialKind.PHOTO, 0),),
    )
    path = job_dir / "outputs" / "beat-sync-plan.json"
    save_plan(path, plan)
    artifact = fingerprint_artifact(job_dir, path, "beat-sync-plan")

    def finish(latest):
        latest.status = JobStatus.SUCCEEDED
        latest.artifacts = [artifact]

    store.update(job_dir, finish)
    return job_dir


def test_valid_contract_rechecks_all_hashes(tmp_path):
    job_dir = prepared_job(tmp_path)
    loaded = load_validated_job(job_dir)
    assert loaded["plan"]["job_id"] == loaded["manifest"]["job_id"]
    assert len(loaded["inputs"]) == 2


def test_tampered_input_is_rejected_before_resolve(tmp_path):
    job_dir = prepared_job(tmp_path)
    manifest = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    input_path = Path(manifest["inputs"][1]["path"])
    input_path.write_bytes(b"changed")
    with pytest.raises(ContractError, match="input changed"):
        load_validated_job(job_dir)


def test_escaping_artifact_path_is_rejected(tmp_path):
    job_dir = prepared_job(tmp_path)
    manifest_path = job_dir / "job.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ContractError, match="escapes job"):
        load_validated_job(job_dir)
