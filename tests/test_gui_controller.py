import pytest

from minoru_studio import gui
from minoru_studio.gui import LauncherController


def test_controller_creates_and_inspects_pending_job(tmp_path):
    controller = LauncherController()
    job_dir = controller.create_job("repo-demo", "demo", str(tmp_path))
    manifest = controller.inspect_job(str(job_dir))
    assert manifest.mode.value == "repo-demo"
    assert manifest.status.value == "pending"


def test_launch_gui_preserves_a_falsy_injected_controller(monkeypatch):
    class FalsyController:
        def __bool__(self):
            return False

    class TkStarted(Exception):
        pass

    default_constructions = []

    def fail_default_controller():
        default_constructions.append(True)
        raise AssertionError("injected controller was discarded")

    def stop_at_tk():
        raise TkStarted()

    monkeypatch.setattr(gui, "LauncherController", fail_default_controller)
    monkeypatch.setattr(gui.tk, "Tk", stop_at_tk)

    with pytest.raises(TkStarted):
        gui.launch_gui(FalsyController())

    assert default_constructions == []
