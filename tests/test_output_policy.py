from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

import pytest

from minoru_studio.jobs.model import JobMode
from minoru_studio.jobs.store import JobStore
from minoru_studio.output_paths import default_output_dir


def test_default_output_is_under_user_videos_and_independent_of_cwd(tmp_path, monkeypatch):
    user_dir = tmp_path / "user"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(Path, "home", lambda: user_dir)
    monkeypatch.chdir(checkout)
    assert default_output_dir() == user_dir / "Videos" / "MinoruStudio"
    assert not default_output_dir().is_relative_to(checkout)


def test_job_excludes_private_content_before_first_manifest_write(tmp_path, monkeypatch):
    store = JobStore()
    original = store.save

    def save(job, manifest):
        assert (job / ".gitignore").read_text(encoding="utf-8") == "*\n"
        return original(job, manifest)

    monkeypatch.setattr(store, "save", save)
    job = store.create(tmp_path, "private", JobMode.NARRATE)
    assert (job / "job.json").is_file()


def _git(root, *args):
    if shutil.which("git") is None:
        pytest.skip("git executable not installed")
    with TemporaryDirectory() as temporary:
        excludes = Path(temporary) / "empty-ignore"
        excludes.write_text("", encoding="utf-8")
        return subprocess.run(
            ["git", "-c", f"core.excludesFile={excludes}", "-C", str(root), *args],
            text=True, encoding="utf-8", capture_output=True, check=False,
        )


def test_job_is_self_excluding_even_inside_a_repo_without_ignore_rules(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    initialized = _git(checkout, "init")
    assert initialized.returncode == 0, initialized.stderr
    job = JobStore().create(checkout / "custom-output", "private", JobMode.NARRATE)
    for relative in ("inputs/storyboard.json", "inputs/script.txt", "work/frame.png",
                     "outputs/subtitles.vtt", "outputs/preview.mp4", "logs/run.log"):
        (job / relative).write_bytes(b"private test data")
    result = _git(checkout, "status", "--porcelain", "--untracked-files=all")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    (checkout / "source.py").write_text("# ordinary source\n", encoding="utf-8")
    assert _git(checkout, "check-ignore", "source.py").returncode == 1


def test_repo_ignores_complete_jobs_and_ad_hoc_production_files():
    checkout = Path(__file__).resolve().parents[1]
    paths = ["jobs/demo.media-job/job.json", "anywhere/demo.media-job/inputs/script.txt",
             "paper_video_work/late_sync_checks/report.json", "outputs/storyboard.json",
             "make_paper_video.py", "make_paper_story.ps1", "example_story_narration.txt",
             "preview.MP4", "frame.PNG", "narration.WAV", "captions.vtt"]
    result = _git(checkout, "check-ignore", "--no-index", *paths)
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == set(paths)
    for public_file in ("src/minoru_studio/cli.py", "README.md", "img/demo-narrate.mp4"):
        assert _git(checkout, "check-ignore", "--no-index", public_file).returncode == 1
