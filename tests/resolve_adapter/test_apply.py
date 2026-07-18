import pytest

from minoru_studio_resolve.gateway import ResolveGateway
from minoru_studio_resolve.job_io import ApplicationStore
from minoru_studio_resolve.service import AdapterError, AdapterService
from tests.resolve_adapter.fakes import FakeResolve


def make_service(resolve):
    return AdapterService(
        ResolveGateway(resolve),
        ApplicationStore(),
        attempt_ids=iter(["attempt-1", "attempt-2"]),
        operation_tokens=iter(
            [
                "operation-1",
                "operation-2",
                "operation-3",
                "operation-4",
                "operation-5",
                "operation-6",
                "operation-7",
                "operation-8",
            ]
        ),
    )


@pytest.fixture
def ready_mixed_service(prepared_mixed_job):
    resolve = FakeResolve()
    service = make_service(resolve)
    service.start(str(prepared_mixed_job))
    detail = service.resume(str(prepared_mixed_job))
    assert detail["state"] == "ready"
    return resolve, service, prepared_mixed_job


@pytest.fixture
def applied_service(ready_mixed_service):
    resolve, service, job_dir = ready_mixed_service
    service.apply_ready(str(job_dir), confirm=lambda summary: True)
    return resolve, service, job_dir


def test_cancel_keeps_ready_and_creates_no_final_timeline(
    ready_mixed_service,
):
    resolve, service, job_dir = ready_mixed_service
    detail = service.apply_ready(str(job_dir), confirm=lambda summary: False)
    assert detail["state"] == "ready"
    assert not resolve.project.final_timeline_ids


def test_apply_places_bgm_visuals_and_blue_markers(ready_mixed_service):
    resolve, service, job_dir = ready_mixed_service
    detail = service.apply_ready(str(job_dir), confirm=lambda summary: True)
    assert detail["state"] == "applied"
    timeline = resolve.project.final_timelines[0]
    assert timeline.audio_track_items[1]
    assert timeline.video_track_items[1]
    assert all(marker["color"] == "Blue" for marker in timeline.markers)
    assert detail["result"]["placed"] >= 1


def test_reapply_uses_unique_timeline_name(applied_service):
    resolve, service, job_dir = applied_service
    service.start(str(job_dir), new_attempt=True)
    second = service.resume(str(job_dir))
    assert second["state"] == "ready"
    service.apply_ready(str(job_dir), confirm=lambda summary: True)
    assert [
        timeline.GetName() for timeline in resolve.project.final_timelines
    ] == ["Demo", "Demo-002"]


def test_mid_apply_failure_keeps_partial_timeline_and_fails_attempt(
    ready_mixed_service,
):
    resolve, service, job_dir = ready_mixed_service
    resolve.project.media_pool.fail_visual_append_after = 1
    with pytest.raises(AdapterError):
        service.apply_ready(str(job_dir), confirm=lambda summary: True)
    detail = service.latest_detail(str(job_dir))
    assert detail["state"] == "failed"
    assert detail["timeline"]["id"] in resolve.project.final_timeline_ids
