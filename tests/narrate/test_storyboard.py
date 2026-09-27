from __future__ import annotations

import json
from pathlib import Path

import pytest

from minoru_studio.narrate.storyboard import parse_storyboard, storyboard_utterances


def _descriptor(tmp_path: Path, clips: list[dict[str, object]]) -> Path:
    image = tmp_path / "frame.png"; image.write_bytes(b"image")
    video = tmp_path / "scene.mp4"; video.write_bytes(b"video")
    descriptor = tmp_path / "story.json"
    descriptor.write_text(json.dumps({"version": 1, "clips": clips}), encoding="utf-8")
    return descriptor


def test_storyboard_preserves_explicit_order_reuses_sources_and_maps_split_chapters_before_later_scenes(tmp_path: Path):
    descriptor = _descriptor(tmp_path, [
        {"id": "chapter-a", "kind": "image", "source": "frame.png", "narration": "第一。第二。"},
        {"id": "chapter-b", "kind": "image", "source": "frame.png", "narration": "第三。"},
        {"id": "later", "kind": "video", "source": "scene.mp4", "narration": "第四。", "trim_start_ms": 0, "trim_end_ms": 1000},
    ])

    storyboard = parse_storyboard(descriptor)
    groups = storyboard_utterances(storyboard)

    assert [clip.id for clip in storyboard.clips] == ["chapter-a", "chapter-b", "later"]
    assert [path.name for path in storyboard.sources] == ["frame.png", "scene.mp4"]
    assert [[item.index for item in group.utterances] for group in groups] == [[1, 2], [3], [4]]


@pytest.mark.parametrize("payload", [
    '{"version":1,"version":1,"clips":[]}',
    '{"version":1,"clips":[{"id":"x","kind":"image","source":"https://example.test/a.png","narration":"a"}]}',
    '{"version":1,"clips":[{"id":"x","kind":"image","source":"frame.png","narration":"a","unknown":1}]}',
    '{"version":1,"clips":[{"id":"x","kind":"image","source":"frame.png","narration":"a"},{"id":"x","kind":"image","source":"frame.png","narration":"b"}]}',
])
def test_storyboard_rejects_duplicate_or_untrusted_descriptor_content(tmp_path: Path, payload: str):
    (tmp_path / "frame.png").write_bytes(b"image")
    descriptor = tmp_path / "story.json"; descriptor.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="storyboard"):
        parse_storyboard(descriptor)


@pytest.mark.parametrize("clip", [
    {"id": "image", "kind": "image", "source": "frame.png", "narration": "a", "trim_start_ms": 0},
    {"id": "video", "kind": "video", "source": "scene.mp4", "narration": "a", "trim_start_ms": 100, "trim_end_ms": 100},
    {"id": "video", "kind": "video", "source": "scene.mp4", "narration": "a", "trim_start_ms": -1},
])
def test_storyboard_rejects_invalid_trims_before_media_consumption(tmp_path: Path, clip: dict[str, object]):
    descriptor = _descriptor(tmp_path, [clip])
    with pytest.raises(ValueError, match="storyboard"):
        parse_storyboard(descriptor)
