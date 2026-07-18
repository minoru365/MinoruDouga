import pytest

from minoru_studio.cli import main


def test_version_prints_package_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_empty_argv_prints_help_before_gui_is_added(capsys):
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "usage:" in output
    assert "MinoruStudio" in output
