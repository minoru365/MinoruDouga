import json

import pytest

from minoru_studio.jobs.model import JobMode
from minoru_studio.jobs.store import JobStore
from resolve_adapter.minoru_studio_resolve.job_io import (
    ApplicationBusy,
    ApplicationStore,
)


def test_create_writes_detail_and_manifest_summary(tmp_path):
    job_dir = JobStore().create(tmp_path, "demo", JobMode.BEAT_SYNC)
    store = ApplicationStore()
    detail = store.create(
        str(job_dir),
        "job-id",
        "project-id",
        "Project",
        attempt_id="attempt-1",
        now="2026-07-18T00:00:00+00:00",
    )
    assert detail["state"] == "staging"
    saved = json.loads(
        (job_dir / "resolve" / "applications" / "attempt-1.json").read_text()
    )
    assert saved["attempt_id"] == "attempt-1"
    manifest = json.loads((job_dir / "job.json").read_text())
    assert manifest["resolve_applications"][0]["state"] == "staging"


def test_claim_refuses_second_operation(tmp_path):
    job_dir = JobStore().create(tmp_path, "demo", JobMode.BEAT_SYNC)
    store = ApplicationStore()
    store.create(str(job_dir), "job", "project", "P", attempt_id="a", now="t")
    store.claim(str(job_dir), "a", "token-1")
    with pytest.raises(ApplicationBusy):
        store.claim(str(job_dir), "a", "token-2")
