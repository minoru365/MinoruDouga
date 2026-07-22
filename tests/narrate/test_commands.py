from pathlib import Path

import pytest

from minoru_studio.cli import main
from minoru_studio.narrate.models import DurationWarning
from minoru_studio.narrate.service import NarrateFailed, NarrateInterrupted


def _create_arguments(tmp_path: Path) -> list[str]:
    return [
        "narrate", str(tmp_path / "input.mp4"), "-Script", str(tmp_path / "script.md"),
        "-Name", "demo", "-OutputDir", str(tmp_path), "-Preview",
    ]


def test_create_runs_service_prints_job_and_emits_content_free_warning(monkeypatch, tmp_path, capsys):
    calls = []

    class Service:
        def create_and_run(self, request):
            calls.append(request)
            return tmp_path / "demo.media-job"

    monkeypatch.setattr("minoru_studio.narrate.commands.NarrateService", lambda: Service())
    monkeypatch.setattr(
        "minoru_studio.narrate.commands.read_duration_warning",
        lambda job: DurationWarning(1_000, 1_300),
    )

    assert main(_create_arguments(tmp_path)) == 0
    assert calls[0].input_path == tmp_path / "input.mp4"
    assert calls[0].script_path == tmp_path / "script.md"
    assert calls[0].name == "demo"
    assert calls[0].output_dir == tmp_path.resolve()
    assert calls[0].preview is True
    captured = capsys.readouterr()
    assert captured.out.strip() == str((tmp_path / "demo.media-job").resolve())
    assert captured.err.strip() == "warning: narration duration 1300 ms exceeds source duration 1000 ms"
    assert "script" not in captured.err.casefold()


def test_resume_runs_service_and_prints_resolved_job_path(monkeypatch, tmp_path, capsys):
    class Service:
        def resume(self, job_dir):
            assert job_dir == tmp_path.resolve()
            return job_dir

    monkeypatch.setattr("minoru_studio.narrate.commands.NarrateService", lambda: Service())
    monkeypatch.setattr("minoru_studio.narrate.commands.read_duration_warning", lambda job: None)

    assert main(["narrate", "resume", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path.resolve())


@pytest.mark.parametrize("arguments", (
    ["narrate", "input.mp4", "-Script", "script.md", "-Name", "demo"],
    ["narrate", "input.mp4", "-Script", "script.md", "-OutputDir", "jobs"],
    ["narrate", "input.mp4", "-Name", "demo", "-OutputDir", "jobs"],
    ["narrate", "input.mp4", "-Script", "script.md", "-Name", " ", "-OutputDir", "jobs"],
    ["narrate", "resume"],
    ["narrate", "resume", "job", "-Preview"],
    ["narrate", "resume", "job", "-Script", "script.md"],
))
def test_command_rejects_missing_or_create_only_resume_arguments(arguments):
    with pytest.raises(SystemExit) as raised:
        main(arguments)
    assert raised.value.code == 2


def test_failure_and_interruption_have_stable_exit_codes(monkeypatch, tmp_path, capsys):
    class FailedService:
        def create_and_run(self, request):
            raise NarrateFailed(tmp_path / "failed", "VOICEVOX unavailable")

    monkeypatch.setattr("minoru_studio.narrate.commands.NarrateService", lambda: FailedService())
    assert main(_create_arguments(tmp_path)) == 1
    assert capsys.readouterr().err.strip() == "VOICEVOX unavailable"

    job_dir = tmp_path / "interrupted"

    class InterruptedService:
        def create_and_run(self, request):
            raise NarrateInterrupted(job_dir)

    monkeypatch.setattr("minoru_studio.narrate.commands.NarrateService", lambda: InterruptedService())
    assert main(_create_arguments(tmp_path)) == 130
    assert capsys.readouterr().err.strip() == str(job_dir.resolve())
