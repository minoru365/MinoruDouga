import json
from datetime import datetime

import pytest

from minoru_studio.jobs.lock import JobLock
from minoru_studio.jobs.model import (
    JobMode,
    JobStatus,
    StepRecord,
    StepStatus,
    manifest_to_dict,
)
from minoru_studio.jobs.store import (
    JobConflictError,
    JobStore,
    fingerprint_file,
    safe_job_name,
)


def test_safe_job_name_blocks_windows_path_syntax():
    assert safe_job_name(" Demo:One/Two. ") == "Demo_One_Two"


def test_safe_job_name_blocks_windows_reserved_stem_before_extension():
    assert safe_job_name("CON.txt") == "_CON.txt"


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


def test_save_write_failure_leaves_no_temporary_or_lock_file(tmp_path, monkeypatch):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    manifest = store.load(job_dir)
    original_write_text = type(job_dir).write_text

    def fail_manifest_temp(path, *args, **kwargs):
        if path.name.startswith(".job-"):
            raise OSError("simulated write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(type(job_dir), "write_text", fail_manifest_temp)
    with pytest.raises(OSError, match="simulated write failure"):
        store.save(job_dir, manifest)
    assert not list(job_dir.glob("*.tmp"))
    assert not (job_dir / "job.lock").exists()


def test_create_failure_removes_only_the_new_partial_job_directory(tmp_path, monkeypatch):
    store = JobStore()

    def fail_write(*args, **kwargs):
        raise OSError("simulated initialization failure")

    monkeypatch.setattr(store, "_write_manifest", fail_write)
    with pytest.raises(OSError, match="simulated initialization failure"):
        store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    assert not list(tmp_path.glob("demo*.media-job"))


def test_recovery_rereads_after_acquiring_lock_before_mutating(tmp_path, monkeypatch):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    manifest = store.load(job_dir)
    manifest.status = JobStatus.RUNNING
    store.save(job_dir, manifest)
    original_acquire = JobLock.acquire
    changed = False

    def update_then_acquire(lock):
        nonlocal changed
        if not changed:
            changed = True
            latest = store.load(job_dir, recover_interrupted=False)
            latest.status = JobStatus.SUCCEEDED
            (job_dir / "job.json").write_text(
                json.dumps(manifest_to_dict(latest)), encoding="utf-8"
            )
        return original_acquire(lock)

    monkeypatch.setattr(JobLock, "acquire", update_then_acquire)
    assert store.load(job_dir).status is JobStatus.SUCCEEDED


def test_save_rejects_stale_manifest_and_update_merges_after_reload(tmp_path):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    first = store.load(job_dir)
    stale = store.load(job_dir)
    first.settings["first"] = True
    store.save(job_dir, first)
    before_stale_save = (job_dir / "job.json").read_text(encoding="utf-8")

    stale.settings["second"] = True
    with pytest.raises(JobConflictError):
        store.save(job_dir, stale)
    assert (job_dir / "job.json").read_text(encoding="utf-8") == before_stale_save

    merged = store.update(job_dir, lambda manifest: manifest.settings.update(second=True))
    assert merged.settings == {"first": True, "second": True}


def test_update_mutator_error_does_not_write_and_releases_lock(tmp_path):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    before = (job_dir / "job.json").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="stop"):
        store.update(job_dir, lambda manifest: (_ for _ in ()).throw(ValueError("stop")))

    assert (job_dir / "job.json").read_text(encoding="utf-8") == before
    assert not (job_dir / "job.lock").exists()


def test_save_changes_updated_at_when_clock_matches_existing_token(tmp_path, monkeypatch):
    store = JobStore()
    job_dir = store.create(tmp_path, "demo", JobMode.TRANSCRIBE)
    manifest = store.load(job_dir)
    original_token = manifest.updated_at

    class FrozenDateTime:
        @classmethod
        def now(cls, timezone):
            return datetime.fromisoformat(original_token)

    monkeypatch.setattr("minoru_studio.jobs.store.datetime", FrozenDateTime)
    store.save(job_dir, manifest)

    assert manifest.updated_at != original_token
