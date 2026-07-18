import json

import pytest

from resolve_adapter.minoru_studio_resolve.ui import (
    action_for_detail,
    confirmation_text,
    instruction_for_detail,
    load_last_job,
    save_last_job,
)


@pytest.mark.parametrize(
    "state, expected",
    [
        (None, ("素材を取り込む", "start", False)),
        (
            "awaiting_in_out",
            ("In/Out設定後に再開", "resume", False),
        ),
        (
            "awaiting_still_setting",
            ("スチル設定変更後に再測定", "resume", False),
        ),
        ("ready", ("タイムラインを生成", "apply", False)),
        ("applied", ("新しい適用を開始", "start", True)),
        ("failed", ("新しい適用を開始", "start", True)),
    ],
)
def test_action_for_detail_selects_one_state_action(state, expected):
    detail = None if state is None else {"state": state}
    assert action_for_detail(detail) == expected


def test_last_job_settings_store_only_selected_path(tmp_path):
    path = tmp_path / "resolve-adapter.json"
    save_last_job(str(path), "C:/jobs/demo.media-job")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "last_job_path": "C:/jobs/demo.media-job"
    }
    assert load_last_job(str(path)) == "C:/jobs/demo.media-job"


def test_waiting_instructions_include_the_required_user_action():
    assert "In/Out" in instruction_for_detail({"state": "awaiting_in_out"})
    message = instruction_for_detail(
        {
            "state": "awaiting_still_setting",
            "still": {"required_frames": 15, "actual_frames": 60},
        }
    )
    assert "15" in message
    assert "環境設定" in message


def test_confirmation_text_summarizes_timeline_mutation():
    message = confirmation_text(
        {
            "bpm": 120.0,
            "interval": 1,
            "approximate_cut_ms": 500,
            "duration_ms": 2_000,
            "material_counts": {"photo": 1, "video": 1},
            "still": {"required_frames": 15, "actual_frames": 15},
            "timeline_name": "Demo",
        }
    )
    assert "120.0" in message
    assert "500 ms" in message
    assert "Demo" in message
