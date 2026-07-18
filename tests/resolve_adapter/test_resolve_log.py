import json

import pytest

from minoru_studio_resolve.gateway import ResolveGateway
from minoru_studio_resolve.job_io import ApplicationStore
from minoru_studio_resolve.service import AdapterError, AdapterService
from tests.resolve_adapter.fakes import FakeResolve


def make_service(resolve):
    return AdapterService(
        ResolveGateway(resolve),
        ApplicationStore(),
        attempt_ids=iter(["attempt-1"]),
        operation_tokens=iter(
            ["operation-1", "operation-2", "operation-3"]
        ),
    )


def read_events(job_dir):
    path = job_dir / "logs" / "resolve.log"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_successful_start_records_local_application_identity(
    prepared_photo_job,
):
    service = make_service(FakeResolve())
    detail = service.start(str(prepared_photo_job))
    events = read_events(prepared_photo_job)
    completed = events[-1]
    assert completed["event"] == "start.completed"
    assert completed["attempt_id"] == detail["attempt_id"]
    assert completed["state"] == "ready"
    assert completed["bin_id"] == detail["bin"]["id"]
    assert "items" not in completed


def test_cancel_records_ready_without_a_final_timeline(prepared_mixed_job):
    service = make_service(FakeResolve())
    service.start(str(prepared_mixed_job))
    service.resume(str(prepared_mixed_job))
    service.apply_ready(str(prepared_mixed_job), confirm=lambda summary: False)
    event = read_events(prepared_mixed_job)[-1]
    assert event["event"] == "apply.cancelled"
    assert event["state"] == "ready"
    assert event["timeline_id"] is None


def test_apply_failure_records_traceback_and_partial_timeline(
    prepared_mixed_job,
):
    resolve = FakeResolve()
    service = make_service(resolve)
    service.start(str(prepared_mixed_job))
    service.resume(str(prepared_mixed_job))
    resolve.project.media_pool.fail_visual_append_after = 1
    with pytest.raises(AdapterError):
        service.apply_ready(
            str(prepared_mixed_job),
            confirm=lambda summary: True,
        )
    event = read_events(prepared_mixed_job)[-1]
    assert event["event"] == "apply.failed"
    assert event["state"] == "failed"
    assert event["timeline_id"] in resolve.project.final_timeline_ids
    assert "GatewayError" in event["traceback"]
