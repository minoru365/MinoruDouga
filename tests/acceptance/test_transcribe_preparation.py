from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from minoru_studio.cli import main
from minoru_studio.jobs.model import JobStatus
from minoru_studio.jobs.store import JobStore, fingerprint_file
from minoru_studio.processes import ProcessResult
from minoru_studio.transcribe.contracts import (
    SegmentResult,
    WorkerResult,
    WordResult,
    load_worker_request,
    save_worker_result,
)
from minoru_studio.transcribe.service import TranscribeService


def _ffmpeg_bin() -> Path | None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return Path(ffmpeg).parent

    package_root = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    )
    if package_root.exists():
        for candidate in package_root.glob("**/bin"):
            if (candidate / "ffmpeg.exe").is_file() and (candidate / "ffprobe.exe").is_file():
                return candidate
    return None


@pytest.fixture
def ffmpeg_tools(monkeypatch) -> Path:
    bin_dir = _ffmpeg_bin()
    if bin_dir is None:
        pytest.skip("WinGet FFmpeg and FFprobe are required for transcription acceptance")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return bin_dir


def _make_video(ffmpeg_bin: Path, destination: Path) -> None:
    subprocess.run(
        [
            str(ffmpeg_bin / "ffmpeg.exe"), "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-c:a", "aac", str(destination),
        ],
        check=True,
    )


def _make_audio(ffmpeg_bin: Path, destination: Path) -> None:
    subprocess.run(
        [
            str(ffmpeg_bin / "ffmpeg.exe"), "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:a", "pcm_s16le", str(destination),
        ],
        check=True,
    )


class FakeWorker:
    def __init__(self, results: list[WorkerResult]) -> None:
        self._results = iter(results)

    def __call__(self, args: list[str], *, cancel_event: object | None) -> ProcessResult:
        request = load_worker_request(args[-1])
        save_worker_result(request.output_json, next(self._results))
        return ProcessResult(0, "", "", "fake worker")


def _spoken_result() -> WorkerResult:
    return WorkerResult(
        schema_version=1,
        model="small",
        provider_version="fake-worker",
        language="ja",
        language_probability=1.0,
        duration_ms=1_000,
        duration_after_vad_ms=900,
        no_speech=False,
        segments=(
            SegmentResult(
                100,
                900,
                "受入テストの文字起こしです。",
                (
                    WordResult(100, 400, "受入テストの"),
                    WordResult(400, 800, "文字起こしです。"),
                ),
            ),
        ),
    )


def _no_speech_result() -> WorkerResult:
    return WorkerResult(
        schema_version=1,
        model="small",
        provider_version="fake-worker",
        language="ja",
        language_probability=1.0,
        duration_ms=1_000,
        duration_after_vad_ms=0,
        no_speech=True,
        segments=(),
    )


def _argv(source: Path, jobs: Path, name: str, *options: str) -> list[str]:
    return [
        "transcribe", str(source), "-Name", name, "-OutputDir", str(jobs), *options,
    ]


def test_transcribe_preparation_e2e_uses_real_cli_media_and_renderers(
    monkeypatch, tmp_path: Path, ffmpeg_tools: Path, capsys,
):
    video = tmp_path / "source.mp4"
    audio = tmp_path / "source.wav"
    jobs = tmp_path / "jobs"
    _make_video(ffmpeg_tools, video)
    _make_audio(ffmpeg_tools, audio)
    source_before = fingerprint_file(video)
    worker = FakeWorker([
        _spoken_result(), _no_speech_result(), _spoken_result(), _spoken_result(),
    ])
    service = TranscribeService(
        worker=worker,
        model_complete=lambda cache, model: True,
    )
    monkeypatch.setattr(
        "minoru_studio.transcribe.commands.TranscribeService", lambda: service,
    )

    assert main(_argv(video, jobs, "video", "-Preview")) == 0
    video_job = jobs / "video.media-job"
    manifest = JobStore().load(video_job, recover_interrupted=False)
    assert manifest.status.value == "succeeded"
    assert (video_job / "outputs" / "transcript.txt").read_text(encoding="utf-8")
    assert (video_job / "outputs" / "subtitles.srt").exists()
    assert (video_job / "outputs" / "subtitles.vtt").exists()
    assert (video_job / "outputs" / "preview.mp4").exists()
    assert fingerprint_file(video) == source_before
    assert "受入テスト" not in (video_job / "logs" / "run.log").read_text(encoding="utf-8")

    assert main(_argv(audio, jobs, "audio-only")) == 0
    audio_job = jobs / "audio-only.media-job"
    assert not (audio_job / "outputs" / "preview.mp4").exists()
    assert (audio_job / "outputs" / "transcript.txt").read_text(encoding="utf-8") == ""
    assert (audio_job / "outputs" / "subtitles.srt").read_text(encoding="utf-8") == ""
    assert (audio_job / "outputs" / "subtitles.vtt").read_text(encoding="utf-8") == "WEBVTT\n\n"

    assert main(_argv(video, jobs, "same-name")) == 0
    assert main(_argv(video, jobs, "same-name")) == 0
    assert (jobs / "same-name.media-job").is_dir()
    assert (jobs / "same-name-002.media-job").is_dir()

    JobStore().update(audio_job, lambda manifest: setattr(manifest, "status", JobStatus.FAILED))
    with audio.open("ab") as handle:
        handle.write(b"changed")
    assert main(["transcribe", "resume", str(audio_job)]) == 1
    assert "input validation" in capsys.readouterr().err
