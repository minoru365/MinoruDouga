import ast
import subprocess
from pathlib import Path


def test_installer_can_copy_to_disposable_roots(tmp_path):
    app_root = tmp_path / "app"
    utility = tmp_path / "utility"
    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            "install.ps1",
            "-AppRoot",
            str(app_root),
            "-ResolveScriptsRoot",
            str(utility),
            "-SkipSync",
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (
        app_root
        / "resolve_adapter"
        / "minoru_studio_resolve"
        / "entry.py"
    ).is_file()
    assert (utility / "MinoruStudio.py").is_file()


def test_installer_does_not_use_system_pip_or_remove_legacy_launcher():
    source = Path("install.ps1").read_text(encoding="utf-8")
    assert "pip install" not in source
    assert "Remove-Item" not in source
    assert "scripts\\MinoruDouga.py" not in source


def test_utility_launcher_parses_as_python36():
    source = Path("scripts/MinoruStudio.py").read_text(encoding="utf-8")
    ast.parse(source, filename="scripts/MinoruStudio.py", feature_version=(3, 6))
