import json
import struct
from pathlib import Path

import pytest

from minoru_studio.jobs.model import ArtifactRecord, JobMode
from minoru_studio.jobs.store import JobStore, fingerprint_artifact
from minoru_studio.script_draft.artifacts import (
    artifacts_valid,
    merge_candidates,
    render_artifacts,
)
from minoru_studio.script_draft.models import FrameCandidate, VideoInfo


def _png(path: Path, width: int = 1280, height: int = 720) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def _record_rendered_artifacts(job_dir: Path) -> None:
    paths = sorted((job_dir / "outputs" / "frames").glob("*.png"))
    paths.extend((job_dir / "outputs" / name for name in ("frame-index.json", "script.md")))
    records = [
        fingerprint_artifact(job_dir, path, "script-draft")
        for path in paths
    ]
    JobStore().update(job_dir, lambda manifest: manifest.artifacts.extend(records))


def test_merge_candidates_sorts_and_keeps_the_scene_image_with_fixed_reason_order(tmp_path: Path):
    scene = tmp_path / "scene.png"
    interval = tmp_path / "interval.png"
    later = tmp_path / "later.png"
    for path in (scene, interval, later):
        _png(path)

    merged = merge_candidates([
        FrameCandidate(1_000, interval, "interval"),
        FrameCandidate(950, scene, "scene"),
        FrameCandidate(1_101, later, "interval"),
    ])

    assert merged == [
        (FrameCandidate(950, scene, "scene"), ("scene", "interval")),
        (FrameCandidate(1_101, later, "interval"), ("interval",)),
    ]


def test_render_artifacts_writes_numbered_frames_index_and_empty_script_sections(tmp_path: Path):
    outputs = tmp_path / "outputs"
    first = tmp_path / "work" / "first.png"
    second = tmp_path / "work" / "second.png"
    first.parent.mkdir(parents=True)
    _png(first, 640, 480)
    _png(second, 1280, 720)

    entries = render_artifacts(
        outputs,
        VideoInfo(12_345, 1920, 1080),
        [
            (FrameCandidate(0, first, "scene"), ("scene", "interval")),
            (FrameCandidate(12_345, second, "interval"), ("interval",)),
        ],
    )

    assert [(entry.index, entry.time_ms, entry.image_path, entry.reasons) for entry in entries] == [
        (1, 0, "frames/frame-0001.png", ("scene", "interval")),
        (2, 12_345, "frames/frame-0002.png", ("interval",)),
    ]
    assert (outputs / "frames" / "frame-0001.png").read_bytes() == first.read_bytes()
    assert json.loads((outputs / "frame-index.json").read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "duration_ms": 12_345,
        "width": 1920,
        "height": 1080,
        "settings": {
            "scene_threshold": 0.30,
            "interval_ms": 5_000,
            "merge_tolerance_ms": 100,
            "max_frame_edge": 1_280,
            "frame_format": "png",
        },
        "entries": [
            {"index": 1, "time_ms": 0, "image_path": "frames/frame-0001.png", "reasons": ["scene", "interval"]},
            {"index": 2, "time_ms": 12_345, "image_path": "frames/frame-0002.png", "reasons": ["interval"]},
        ],
    }
    assert (outputs / "script.md").read_text(encoding="utf-8") == (
        "# Script Draft\n\n"
        "| Source duration | Resolution |\n"
        "| --- | --- |\n"
        "| 00:00:12.345 | 1920 × 1080 |\n\n"
        "This is a human-editable draft.\n\n"
        "## 00:00:00.000 — Frame 0001\n\n"
        "![Frame 0001](frames/frame-0001.png)\n\n"
        "### 画面の説明\n\n"
        "### 操作\n\n"
        "### ナレーション\n\n"
        "## 00:00:12.345 — Frame 0002\n\n"
        "![Frame 0002](frames/frame-0002.png)\n\n"
        "### 画面の説明\n\n"
        "### 操作\n\n"
        "### ナレーション\n"
    )


def test_render_artifacts_rejects_existing_final_outputs_and_invalid_candidate_pngs(tmp_path: Path):
    outputs = tmp_path / "outputs"
    source = tmp_path / "work" / "frame.png"
    source.parent.mkdir()
    _png(source)
    (outputs / "frames").mkdir(parents=True)
    _png(outputs / "frames" / "frame-0001.png")

    with pytest.raises(ValueError, match="^final artifact already exists$"):
        render_artifacts(outputs, VideoInfo(1, 1, 1), [
            (FrameCandidate(0, source, "scene"), ("scene",)),
        ])

    (outputs / "frames" / "frame-0001.png").unlink()
    source.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="^frame output is not a PNG file$"):
        render_artifacts(outputs, VideoInfo(1, 1, 1), [
            (FrameCandidate(0, source, "scene"), ("scene",)),
        ])


def test_artifacts_valid_rejects_changed_content_malformed_index_and_escaping_paths(tmp_path: Path):
    job_dir = JobStore().create(tmp_path, "draft", mode=JobMode.SCRIPT_DRAFT)
    source = job_dir / "work" / "frame.png"
    _png(source)
    render_artifacts(job_dir / "outputs", VideoInfo(5_000, 1280, 720), [
        (FrameCandidate(0, source, "interval"), ("interval",)),
    ])
    _record_rendered_artifacts(job_dir)

    assert artifacts_valid(job_dir)

    index_path = job_dir / "outputs" / "frame-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["entries"][0]["image_path"] = "../script.md"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert not artifacts_valid(job_dir)

    index["entries"][0]["image_path"] = "frames/frame-0001.png"
    index["entries"][0]["index"] = True
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert not artifacts_valid(job_dir)

    index["entries"][0]["index"] = 1
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert not artifacts_valid(job_dir)


def test_artifacts_valid_requires_every_expected_fingerprint_and_markdown_link(tmp_path: Path):
    job_dir = JobStore().create(tmp_path, "draft", mode=JobMode.SCRIPT_DRAFT)
    source = job_dir / "work" / "frame.png"
    _png(source)
    render_artifacts(job_dir / "outputs", VideoInfo(5_000, 1280, 720), [
        (FrameCandidate(0, source, "interval"), ("interval",)),
    ])
    _record_rendered_artifacts(job_dir)

    assert artifacts_valid(job_dir)
    (job_dir / "outputs" / "script.md").write_text("# Script Draft\n", encoding="utf-8")
    assert not artifacts_valid(job_dir)

    records = JobStore().load(job_dir, recover_interrupted=False).artifacts
    assert all(isinstance(record, ArtifactRecord) for record in records)
