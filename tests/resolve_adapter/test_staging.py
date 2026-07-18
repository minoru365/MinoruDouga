from minoru_studio_resolve.gateway import ResolveGateway
from minoru_studio_resolve.job_io import ApplicationStore
from minoru_studio_resolve.service import AdapterService
from tests.resolve_adapter.fakes import FakeResolve


def test_stage_imports_photos_individually_and_waits_for_video(
    prepared_mixed_job,
):
    resolve = FakeResolve()
    service = AdapterService(
        ResolveGateway(resolve),
        ApplicationStore(),
        attempt_ids=iter(["attempt-1"]),
        operation_tokens=iter(["operation-1"]),
    )
    detail = service.start(str(prepared_mixed_job))
    assert detail["state"] == "awaiting_in_out"
    assert resolve.project.media_pool.photo_import_batch_sizes == [1]
    assert detail["bin"]["id"]
    assert len(detail["items"]) == 3
    assert not resolve.project.timelines


def test_photo_only_job_continues_to_checking_still(prepared_photo_job):
    resolve = FakeResolve()
    service = AdapterService(
        ResolveGateway(resolve),
        ApplicationStore(),
        attempt_ids=iter(["attempt-1"]),
        operation_tokens=iter(["operation-1"]),
    )
    detail = service.start(str(prepared_photo_job), stop_after_stage=True)
    assert detail["state"] == "checking_still"
