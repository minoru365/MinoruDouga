import json

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
