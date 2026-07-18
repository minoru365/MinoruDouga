import pytest

from minoru_studio.beat_sync.models import (
    BeatAnalysis,
    BeatSyncPlan,
    BeatSyncSettings,
    MaterialKind,
    MaterialPlan,
    PlanError,
    load_plan,
    plan_from_dict,
    plan_to_dict,
    save_plan,
)


def sample_plan():
    return BeatSyncPlan(
        schema_version=1,
        job_id="job-123",
        audio_input_index=0,
        analysis=BeatAnalysis(
            duration_ms=2_000,
            bpm=120.0,
            beats_ms=(500, 1_000, 1_500),
            cut_points_ms=(0, 500, 1_000, 1_500, 2_000),
            minimum_cut_ms=150,
        ),
        settings=BeatSyncSettings("auto", 1, "asc", "Demo"),
        materials=(
            MaterialPlan(1, MaterialKind.PHOTO, 0),
            MaterialPlan(2, MaterialKind.VIDEO, 1),
        ),
    )


def test_plan_round_trip_and_atomic_write(tmp_path):
    plan = sample_plan()
    assert plan_from_dict(plan_to_dict(plan)) == plan
    path = tmp_path / "outputs" / "beat-sync-plan.json"
    save_plan(path, plan)
    assert load_plan(path) == plan
    assert not list(path.parent.glob("*.tmp"))


def test_float_milliseconds_are_rejected():
    data = plan_to_dict(sample_plan())
    data["analysis"]["duration_ms"] = 2_000.0
    with pytest.raises(PlanError, match="duration_ms"):
        plan_from_dict(data)


def test_cut_points_must_span_zero_to_duration():
    data = plan_to_dict(sample_plan())
    data["analysis"]["cut_points_ms"] = [100, 500, 2_000]
    with pytest.raises(PlanError, match="cut_points_ms"):
        plan_from_dict(data)
