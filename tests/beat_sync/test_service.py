import pytest

from minoru_studio.beat_sync.models import BeatAnalysis, load_plan
from minoru_studio.beat_sync.service import BeatSyncRequest, BeatSyncService
from minoru_studio.jobs.model import JobStatus
from minoru_studio.jobs.store import JobStore


class FakeAnalyzer:
    def analyze(self, path):
        return BeatAnalysis(
            2_000,
            120.0,
            (500, 1_000, 1_500),
            (0, 500, 1_000, 1_500, 2_000),
        )


def make_request(tmp_path):
    music = tmp_path / "song.wav"
    music.write_bytes(b"audio")
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.jpg").write_bytes(b"photo")
    return BeatSyncRequest(
        music,
        media,
        "auto",
        "asc",
        "Demo",
        "demo",
        tmp_path / "jobs",
    )


def test_service_creates_successful_registered_plan(tmp_path):
    service = BeatSyncService(analyzer=FakeAnalyzer())
    job_dir = service.create_and_prepare(make_request(tmp_path))
    manifest = JobStore().load(job_dir, recover_interrupted=False)
    assert manifest.status is JobStatus.SUCCEEDED
    assert manifest.steps["prepare"].status.value == "succeeded"
    assert manifest.artifacts[0].path == "outputs/beat-sync-plan.json"
    assert load_plan(job_dir / manifest.artifacts[0].path).job_id == manifest.job_id


def test_resume_rejects_changed_input(tmp_path):
    request = make_request(tmp_path)
    service = BeatSyncService(analyzer=FakeAnalyzer())
    job_dir = service._create_pending(request)
    (request.media_dir / "a.jpg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="input changed"):
        service.resume(job_dir)
