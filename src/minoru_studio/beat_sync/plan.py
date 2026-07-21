from __future__ import annotations

from minoru_studio.beat_sync.models import (
    PLAN_SCHEMA_VERSION,
    BeatSyncPlan,
    BeatSyncSettings,
    MaterialPlan,
    validate_plan,
)


def resolve_every_n(
    interval_count: int,
    material_count: int,
    requested: str | int,
) -> int:
    if interval_count <= 0 or material_count <= 0:
        raise ValueError("interval and material counts must be positive")
    if requested != "auto":
        if isinstance(requested, bool) or not isinstance(requested, int):
            raise ValueError("requested interval must be auto or integer")
        if not 1 <= requested <= 16:
            raise ValueError("requested interval must be 1..16")
        return requested
    rounded = (interval_count * 2 + material_count) // (2 * material_count)
    return max(1, min(16, rounded))


def estimate_material_count(cut_points_ms, every_n):
    if not isinstance(every_n, int) or isinstance(every_n, bool) or every_n < 1:
        raise ValueError("every_n must be a positive integer")
    interval_count = len(cut_points_ms) - 1
    if interval_count <= 0:
        raise ValueError("cut_points_ms must contain at least two points")
    return -(-interval_count // every_n)


def build_plan(
    job_id,
    analysis,
    materials,
    every_n_requested,
    order_mode,
    timeline_name,
):
    resolved = resolve_every_n(
        len(analysis.cut_points_ms) - 1,
        len(materials),
        every_n_requested,
    )
    plan = BeatSyncPlan(
        PLAN_SCHEMA_VERSION,
        job_id,
        0,
        analysis,
        BeatSyncSettings(
            every_n_requested,
            resolved,
            order_mode,
            timeline_name.strip(),
        ),
        tuple(
            MaterialPlan(index + 1, material.kind, index)
            for index, material in enumerate(materials)
        ),
    )
    validate_plan(plan)
    return plan
