import json
from importlib.metadata import PackageNotFoundError

from minoru_studio.doctor import CheckStatus, render_doctor, run_doctor


def test_doctor_reports_each_required_tool(monkeypatch):
    paths = {
        "pwsh": "C:/Program Files/PowerShell/7/pwsh.exe",
        "uv": "C:/tools/uv.exe",
        "ffmpeg": "C:/tools/ffmpeg.exe",
        "ffprobe": "C:/tools/ffprobe.exe",
    }
    monkeypatch.setattr(
        "minoru_studio.doctor.shutil.which",
        lambda name: paths.get(name),
    )
    monkeypatch.setattr(
        "minoru_studio.doctor._read_version",
        lambda path, args: "test-version",
    )

    report = run_doctor()

    assert report.ok
    assert {check.name for check in report.checks} == {
        "python",
        "powershell",
        "uv",
        "ffmpeg",
        "ffprobe",
        "faster-whisper",
    }
    assert all(check.status is CheckStatus.OK for check in report.checks)


def test_missing_tool_makes_report_fail(monkeypatch):
    monkeypatch.setattr("minoru_studio.doctor.shutil.which", lambda name: None)

    report = run_doctor()

    assert not report.ok
    assert any(check.status is CheckStatus.MISSING for check in report.checks)


def test_json_report_is_machine_readable(monkeypatch):
    monkeypatch.setattr("minoru_studio.doctor.shutil.which", lambda name: None)

    payload = json.loads(render_doctor(run_doctor(), as_json=True))

    assert payload["ok"] is False
    assert isinstance(payload["checks"], list)


def test_faster_whisper_metadata_is_a_required_check(monkeypatch):
    monkeypatch.setattr("minoru_studio.doctor.shutil.which", lambda name: "C:/tool.exe")
    monkeypatch.setattr("minoru_studio.doctor._read_version", lambda path, args: "test-version")
    monkeypatch.setattr("minoru_studio.doctor.metadata.version", lambda name: "1.2.1")

    report = run_doctor()

    check = next(item for item in report.checks if item.name == "faster-whisper")
    assert check.required is True
    assert check.status is CheckStatus.OK
    assert check.version == "1.2.1"


def test_missing_faster_whisper_fails_doctor(monkeypatch):
    monkeypatch.setattr("minoru_studio.doctor.shutil.which", lambda name: "C:/tool.exe")
    monkeypatch.setattr("minoru_studio.doctor._read_version", lambda path, args: "test-version")
    monkeypatch.setattr(
        "minoru_studio.doctor.metadata.version",
        lambda name: (_ for _ in ()).throw(PackageNotFoundError(name)),
    )

    report = run_doctor()

    assert not report.ok
    assert next(item for item in report.checks if item.name == "faster-whisper").status is CheckStatus.MISSING
