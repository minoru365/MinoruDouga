import json
import os

import pytest

from minoru_studio.jobs.lock import (
    JobLock,
    JobLockedError,
    _pid_is_running,
    _require_mutex_acquired,
)


def test_pid_liveness_recognizes_the_current_process_without_terminating_it():
    assert _pid_is_running(os.getpid()) is True
    assert os.getpid() > 0


def test_pid_liveness_rejects_an_invalid_pid():
    assert _pid_is_running(0) is False


def test_lock_is_exclusive_and_release_is_token_safe(tmp_path):
    job_dir = tmp_path / "demo.media-job"
    job_dir.mkdir()
    with JobLock(job_dir):
        with pytest.raises(JobLockedError):
            JobLock(job_dir).acquire()
        payload = json.loads((job_dir / "job.lock").read_text(encoding="utf-8"))
        assert payload["pid"] > 0
        assert payload["token"]
    assert not (job_dir / "job.lock").exists()


def test_dead_pid_lock_is_replaced(tmp_path, monkeypatch):
    job_dir = tmp_path / "demo.media-job"
    job_dir.mkdir()
    (job_dir / "job.lock").write_text(
        json.dumps({"pid": 999999, "token": "old"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("minoru_studio.jobs.lock._pid_is_running", lambda pid: False)
    with JobLock(job_dir):
        payload = json.loads((job_dir / "job.lock").read_text(encoding="utf-8"))
        assert payload["token"] != "old"


def test_release_does_not_delete_externally_replaced_metadata(tmp_path):
    job_dir = tmp_path / "demo.media-job"
    job_dir.mkdir()
    lock = JobLock(job_dir)
    lock.acquire()
    replacement = {"pid": 999999, "token": "external"}
    (job_dir / "job.lock").write_text(json.dumps(replacement), encoding="utf-8")
    lock.release()
    assert json.loads((job_dir / "job.lock").read_text(encoding="utf-8")) == replacement


def test_unexpected_mutex_wait_result_raises():
    with pytest.raises(RuntimeError, match="unexpected mutex wait result"):
        _require_mutex_acquired(0xDEADBEEF)
