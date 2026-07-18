from __future__ import annotations

from pathlib import Path

import pytest

from minoru_studio.cli import main
from minoru_studio.transcribe.service import (
    TranscriptionFailed,
    TranscriptionInterrupted,
)


def _create_arguments(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "transcribe",
        str(tmp_path / "input.mp4"),
        "-Name",
        "demo",
        "--output-dir",
        str(tmp_path),
        *extra,
    ]


def test_create_accepts_powershell_and_gnu_options_with_defaults(monkeypatch, tmp_path, capsys):
    calls = []

    class Service:
        def create_and_run(self, request, *, allow_model_download):
            calls.append((request, allow_model_download))
            return tmp_path / "demo.transcribe-job"

    monkeypatch.setattr("minoru_studio.transcribe.commands.TranscribeService", lambda: Service())

    assert main(_create_arguments(tmp_path)) == 0

    request, allow_model_download = calls[0]
    assert request.input_path == tmp_path / "input.mp4"
    assert request.name == "demo"
    assert request.output_dir == tmp_path.resolve()
    assert request.model == "small"
    assert request.language == "ja"
    assert request.normalize is False
    assert request.denoise is False
    assert request.preview is False
    assert allow_model_download is False
    assert capsys.readouterr().out.strip() == str((tmp_path / "demo.transcribe-job").resolve())


def test_create_maps_all_options_and_normalizes_language(monkeypatch, tmp_path):
    calls = []

    class Service:
        def create_and_run(self, request, *, allow_model_download):
            calls.append((request, allow_model_download))
            return tmp_path / "done"

    monkeypatch.setattr("minoru_studio.transcribe.commands.TranscribeService", lambda: Service())

    assert main(_create_arguments(
        tmp_path,
        "-Model", "medium",
        "--language", "ENg",
        "-Normalize",
        "--denoise",
        "-Preview",
        "--allow-model-download",
    )) == 0

    request, allow_model_download = calls[0]
    assert request.model == "medium"
    assert request.language == "eng"
    assert request.normalize is True
    assert request.denoise is True
    assert request.preview is True
    assert allow_model_download is True


@pytest.mark.parametrize("language", ["auto", "JA", "en", "ENG"])
def test_create_accepts_supported_language_shapes(monkeypatch, tmp_path, language):
    calls = []

    class Service:
        def create_and_run(self, request, *, allow_model_download):
            calls.append(request)
            return tmp_path / "done"

    monkeypatch.setattr("minoru_studio.transcribe.commands.TranscribeService", lambda: Service())

    assert main(_create_arguments(tmp_path, "-Language", language)) == 0
    assert calls[0].language == language.lower()


@pytest.mark.parametrize("language", ["j", "japan", "en-GB", "123"])
def test_create_rejects_invalid_language_shapes(tmp_path, language):
    with pytest.raises(SystemExit) as raised:
        main(_create_arguments(tmp_path, "-Language", language))
    assert raised.value.code == 2


@pytest.mark.parametrize("arguments", [
    ["transcribe", "input.mp4", "-OutputDir", "jobs"],
    ["transcribe", "input.mp4", "-Name", "demo"],
])
def test_create_requires_name_and_output_dir(arguments):
    with pytest.raises(SystemExit) as raised:
        main(arguments)
    assert raised.value.code == 2


def test_resume_uses_ephemeral_download_permission(monkeypatch, tmp_path, capsys):
    calls = []

    class Service:
        def resume(self, job_dir, *, allow_model_download):
            calls.append((job_dir, allow_model_download))
            return job_dir

    monkeypatch.setattr("minoru_studio.transcribe.commands.TranscribeService", lambda: Service())

    assert main([
        "transcribe", "resume", str(tmp_path), "-AllowModelDownload",
    ]) == 0
    assert calls == [(tmp_path.resolve(), True)]
    assert capsys.readouterr().out.strip() == str(tmp_path.resolve())


@pytest.mark.parametrize("arguments", [
    ["transcribe", "resume"],
    ["transcribe", "input.mp4", "another.mp4", "-Name", "demo", "-OutputDir", "jobs"],
    ["transcribe", "resume", "job", "-Name", "demo"],
])
def test_transcribe_rejects_ambiguous_or_mode_incompatible_arguments(arguments):
    with pytest.raises(SystemExit) as raised:
        main(arguments)
    assert raised.value.code == 2


def test_service_failure_returns_content_free_error_without_launching_gui(monkeypatch, tmp_path, capsys):
    job_dir = tmp_path / "failed"

    class Service:
        def create_and_run(self, request, *, allow_model_download):
            raise TranscriptionFailed(job_dir, "worker")

    gui_calls = []
    monkeypatch.setattr("minoru_studio.transcribe.commands.TranscribeService", lambda: Service())
    monkeypatch.setattr("minoru_studio.cli.launch_gui", lambda: gui_calls.append(True))

    assert main(_create_arguments(tmp_path)) == 1
    assert gui_calls == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "worker"


def test_interruption_returns_130_and_resumable_absolute_job_path(monkeypatch, tmp_path, capsys):
    job_dir = tmp_path / "interrupted"

    class Service:
        def create_and_run(self, request, *, allow_model_download):
            raise TranscriptionInterrupted(job_dir)

    monkeypatch.setattr("minoru_studio.transcribe.commands.TranscribeService", lambda: Service())

    assert main(_create_arguments(tmp_path)) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == str(job_dir.resolve())
