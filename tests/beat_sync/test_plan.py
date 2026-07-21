from pathlib import Path

import pytest

from minoru_studio.beat_sync.media import MaterialSource
from minoru_studio.beat_sync.models import BeatAnalysis, MaterialKind
from minoru_studio.beat_sync.plan import (
    build_plan,
    estimate_material_count,
    resolve_every_n,
)


def test_auto_interval_uses_half_up_rounding_and_clamps():
    assert resolve_every_n(5, 2, "auto") == 3
    assert resolve_every_n(100, 2, "auto") == 16
    assert resolve_every_n(8, 3, 2) == 2


def test_estimate_material_count_rounds_up_partial_groups():
    cut_points_ms = list(range(0, 500 * 10, 500))  # 9 intervals
    assert estimate_material_count(cut_points_ms, 4) == 3
    assert estimate_material_count(cut_points_ms, 3) == 3
    assert estimate_material_count(cut_points_ms, 9) == 1


def test_estimate_material_count_rejects_bad_every_n():
    with pytest.raises(ValueError, match="every_n"):
        estimate_material_count([0, 500, 1000], "auto")
    with pytest.raises(ValueError, match="every_n"):
        estimate_material_count([0, 500, 1000], 0)


def test_estimate_material_count_requires_two_points():
    with pytest.raises(ValueError, match="cut_points_ms"):
        estimate_material_count([0], 4)


def test_plan_references_ordered_job_inputs():
    analysis = BeatAnalysis(2_000, 120.0, (500, 1_000), (0, 500, 1_000, 2_000))
    materials = [
        MaterialSource(Path("b.mov"), MaterialKind.VIDEO),
        MaterialSource(Path("a.jpg"), MaterialKind.PHOTO),
    ]
    plan = build_plan("job", analysis, materials, "auto", "random", "Demo")
    assert [item.input_index for item in plan.materials] == [1, 2]
    assert plan.settings.every_n_resolved == 2
