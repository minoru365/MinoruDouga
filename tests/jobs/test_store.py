import json

from minoru_studio.jobs.model import JobMode, JobStatus, StepRecord, StepStatus
from minoru_studio.jobs.store import JobStore, fingerprint_file, safe_job_name


def test_safe_job_name_blocks_windows_path_syntax():
    assert safe_job_name(" Demo:One/Two. ") == "Demo_One_Two"


def test_create_uses_incrementing_directory_without_overwrite(tmp_path):
    store = JobStore()
    first = store.create(tmp_path, "demo", JobMode.BEAT_SYNC)
    second = store.create(tmp_path, "demo", JobMode.BEAT_SYNC)
    assert first.name == "demo.media-job"
    assert second.name == "demo-002.media-job"
    assert json.loads((first / "job.json").read_text(encoding="utf-8"))["status"] == "pending"


def test_fingerprint_records_absolute_path_and_sha256(tmp_path):
    source = tmp_path / "台本.txt"
    source.write_text("hello", encoding="utf-8")
    ref = fingerprint_file(source)
    assert ref.path == str(source.resolve())
    assert ref.size == 5
    assert ref.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_load_recovers_unlocked_running_state(tmp_path):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    manifest = store.load(job_dir)
    manifest.status = JobStatus.RUNNING
    manifest.steps["extract"] = StepRecord(status=StepStatus.RUNNING)
    store.save(job_dir, manifest)
    recovered = store.load(job_dir, recover_interrupted=True)
    assert recovered.status is JobStatus.INTERRUPTED
    assert recovered.steps["extract"].status is StepStatus.INTERRUPTED


def test_load_recovers_running_state_when_lock_pid_is_dead(tmp_path, monkeypatch):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    manifest = store.load(job_dir)
    manifest.status = JobStatus.RUNNING
    store.save(job_dir, manifest)
    (job_dir / "job.lock").write_text(
        json.dumps({"pid": 999999, "token": "dead"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("minoru_studio.jobs.lock._pid_is_running", lambda pid: False)
    assert store.load(job_dir).status is JobStatus.INTERRUPTED


def test_load_preserves_running_state_while_live_lock_is_held(tmp_path):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    manifest = store.load(job_dir)
    manifest.status = JobStatus.RUNNING
    store.save(job_dir, manifest)
    with store.locked(job_dir):
        assert store.load(job_dir).status is JobStatus.RUNNING
