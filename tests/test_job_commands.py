import json
from pathlib import Path

import pytest

from minoru_studio.cli import main


def test_jobs_create_prints_new_job_path(tmp_path, capsys):
    assert main([
        "jobs",
        "create",
        "-Mode",
        "beat-sync",
        "-Name",
        "demo",
        "-OutputDir",
        str(tmp_path),
    ]) == 0
    created = capsys.readouterr().out.strip()
    assert created.endswith("demo.media-job")


def test_jobs_create_rejects_invalid_mode(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main([
            "jobs",
            "create",
            "-Mode",
            "invalid",
            "-Name",
            "demo",
            "-OutputDir",
            str(tmp_path),
        ])
    assert exc.value.code == 2


def test_jobs_create_resolves_relative_output_dir_at_cli_boundary(
    tmp_path, capsys, monkeypatch
):
    captured: dict[str, Path] = {}

    class Store:
        def create(self, root, name, mode):
            captured["root"] = root
            return root / f"{name}.media-job"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("minoru_studio.job_commands.JobStore", Store)

    assert main([
        "jobs", "create", "-Mode", "narrate", "-Name", "voice", "-OutputDir", ".",
    ]) == 0
    assert captured["root"].is_absolute()
    assert Path(capsys.readouterr().out.strip()).is_absolute()


def test_jobs_create_reuses_name_without_changing_first_manifest(tmp_path, capsys):
    command = [
        "jobs", "create", "-Mode", "beat-sync", "-Name", "demo",
        "-OutputDir", str(tmp_path),
    ]
    assert main(command) == 0
    first_job = Path(capsys.readouterr().out.strip())
    before = (first_job / "job.json").read_text(encoding="utf-8")

    assert main(command) == 0
    second_job = Path(capsys.readouterr().out.strip())

    assert second_job.name == "demo-002.media-job"
    assert (first_job / "job.json").read_text(encoding="utf-8") == before


def test_jobs_inspect_prints_valid_json_without_changing_manifest(tmp_path, capsys):
    main([
        "jobs", "create", "-Mode", "narrate", "-Name", "voice",
        "-OutputDir", str(tmp_path),
    ])
    job_dir = capsys.readouterr().out.strip()
    before = (tmp_path / "voice.media-job" / "job.json").read_text(encoding="utf-8")
    assert main(["jobs", "inspect", job_dir]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "narrate"
    assert payload["status"] == "pending"
    after = (tmp_path / "voice.media-job" / "job.json").read_text(encoding="utf-8")
    assert after == before
