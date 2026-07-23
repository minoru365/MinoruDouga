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


def test_manifest_without_tools_remains_readable_and_new_tools_round_trip():
    manifest = new_manifest("voice", JobMode.TRANSCRIBE)
    data = manifest_to_dict(manifest)
    data.pop("tools")

    assert manifest_from_dict(data).tools == {}

    manifest.tools = {"python": "3.12.0", "faster-whisper": "1.2.1"}
    assert manifest_from_dict(manifest_to_dict(manifest)).tools == manifest.tools


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


def test_script_draft_steps_are_restored_in_mode_order_before_unknown_steps():
    manifest = new_manifest("draft", JobMode.SCRIPT_DRAFT)
    manifest.steps = {
        "render-draft": StepRecord(),
        "custom": StepRecord(),
        "extract-interval-frames": StepRecord(),
        "probe-input": StepRecord(),
        "extract-scene-frames": StepRecord(),
    }

    restored = manifest_from_dict(manifest_to_dict(manifest))

    assert list(restored.steps) == [
        "probe-input",
        "extract-scene-frames",
        "extract-interval-frames",
        "render-draft",
        "custom",
    ]


def test_narrate_steps_are_restored_in_mode_order_before_unknown_steps():
    manifest = new_manifest("narration", JobMode.NARRATE)
    manifest.steps = {
        "render-preview": StepRecord(),
        "custom": StepRecord(),
        "concat-audio": StepRecord(),
        "probe-input": StepRecord(),
        "parse-script": StepRecord(),
        "synthesize-utterances": StepRecord(),
        "render-artifacts": StepRecord(),
        "later": StepRecord(),
    }

    restored = manifest_from_dict(manifest_to_dict(manifest))

    assert list(restored.steps) == [
        "probe-input",
        "parse-script",
        "synthesize-utterances",
        "concat-audio",
        "render-artifacts",
        "render-preview",
        "custom",
        "later",
    ]


def test_all_fixed_modes_are_declared():
    assert {mode.value for mode in JobMode} == {
        "beat-sync",
        "transcribe",
        "narrate",
        "script-draft",
    }
