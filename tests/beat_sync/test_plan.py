from pathlib import Path

from minoru_studio.beat_sync.media import MaterialSource
from minoru_studio.beat_sync.models import BeatAnalysis, MaterialKind
from minoru_studio.beat_sync.plan import build_plan, resolve_every_n


def test_auto_interval_uses_half_up_rounding_and_clamps():
    assert resolve_every_n(5, 2, "auto") == 3
    assert resolve_every_n(100, 2, "auto") == 16
    assert resolve_every_n(8, 3, 2) == 2


def test_plan_references_ordered_job_inputs():
    analysis = BeatAnalysis(2_000, 120.0, (500, 1_000), (0, 500, 1_000, 2_000))
    materials = [
        MaterialSource(Path("b.mov"), MaterialKind.VIDEO),
        MaterialSource(Path("a.jpg"), MaterialKind.PHOTO),
    ]
    plan = build_plan("job", analysis, materials, "auto", "random", "Demo")
    assert [item.input_index for item in plan.materials] == [1, 2]
    assert plan.settings.every_n_resolved == 2
