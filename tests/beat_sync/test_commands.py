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
