import pytest

from minoru_studio_resolve.gateway import GatewayError, ResolveGateway
from tests.resolve_adapter.fakes import FakeMediaPoolItem, FakeResolve


def make_environment(tmp_path, mode, **fake_kwargs):
    resolve = FakeResolve(**fake_kwargs)
    gateway = ResolveGateway(resolve)
    bin_detail = gateway.create_application_bin("demo Resolve", "attempt-1")
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    if mode == "transcribe":
        sources = [
            {"key": "source-video", "kind": "video", "path": str(video)},
            {"key": "source-audio", "kind": "audio", "path": str(video)},
        ]
    else:
        narration = tmp_path / "narration.wav"
        narration.write_bytes(b"narration")
        sources = [
            {"key": "source-video", "kind": "video", "path": str(video)},
            {
                "key": "narration-audio",
                "kind": "audio",
                "path": str(narration),
            },
        ]
    validated = {"mode": mode, "bin": bin_detail, "sources": sources}
    return resolve, gateway, validated


def test_transcribe_places_same_item_on_v1_and_a1(tmp_path):
    resolve, gateway, validated = make_environment(tmp_path, "transcribe")
    details = gateway.import_media_sources(
        validated["sources"],
        validated["bin"],
    )
    assert [len(call) for call in resolve.project.media_pool.import_calls] == [1]
    items = gateway.find_media_items(validated["bin"], details)
    timeline, unused_detail = gateway.create_final_timeline("demo Resolve")
    result = gateway.place_media_timeline(timeline, items, validated)
    video = timeline.video_track_items[1][0]
    audio = timeline.audio_track_items[1][0]
    assert video.media_pool_item.GetUniqueId() == audio.media_pool_item.GetUniqueId()
    assert video.record_frame == audio.record_frame == 0
    assert result["audio_key"] == "source-audio"


def test_narrate_places_generated_narration_on_a1(tmp_path):
    resolve, gateway, validated = make_environment(tmp_path, "narrate")
    details = gateway.import_media_sources(
        validated["sources"],
        validated["bin"],
    )
    items = gateway.find_media_items(validated["bin"], details)
    timeline, unused_detail = gateway.create_final_timeline("demo Resolve")
    gateway.place_media_timeline(timeline, items, validated)
    assert timeline.video_track_items[1][0].media_pool_item.GetName() == "source.mp4"
    assert timeline.audio_track_items[1][0].media_pool_item.GetName() == "narration.wav"


def test_narrate_stages_audio_without_frame_metadata(tmp_path):
    unused_resolve, gateway, validated = make_environment(
        tmp_path,
        "narrate",
        audio_frames="",
    )
    details = gateway.import_media_sources(
        validated["sources"],
        validated["bin"],
    )
    by_key = {detail["key"]: detail for detail in details}
    assert by_key["source-video"]["frames"] > 0
    assert by_key["narration-audio"]["frames"] == 0


def test_narrate_places_full_audio_clip_without_frame_metadata(tmp_path):
    unused_resolve, gateway, validated = make_environment(
        tmp_path,
        "narrate",
        audio_frames="",
    )
    details = gateway.import_media_sources(
        validated["sources"],
        validated["bin"],
    )
    items = gateway.find_media_items(validated["bin"], details)
    timeline, unused_detail = gateway.create_final_timeline("demo Resolve")
    result = gateway.place_media_timeline(timeline, items, validated)
    audio = timeline.audio_track_items[1][0]
    assert audio.media_pool_item.GetName() == "narration.wav"
    assert audio.record_frame == 0
    assert audio.duration > 0
    assert result["audio_key"] == "narration-audio"


def test_video_without_frame_metadata_is_rejected(tmp_path):
    unused_resolve, gateway, validated = make_environment(
        tmp_path,
        "transcribe",
        video_frames="",
    )
    with pytest.raises(GatewayError):
        gateway.import_media_sources(validated["sources"], validated["bin"])


def test_missing_recorded_media_item_raises(tmp_path):
    unused_resolve, gateway, validated = make_environment(
        tmp_path,
        "transcribe",
    )
    details = gateway.import_media_sources(
        validated["sources"],
        validated["bin"],
    )
    details[0]["id"] = "missing-item"
    with pytest.raises(GatewayError):
        gateway.find_media_items(validated["bin"], details)


def test_duplicate_item_id_in_bin_raises(tmp_path):
    unused_resolve, gateway, validated = make_environment(
        tmp_path,
        "transcribe",
    )
    details = gateway.import_media_sources(
        validated["sources"],
        validated["bin"],
    )
    folder = gateway.application_bin(validated["bin"])
    folder.clips.append(
        FakeMediaPoolItem(details[0]["id"], details[0]["path"], "video")
    )
    with pytest.raises(GatewayError):
        gateway.find_media_items(validated["bin"], details)


def test_empty_append_result_fails_placement(tmp_path):
    resolve, gateway, validated = make_environment(tmp_path, "transcribe")
    details = gateway.import_media_sources(
        validated["sources"],
        validated["bin"],
    )
    items = gateway.find_media_items(validated["bin"], details)
    timeline, unused_detail = gateway.create_final_timeline("demo Resolve")
    resolve.project.media_pool.fail_visual_append_after = 0
    with pytest.raises(GatewayError):
        gateway.place_media_timeline(timeline, items, validated)


def test_timeline_by_id_finds_recorded_timeline(tmp_path):
    unused_resolve, gateway, unused_validated = make_environment(
        tmp_path,
        "transcribe",
    )
    timeline, detail = gateway.create_final_timeline("demo Resolve")
    assert gateway.timeline_by_id(detail["id"]) is timeline
    with pytest.raises(GatewayError, match="recorded final timeline is missing"):
        gateway.timeline_by_id("absent-timeline")


def test_subtitle_track_count_reflects_manual_track(tmp_path):
    unused_resolve, gateway, unused_validated = make_environment(
        tmp_path,
        "transcribe",
    )
    timeline, unused_detail = gateway.create_final_timeline("demo Resolve")
    assert gateway.subtitle_track_count(timeline) == 0
    assert timeline.AddTrack("subtitle")
    assert gateway.subtitle_track_count(timeline) == 1
