from minoru_studio_resolve.gateway import ResolveGateway
from tests.resolve_adapter.fakes import FakeResolve


def _populate(resolve):
    gateway = ResolveGateway(resolve)
    project = resolve.project
    media_pool = project.media_pool

    audio, photo_a, photo_b, photo_c = media_pool.ImportMedia(
        ["song.wav", "a.jpg", "b.jpg", "c.jpg"]
    )
    items = {0: audio, 1: photo_a, 2: photo_b, 3: photo_c}

    timeline = media_pool.CreateEmptyTimeline("Final")
    project.SetCurrentTimeline(timeline)

    validated = {
        "plan": {
            "audio_input_index": 0,
            "analysis": {"cut_points_ms": [0, 500, 1600, 2000]},
            "settings": {"every_n_resolved": 1, "timeline_name": "Final"},
            "materials": [
                {"input_index": 1, "kind": "photo", "order_index": 0},
                {"input_index": 2, "kind": "photo", "order_index": 1},
                {"input_index": 3, "kind": "photo", "order_index": 2},
            ],
        },
        "manifest": {"job_id": "job-1"},
    }
    detail = {
        "timeline_rate": {"numerator": 30, "denominator": 1},
        "still": {"actual_frames": 100},
        "attempt_id": "attempt-1",
        "source_windows": [],
    }
    return gateway.populate_timeline(timeline, items, validated, detail), timeline


def test_photo_duration_lag_is_corrected_by_retry():
    resolve = FakeResolve(lag_still_duration=True)
    result, _timeline = _populate(resolve)

    assert result["placed"] == 3
    assert result["mismatches"] == []
    assert result["corrections"] >= 1


def test_photo_duration_without_lag_needs_no_correction():
    resolve = FakeResolve()
    result, _timeline = _populate(resolve)

    assert result["placed"] == 3
    assert result["mismatches"] == []
    assert result["corrections"] == 0
