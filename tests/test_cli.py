import pytest

from minoru_studio.cli import main


def test_version_prints_package_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "0.3.0"


def test_empty_argv_launches_gui(monkeypatch):
    calls = []
    monkeypatch.setattr("minoru_studio.cli.launch_gui", lambda: calls.append("gui"))
    assert main([]) == 0
    assert calls == ["gui"]


def test_doctor_exit_code_reflects_report(monkeypatch):
    from minoru_studio.doctor import DoctorReport

    monkeypatch.setattr(
        "minoru_studio.cli.run_doctor",
        lambda: DoctorReport(checks=[]),
    )

    assert main(["doctor", "--json"]) == 0


def test_script_draft_help_explains_create_resume_and_limited_options(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["script-draft", "--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "create" in output
    assert "resume" in output
    assert "-Name" in output
    assert "-OutputDir" in output
    assert "-Model" not in output
    assert "-Language" not in output
