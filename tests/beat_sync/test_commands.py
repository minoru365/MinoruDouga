import pytest

from minoru_studio.beat_sync.models import BeatAnalysis
from minoru_studio.cli import main


def test_create_accepts_powershell_and_gnu_names(monkeypatch, tmp_path, capsys):
    calls = []

    class Service:
        def create_and_prepare(self, request):
            calls.append(request)
            return tmp_path / "demo.media-job"

    monkeypatch.setattr(
        "minoru_studio.beat_sync.commands.BeatSyncService",
        lambda: Service(),
    )
    assert main([
        "beat-sync",
        "-Music",
        str(tmp_path / "song.wav"),
        "--media-dir",
        str(tmp_path / "media"),
        "-EveryN",
        "auto",
        "-Order",
        "asc",
        "-TimelineName",
        "Demo",
        "-Name",
        "demo",
        "-OutputDir",
        str(tmp_path),
    ]) == 0
    assert calls[0].every_n_requested == "auto"
    assert capsys.readouterr().out.strip().endswith("demo.media-job")


def test_resume_calls_service(monkeypatch, tmp_path):
    calls = []

    class Service:
        def resume(self, job_dir):
            calls.append(job_dir)
            return job_dir

    monkeypatch.setattr(
        "minoru_studio.beat_sync.commands.BeatSyncService",
        lambda: Service(),
    )
    assert main(["beat-sync", "resume", str(tmp_path)]) == 0
    assert calls == [tmp_path.resolve()]


def test_estimate_reports_material_count_for_fixed_every_n(
    monkeypatch, tmp_path, capsys
):
    class Analyzer:
        def analyze(self, path):
            return BeatAnalysis(
                4_000, 120.0, (500, 1_000, 1_500, 2_000, 2_500, 3_000, 3_500),
                (0, 500, 1_000, 1_500, 2_000, 2_500, 3_000, 3_500, 4_000),
            )

    monkeypatch.setattr(
        "minoru_studio.beat_sync.commands.LibrosaBeatAnalyzer",
        lambda: Analyzer(),
    )
    assert main([
        "beat-sync",
        "-Music",
        str(tmp_path / "song.wav"),
        "-EveryN",
        "4",
        "estimate",
    ]) == 0
    out = capsys.readouterr().out
    assert "estimated_material_count=2" in out


def test_estimate_rejects_auto_every_n(tmp_path):
    with pytest.raises(SystemExit):
        main([
            "beat-sync",
            "-Music",
            str(tmp_path / "song.wav"),
            "estimate",
        ])


def test_estimate_requires_music(tmp_path):
    with pytest.raises(SystemExit):
        main(["beat-sync", "-EveryN", "4", "estimate"])
