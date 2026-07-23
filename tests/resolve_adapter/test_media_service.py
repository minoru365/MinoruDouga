import hashlib
import json
from pathlib import Path

import pytest

from minoru_studio_resolve.gateway import ResolveGateway
from minoru_studio_resolve.job_io import ApplicationStore
from minoru_studio_resolve.media_service import MediaPlacementService
from minoru_studio_resolve.service import AdapterError
from minoru_studio_resolve.state import next_action
from tests.resolve_adapter.fakes import FakeResolve


def make_media_service(**fake_kwargs):
    resolve = FakeResolve(**fake_kwargs)
    service = MediaPlacementService(
        ResolveGateway(resolve),
        ApplicationStore(),
        attempt_ids=iter(["attempt-1", "attempt-2"]),
        operation_tokens=iter(
            ["operation-{0}".format(number) for number in range(1, 17)]
        ),
    )
    return resolve, service


def test_transcribe_places_then_waits_for_manual_srt(prepared_transcribe_job):
    resolve, service = make_media_service()
    started = service.start(str(prepared_transcribe_job))
    assert started["state"] == "ready"
    assert started["mode"] == "transcribe"
    assert started["subtitle"]["user_confirmed"] is False
    detail = service.apply_ready(
        str(prepared_transcribe_job),
        confirm=lambda _: True,
    )
    assert detail["state"] == "awaiting_subtitle_import"
    timeline = resolve.project.final_timelines[0]
    assert (
        len(timeline.video_track_items[1])
        == len(timeline.audio_track_items[1])
        == 1
    )
    assert detail["timeline"]["id"] == timeline.GetUniqueId()
    assert next_action(detail) == "confirm_subtitles"


def test_subtitle_confirmation_requires_manual_track(prepared_narrate_job):
    resolve, service = make_media_service()
    service.start(str(prepared_narrate_job))
    service.apply_ready(str(prepared_narrate_job), confirm=lambda _: True)
    with pytest.raises(AdapterError, match="subtitle track"):
        service.confirm_subtitle_import(
            str(prepared_narrate_job),
            confirm=lambda _: True,
        )
    assert (
        service.latest_detail(str(prepared_narrate_job))["state"]
        == "awaiting_subtitle_import"
    )
    assert resolve.project.final_timelines[0].AddTrack("subtitle")
    confirmed = service.confirm_subtitle_import(
        str(prepared_narrate_job),
        confirm=lambda _: True,
    )
    assert confirmed["state"] == "applied"
    assert confirmed["subtitle"]["user_confirmed"] is True


def test_narrate_applies_audio_clip_without_frame_metadata(
    prepared_narrate_job,
):
    resolve, service = make_media_service(audio_frames="")
    assert service.start(str(prepared_narrate_job))["state"] == "ready"
    detail = service.apply_ready(
        str(prepared_narrate_job),
        confirm=lambda _: True,
    )
    assert detail["state"] == "awaiting_subtitle_import"
    timeline = resolve.project.final_timelines[0]
    assert (
        timeline.audio_track_items[1][0].media_pool_item.GetName()
        == "narration.wav"
    )


def test_declined_timeline_confirmation_keeps_ready(prepared_transcribe_job):
    resolve, service = make_media_service()
    service.start(str(prepared_transcribe_job))
    detail = service.apply_ready(
        str(prepared_transcribe_job),
        confirm=lambda _: False,
    )
    assert detail["state"] == "ready"
    assert detail["timeline"] is None
    assert not resolve.project.final_timelines


def test_declined_subtitle_confirmation_keeps_checkpoint(
    prepared_transcribe_job,
):
    resolve, service = make_media_service()
    service.start(str(prepared_transcribe_job))
    service.apply_ready(str(prepared_transcribe_job), confirm=lambda _: True)
    resolve.project.final_timelines[0].AddTrack("subtitle")
    detail = service.confirm_subtitle_import(
        str(prepared_transcribe_job),
        confirm=lambda _: False,
    )
    assert detail["state"] == "awaiting_subtitle_import"
    assert detail["subtitle"]["user_confirmed"] is False


def test_start_refuses_unfinished_attempt(prepared_transcribe_job):
    unused_resolve, service = make_media_service()
    service.start(str(prepared_transcribe_job))
    with pytest.raises(AdapterError):
        service.start(str(prepared_transcribe_job))


def test_terminal_attempt_requires_new_attempt_and_gets_unique_name(
    prepared_transcribe_job,
):
    resolve, service = make_media_service()
    service.start(str(prepared_transcribe_job))
    service.apply_ready(str(prepared_transcribe_job), confirm=lambda _: True)
    resolve.project.final_timelines[0].AddTrack("subtitle")
    service.confirm_subtitle_import(
        str(prepared_transcribe_job),
        confirm=lambda _: True,
    )
    with pytest.raises(AdapterError, match="new_attempt"):
        service.start(str(prepared_transcribe_job))
    service.start(str(prepared_transcribe_job), new_attempt=True)
    service.apply_ready(str(prepared_transcribe_job), confirm=lambda _: True)
    assert [
        timeline.GetName() for timeline in resolve.project.final_timelines
    ] == ["transcribe demo Resolve", "transcribe demo Resolve-002"]


def test_append_failure_keeps_partial_timeline_and_fails_attempt(
    prepared_transcribe_job,
):
    resolve, service = make_media_service()
    service.start(str(prepared_transcribe_job))
    resolve.project.media_pool.fail_visual_append_after = 0
    with pytest.raises(AdapterError):
        service.apply_ready(
            str(prepared_transcribe_job),
            confirm=lambda _: True,
        )
    detail = service.latest_detail(str(prepared_transcribe_job))
    assert detail["state"] == "failed"
    assert detail["timeline"]["id"] in resolve.project.final_timeline_ids


def test_changed_srt_keeps_checkpoint(prepared_narrate_job):
    resolve, service = make_media_service()
    service.start(str(prepared_narrate_job))
    service.apply_ready(str(prepared_narrate_job), confirm=lambda _: True)
    resolve.project.final_timelines[0].AddTrack("subtitle")
    srt = Path(prepared_narrate_job) / "outputs" / "subtitles.srt"
    payload = b"regenerated"
    srt.write_bytes(payload)
    manifest_path = Path(prepared_narrate_job) / "job.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["artifacts"]:
        if item["kind"] == "subtitles-srt":
            item["size"] = len(payload)
            item["sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AdapterError):
        service.confirm_subtitle_import(
            str(prepared_narrate_job),
            confirm=lambda _: True,
        )
    assert (
        service.latest_detail(str(prepared_narrate_job))["state"]
        == "awaiting_subtitle_import"
    )


def test_missing_timeline_keeps_checkpoint(prepared_transcribe_job):
    resolve, service = make_media_service()
    service.start(str(prepared_transcribe_job))
    service.apply_ready(str(prepared_transcribe_job), confirm=lambda _: True)
    timeline = resolve.project.final_timelines[0]
    timeline.AddTrack("subtitle")
    resolve.project.timelines.remove(timeline)
    with pytest.raises(AdapterError):
        service.confirm_subtitle_import(
            str(prepared_transcribe_job),
            confirm=lambda _: True,
        )
    assert (
        service.latest_detail(str(prepared_transcribe_job))["state"]
        == "awaiting_subtitle_import"
    )


def test_leftover_operation_token_marks_attempt_failed(
    prepared_transcribe_job,
):
    unused_resolve, service = make_media_service()
    started = service.start(str(prepared_transcribe_job))
    service.applications.claim(
        str(prepared_transcribe_job),
        started["attempt_id"],
        "stale-token",
    )
    with pytest.raises(AdapterError, match="interrupted"):
        service.apply_ready(
            str(prepared_transcribe_job),
            confirm=lambda _: True,
        )
    detail = service.applications.load(
        str(prepared_transcribe_job),
        started["attempt_id"],
    )
    assert detail["state"] == "failed"
    assert detail["operation_token"] is None


def test_sentinel_timeline_is_never_changed(prepared_transcribe_job):
    resolve, service = make_media_service()
    sentinel = resolve.project.sentinel_timeline
    service.start(str(prepared_transcribe_job))
    service.apply_ready(str(prepared_transcribe_job), confirm=lambda _: True)
    resolve.project.final_timelines[0].AddTrack("subtitle")
    service.confirm_subtitle_import(
        str(prepared_transcribe_job),
        confirm=lambda _: True,
    )
    assert resolve.project.GetTimelineByIndex(1) is sentinel
    assert sentinel.items == []
    assert sentinel.markers == []
    assert sentinel.subtitle_track_count == 0
