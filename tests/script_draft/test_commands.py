from pathlib import Path

import pytest

from minoru_studio.cli import main
from minoru_studio.script_draft.service import ScriptDraftFailed, ScriptDraftInterrupted


def _create_arguments(tmp_path: Path) -> list[str]:
    return ["script-draft", str(tmp_path / "input.mp4"), "-Name", "demo", "-OutputDir", str(tmp_path)]


def test_create_runs_service_and_prints_resolved_job_path(monkeypatch, tmp_path, capsys):
    calls = []

    class Service:
        def create_and_run(self, request):
            calls.append(request)
            return tmp_path / "demo.script-draft-job"

    monkeypatch.setattr("minoru_studio.script_draft.commands.ScriptDraftService", lambda: Service())

    assert main(_create_arguments(tmp_path)) == 0
    assert calls[0].input_path == tmp_path / "input.mp4"
    assert calls[0].name == "demo"
    assert calls[0].output_dir == tmp_path.resolve()
    assert capsys.readouterr().out.strip() == str((tmp_path / "demo.script-draft-job").resolve())


def test_resume_runs_service_and_prints_resolved_job_path(monkeypatch, tmp_path, capsys):
    class Service:
        def resume(self, job_dir):
            assert job_dir == tmp_path.resolve()
            return job_dir

    monkeypatch.setattr("minoru_studio.script_draft.commands.ScriptDraftService", lambda: Service())

    assert main(["script-draft", "resume", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path.resolve())


@pytest.mark.parametrize("arguments", (
    ["script-draft", "input.mp4", "-OutputDir", "jobs"],
    ["script-draft", "input.mp4", "-Name", "demo"],
    ["script-draft", "input.mp4", "extra", "-Name", "demo", "-OutputDir", "jobs"],
    ["script-draft", "resume"],
    ["script-draft", "resume", "job", "-Name", "demo"],
))
def test_command_rejects_missing_or_mode_incompatible_arguments(arguments):
    with pytest.raises(SystemExit) as raised:
        main(arguments)
    assert raised.value.code == 2


def test_failure_returns_stable_category(monkeypatch, tmp_path, capsys):
    class Service:
        def create_and_run(self, request):
            raise ScriptDraftFailed(tmp_path / "failed", "FFmpeg")

    monkeypatch.setattr("minoru_studio.script_draft.commands.ScriptDraftService", lambda: Service())

    assert main(_create_arguments(tmp_path)) == 1
    assert capsys.readouterr().err.strip() == "FFmpeg"


def test_interruption_returns_resumable_path(monkeypatch, tmp_path, capsys):
    job_dir = tmp_path / "interrupted"

    class Service:
        def create_and_run(self, request):
            raise ScriptDraftInterrupted(job_dir)

    monkeypatch.setattr("minoru_studio.script_draft.commands.ScriptDraftService", lambda: Service())

    assert main(_create_arguments(tmp_path)) == 130
    assert capsys.readouterr().err.strip() == str(job_dir.resolve())
