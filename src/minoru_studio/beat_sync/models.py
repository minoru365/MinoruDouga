from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4


PLAN_SCHEMA_VERSION = 1


class PlanError(ValueError):
    pass


class MaterialKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class BeatAnalysis:
    duration_ms: int
    bpm: float
    beats_ms: tuple[int, ...]
    cut_points_ms: tuple[int, ...]
    minimum_cut_ms: int = 150


@dataclass(frozen=True, slots=True)
class BeatSyncSettings:
    every_n_requested: str | int
    every_n_resolved: int
    order_mode: str
    timeline_name: str


@dataclass(frozen=True, slots=True)
class MaterialPlan:
    input_index: int
    kind: MaterialKind
    order_index: int


@dataclass(frozen=True, slots=True)
class BeatSyncPlan:
    schema_version: int
    job_id: str
    audio_input_index: int
    analysis: BeatAnalysis
    settings: BeatSyncSettings
    materials: tuple[MaterialPlan, ...]
    mode: str = "beat-sync"


def _require_milliseconds(name: str, values: tuple[Any, ...]) -> None:
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PlanError(f"{name} values must be integer milliseconds")


def _require_strictly_increasing(name: str, values: tuple[int, ...]) -> None:
    if any(current >= following for current, following in zip(values, values[1:])):
        raise PlanError(f"{name} must be strictly increasing")


def validate_plan(plan: BeatSyncPlan) -> None:
    if plan.schema_version != PLAN_SCHEMA_VERSION:
        raise PlanError(f"schema_version must be {PLAN_SCHEMA_VERSION}")
    if plan.mode != "beat-sync":
        raise PlanError("mode must be beat-sync")
    if not isinstance(plan.job_id, str) or not plan.job_id.strip():
        raise PlanError("job_id must be non-empty")
    if isinstance(plan.audio_input_index, bool) or plan.audio_input_index != 0:
        raise PlanError("audio_input_index must be 0")

    analysis = plan.analysis
    _require_milliseconds("duration_ms", (analysis.duration_ms,))
    _require_milliseconds("minimum_cut_ms", (analysis.minimum_cut_ms,))
    _require_milliseconds("beats_ms", analysis.beats_ms)
    _require_milliseconds("cut_points_ms", analysis.cut_points_ms)
    if analysis.duration_ms <= 0:
        raise PlanError("duration_ms must be positive")
    if analysis.minimum_cut_ms <= 0:
        raise PlanError("minimum_cut_ms must be positive")
    try:
        bpm_is_valid = math.isfinite(analysis.bpm) and analysis.bpm > 0
    except TypeError as exc:
        raise PlanError("bpm must be finite and positive") from exc
    if not bpm_is_valid:
        raise PlanError("bpm must be finite and positive")
    _require_strictly_increasing("beats_ms", analysis.beats_ms)
    _require_strictly_increasing("cut_points_ms", analysis.cut_points_ms)
    if len(analysis.beats_ms) < 2:
        raise PlanError("beats_ms must contain at least two beats")
    if len(analysis.cut_points_ms) < 4:
        raise PlanError("cut_points_ms must contain at least two internal cut points")
    if (
        analysis.cut_points_ms[0] != 0
        or analysis.cut_points_ms[-1] != analysis.duration_ms
    ):
        raise PlanError("cut_points_ms must span 0 to duration_ms")
    if any(
        following - current < analysis.minimum_cut_ms
        for current, following in zip(
            analysis.cut_points_ms,
            analysis.cut_points_ms[1:],
        )
    ):
        raise PlanError("cut_points_ms intervals must meet minimum_cut_ms")

    settings = plan.settings
    requested = settings.every_n_requested
    if requested != "auto" and (
        isinstance(requested, bool)
        or not isinstance(requested, int)
        or not 1 <= requested <= 16
    ):
        raise PlanError("every_n_requested must be auto or an integer from 1 to 16")
    if (
        isinstance(settings.every_n_resolved, bool)
        or not isinstance(settings.every_n_resolved, int)
        or not 1 <= settings.every_n_resolved <= 16
    ):
        raise PlanError("every_n_resolved must be an integer from 1 to 16")
    if settings.order_mode not in {"asc", "random"}:
        raise PlanError("order_mode must be asc or random")
    if not isinstance(settings.timeline_name, str) or not settings.timeline_name.strip():
        raise PlanError("timeline_name must be non-blank")

    if not plan.materials:
        raise PlanError("materials must be non-empty")
    input_indices = [material.input_index for material in plan.materials]
    if any(
        isinstance(index, bool) or not isinstance(index, int) or index <= 0
        for index in input_indices
    ):
        raise PlanError("material input_index values must be positive integers")
    if len(set(input_indices)) != len(input_indices):
        raise PlanError("material input_index values must be unique")
    order_indices = [material.order_index for material in plan.materials]
    if order_indices != list(range(len(plan.materials))):
        raise PlanError("material order_index values must be contiguous")


def plan_to_dict(plan: BeatSyncPlan) -> dict[str, Any]:
    validate_plan(plan)
    data = asdict(plan)
    data["materials"] = [
        {**asdict(item), "kind": item.kind.value}
        for item in plan.materials
    ]
    return data


def plan_from_dict(payload: Mapping[str, Any]) -> BeatSyncPlan:
    if not isinstance(payload, Mapping):
        raise PlanError("invalid beat-sync plan: root must be an object")
    try:
        analysis_data = payload["analysis"]
        settings_data = payload["settings"]
        material_data = payload["materials"]
        analysis = BeatAnalysis(
            duration_ms=analysis_data["duration_ms"],
            bpm=analysis_data["bpm"],
            beats_ms=tuple(analysis_data["beats_ms"]),
            cut_points_ms=tuple(analysis_data["cut_points_ms"]),
            minimum_cut_ms=analysis_data["minimum_cut_ms"],
        )
        settings = BeatSyncSettings(
            every_n_requested=settings_data["every_n_requested"],
            every_n_resolved=settings_data["every_n_resolved"],
            order_mode=settings_data["order_mode"],
            timeline_name=settings_data["timeline_name"],
        )
        materials = tuple(
            MaterialPlan(
                input_index=item["input_index"],
                kind=MaterialKind(item["kind"]),
                order_index=item["order_index"],
            )
            for item in material_data
        )
        plan = BeatSyncPlan(
            schema_version=payload["schema_version"],
            job_id=payload["job_id"],
            audio_input_index=payload["audio_input_index"],
            analysis=analysis,
            settings=settings,
            materials=materials,
            mode=payload["mode"],
        )
        validate_plan(plan)
        return plan
    except PlanError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanError(f"invalid beat-sync plan: {exc}") from exc


def save_plan(path: str | Path, plan: BeatSyncPlan) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}-{uuid4()}.tmp"
    try:
        temporary.write_text(
            json.dumps(
                plan_to_dict(plan),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_plan(path: str | Path) -> BeatSyncPlan:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read beat-sync plan: {exc}") from exc
    return plan_from_dict(payload)
