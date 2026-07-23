import json
from pathlib import Path

import pytest

from minoru_studio.jobs.model import JobMode, JobStatus
from minoru_studio.jobs.store import JobStore
from resolve_adapter.minoru_studio_resolve.contract import (
    ContractError,
    load_validated_media_job,
)
from tests.resolve_adapter.conftest import (
    build_prepared_narrate_job,
    build_prepared_transcribe_job,
)


def _edit_manifest(job_dir, mutate):
    path = Path(job_dir) / "job.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_transcribe_reuses_verified_source_for_v1_and_a1(
    prepared_transcribe_job,
):
    loaded = load_validated_media_job(prepared_transcribe_job)
    assert loaded["mode"] == "transcribe"
    assert [item["key"] for item in loaded["sources"]] == [
        "source-video",
        "source-audio",
    ]
    assert loaded["sources"][0]["path"] == loaded["sources"][1]["path"]


def test_narrate_uses_verified_narration_for_a1(prepared_narrate_job):
    loaded = load_validated_media_job(prepared_narrate_job)
    assert [item["key"] for item in loaded["sources"]] == [
        "source-video",
        "narration-audio",
    ]
    assert loaded["sources"][1]["path"].endswith("narration.wav")


def test_timeline_name_and_subtitle_fingerprint_are_returned(
    prepared_transcribe_job,
):
    loaded = load_validated_media_job(prepared_transcribe_job)
    assert loaded["timeline_name"] == "transcribe demo Resolve"
    srt = Path(loaded["subtitle"]["path"])
    assert srt.name == "subtitles.srt"
    assert loaded["subtitle"]["size"] == srt.stat().st_size
    assert isinstance(loaded["subtitle"]["sha256"], str)


def _changed_source(job_dir):
    def mutate(manifest):
        Path(manifest["inputs"][0]["path"]).write_bytes(b"changed")

    _edit_manifest(job_dir, mutate)


def _changed_narration_wav(job_dir):
    (Path(job_dir) / "outputs" / "narration.wav").write_bytes(b"changed")


def _absent_srt_record(job_dir):
    def mutate(manifest):
        manifest["artifacts"] = [
            item
            for item in manifest["artifacts"]
            if item["kind"] != "subtitles-srt"
        ]

    _edit_manifest(job_dir, mutate)


def _duplicate_srt_record(job_dir):
    def mutate(manifest):
        records = [
            item
            for item in manifest["artifacts"]
            if item["kind"] == "subtitles-srt"
        ]
        manifest["artifacts"].append(dict(records[0]))

    _edit_manifest(job_dir, mutate)


def _escaping_srt_record(job_dir):
    def mutate(manifest):
        for item in manifest["artifacts"]:
            if item["kind"] == "subtitles-srt":
                item["path"] = "../outside.srt"

    _edit_manifest(job_dir, mutate)


def _directory_srt_artifact(job_dir):
    path = Path(job_dir) / "outputs" / "subtitles.srt"
    path.unlink()
    path.mkdir()


@pytest.mark.parametrize(
    "build, corrupt",
    [
        (build_prepared_transcribe_job, _changed_source),
        (build_prepared_narrate_job, _changed_narration_wav),
        (build_prepared_transcribe_job, _absent_srt_record),
        (build_prepared_transcribe_job, _duplicate_srt_record),
        (build_prepared_transcribe_job, _escaping_srt_record),
        (build_prepared_transcribe_job, _directory_srt_artifact),
    ],
    ids=[
        "changed-source",
        "changed-narration-wav",
        "absent-srt-record",
        "duplicate-srt-record",
        "escaping-srt-record",
        "directory-srt-artifact",
    ],
)
def test_tampered_media_job_is_rejected_before_resolve(
    tmp_path, build, corrupt,
):
    job_dir = build(tmp_path)
    corrupt(job_dir)
    with pytest.raises(ContractError):
        load_validated_media_job(job_dir)


def test_audio_only_source_input_is_rejected(tmp_path):
    job_dir = build_prepared_transcribe_job(tmp_path, suffix=".wav")
    with pytest.raises(ContractError):
        load_validated_media_job(job_dir)


def test_script_draft_mode_is_rejected(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    store = JobStore()
    job_dir = store.create(
        tmp_path / "jobs",
        "draft demo",
        JobMode.SCRIPT_DRAFT,
        [source],
    )

    def finish(latest):
        latest.status = JobStatus.SUCCEEDED

    store.update(job_dir, finish)
    with pytest.raises(ContractError):
        load_validated_media_job(job_dir)


def test_unsuccessful_job_is_rejected(prepared_transcribe_job):
    def mutate(manifest):
        manifest["status"] = "failed"

    _edit_manifest(prepared_transcribe_job, mutate)
    with pytest.raises(ContractError):
        load_validated_media_job(prepared_transcribe_job)
