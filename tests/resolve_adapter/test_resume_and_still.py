from minoru_studio_resolve.gateway import ResolveGateway
from minoru_studio_resolve.job_io import ApplicationStore
from minoru_studio_resolve.service import AdapterService
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


def test_mixed_job_reads_marks_on_second_invocation(prepared_mixed_job):
    resolve = FakeResolve()
    service = make_service(resolve)
    service.start(str(prepared_mixed_job))
    video = resolve.project.media_pool.video_items[0]
    video.marks = {"video": {"in": 10, "out": 39}}
    detail = service.resume(str(prepared_mixed_job))
    assert detail["source_windows"][0]["mark_in_frame"] == 10
    assert detail["source_windows"][0]["mark_out_frame_exclusive"] == 40


def test_still_mismatch_deletes_only_probe_and_waits(prepared_photo_job):
    resolve = FakeResolve(still_duration=60)
    service = make_service(resolve)
    detail = service.start(str(prepared_photo_job))
    assert detail["state"] == "awaiting_still_setting"
    assert detail["still"]["actual_frames"] == 60
    assert detail["still"]["required_frames"] != 60
    assert resolve.project.deleted_timeline_ids == ["probe-attempt-1"]
    assert not resolve.project.final_timeline_ids
    assert resolve.project.current_timeline_id == "sentinel"


def test_still_retry_reimports_only_photos(prepared_mixed_job):
    resolve = FakeResolve(still_duration=60)
    service = make_service(resolve)
    service.start(str(prepared_mixed_job))
    first = service.resume(str(prepared_mixed_job))
    assert first["state"] == "awaiting_still_setting"
    resolve.project.media_pool.still_duration = first["still"][
        "required_frames"
    ]
    second = service.resume(str(prepared_mixed_job))
    assert second["state"] == "ready"
    assert resolve.project.media_pool.photo_import_batch_sizes == [1, 1]
    assert resolve.project.media_pool.video_import_calls == 1
