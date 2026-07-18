from datetime import UTC, datetime

import pytest

from minoru_studio.jobs.model import (
    JobMode,
    JobStatus,
    ManifestError,
    StepRecord,
    StepStatus,
    manifest_from_dict,
    manifest_to_dict,
    new_manifest,
)


def test_new_manifest_has_stable_schema_and_pending_state():
    now = datetime(2026, 7, 18, 3, 0, tzinfo=UTC)
    manifest = new_manifest("demo", JobMode.BEAT_SYNC, now=now)
    assert manifest.schema_version == 1
    assert manifest.name == "demo"
    assert manifest.mode is JobMode.BEAT_SYNC
    assert manifest.status is JobStatus.PENDING
    assert manifest.created_at == "2026-07-18T03:00:00+00:00"
    assert manifest.updated_at == manifest.created_at


def test_manifest_round_trip_preserves_nested_enums():
    manifest = new_manifest("voice", JobMode.NARRATE)
    manifest.steps["prepare"] = StepRecord(status=StepStatus.RUNNING)
    restored = manifest_from_dict(manifest_to_dict(manifest))
    assert restored == manifest
    assert restored.steps["prepare"].status is StepStatus.RUNNING


def test_unknown_schema_is_rejected():
    manifest = new_manifest("demo", JobMode.TRANSCRIBE)
    data = manifest_to_dict(manifest)
    data["schema_version"] = 999
    with pytest.raises(ManifestError, match="unsupported schema_version"):
        manifest_from_dict(data)


def test_malformed_steps_are_rejected_as_manifest_error():
    manifest = new_manifest("demo", JobMode.TRANSCRIBE)
    data = manifest_to_dict(manifest)
    data["steps"] = []
    with pytest.raises(ManifestError, match="invalid manifest"):
        manifest_from_dict(data)


def test_all_fixed_modes_are_declared():
    assert {mode.value for mode in JobMode} == {
        "beat-sync",
        "transcribe",
        "narrate",
        "script-draft",
        "repo-demo",
    }
