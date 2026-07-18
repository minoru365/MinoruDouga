from minoru_studio.beat_sync.models import BeatAnalysis, load_plan
from minoru_studio.beat_sync.service import BeatSyncRequest, BeatSyncService
from minoru_studio.jobs.store import JobStore


class FixedAnalyzer:
    def analyze(self, path):
        return BeatAnalysis(
            4_000,
            120.0,
            (500, 1_000, 1_500, 2_000, 2_500, 3_000, 3_500),
            (0, 500, 1_000, 1_500, 2_000, 2_500, 3_000, 3_500, 4_000),
        )


def test_prepared_job_is_resolve_independent(tmp_path):
    music = tmp_path / "song.wav"
    music.write_bytes(b"audio")
    media = tmp_path / "media"
    media.mkdir()
    (media / "01.jpg").write_bytes(b"photo")
    (media / "02.mov").write_bytes(b"video")
    job_dir = BeatSyncService(analyzer=FixedAnalyzer()).create_and_prepare(
        BeatSyncRequest(
            music,
            media,
            "auto",
            "asc",
            "Demo",
            "demo",
            tmp_path / "jobs",
        )
    )
    manifest = JobStore().load(job_dir, recover_interrupted=False)
    plan = load_plan(job_dir / "outputs" / "beat-sync-plan.json")
    assert manifest.status.value == "succeeded"
    assert all(isinstance(value, int) for value in plan.analysis.beats_ms)
    assert [item.input_index for item in plan.materials] == [1, 2]
    assert plan.analysis.cut_points_ms[-1] == plan.analysis.duration_ms
