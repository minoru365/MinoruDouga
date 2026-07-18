from types import SimpleNamespace

import pytest

from minoru_studio import gui
from minoru_studio.gui import LauncherController
from minoru_studio.transcribe.gui_state import ModelPrompt
from minoru_studio.jobs.model import JobMode, JobStatus


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


def test_controller_prepares_beat_sync_with_injected_service(tmp_path):
    calls = []

    class Service:
        def create_and_prepare(self, request):
            calls.append(request)
            return tmp_path / "demo.media-job"

    controller = LauncherController(beat_sync_service=Service())
    result = controller.prepare_beat_sync(
        music="song.wav",
        media_dir="media",
        every_n="auto",
        order="asc",
        timeline_name="Demo",
        name="demo",
        output_dir=str(tmp_path),
    )
    assert result.name == "demo.media-job"
    assert calls[0].timeline_name == "Demo"


def test_controller_prepares_transcription_with_injected_service_and_forwards_callbacks(tmp_path):
    calls = []
    events = []

    class Service:
        def create_and_run(self, request, **kwargs):
            calls.append({"request": request, **kwargs})
            kwargs["progress"]("extract-audio")
            return tmp_path / "demo.media-job"

    cancel = __import__("threading").Event()
    controller = LauncherController(transcribe_service=Service())
    result = controller.prepare_transcription(
        input_path="input.mp4", name="demo", output_dir=str(tmp_path),
        model="small", language="ja", normalize=False, denoise=False,
        preview=True, allow_model_download=True, cancel_event=cancel,
        progress=events.append,
    )

    assert result.name == "demo.media-job"
    assert calls[0]["request"].model == "small"
    assert calls[0]["allow_model_download"] is True
    assert calls[0]["cancel_event"] is cancel
    assert events == ["extract-audio"]


def test_controller_resumes_transcription_and_keeps_falsy_injected_service(tmp_path):
    calls = []

    class FalsyService:
        def __bool__(self):
            return False

        def resume(self, job_dir, **kwargs):
            calls.append((job_dir, kwargs))
            return tmp_path / "demo.media-job"

    cancel = __import__("threading").Event()
    controller = LauncherController(transcribe_service=FalsyService())
    assert controller.resume_transcription(
        str(tmp_path / "demo.media-job"), allow_model_download=True,
        cancel_event=cancel,
    ) == tmp_path / "demo.media-job"
    assert calls[0][1]["cancel_event"] is cancel
    assert calls[0][1]["allow_model_download"] is True


def test_controller_builds_model_prompt_without_authorization_data(monkeypatch):
    expected = ModelPrompt(False, "small", 500, 1_000, 2_000, "cache")
    monkeypatch.setattr(gui, "model_prompt", lambda model: expected)

    assert LauncherController().transcription_model_prompt("small") is expected


def test_opened_failed_transcription_resumes_without_validating_or_saving_new_form(monkeypatch, tmp_path):
    buttons = {}
    widgets = []

    class Variable:
        def __init__(self, value=None):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class Widget:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            self.command = kwargs.get("command")
            self.text = kwargs.get("text")
            self.states = []
            if self.text:
                buttons[self.text] = self
            widgets.append(self)

        def grid(self, *args, **kwargs):
            return None

        pack = grid
        grid_remove = grid
        columnconfigure = grid
        bind = grid

        def state(self, values):
            self.states.extend(values)

        def configure(self, **kwargs):
            self.text = kwargs.get("text", self.text)
            if self.text:
                buttons[self.text] = self

    class Root(Widget):
        def title(self, *args):
            return None

        geometry = title
        resizable = title
        mainloop = title

        def after(self, delay, callback):
            callback()

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.target()

    failed_job = tmp_path / "failed.media-job"
    manifest = SimpleNamespace(
        name="failed",
        mode=JobMode.TRANSCRIBE,
        status=JobStatus.FAILED,
        settings={"model": "medium", "language": "ja"},
    )
    calls = []

    class Controller:
        def inspect_job(self, path):
            return manifest

        def transcription_model_prompt(self, model):
            assert model == "medium"
            return ModelPrompt(True, model, 0, 0, 0, "cache")

        def resume_transcription(self, job_dir, **kwargs):
            calls.append((job_dir, kwargs))
            return failed_job

        def prepare_transcription(self, **kwargs):
            raise AssertionError("resume must not prepare a new request")

    monkeypatch.setattr(gui.tk, "Tk", Root)
    monkeypatch.setattr(gui.tk, "StringVar", Variable)
    monkeypatch.setattr(gui.tk, "BooleanVar", Variable)
    for name in ("Frame", "Label", "Entry", "Button", "Combobox", "LabelFrame", "Spinbox", "Checkbutton", "Radiobutton", "Separator"):
        monkeypatch.setattr(gui.ttk, name, Widget)
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: str(failed_job))
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: None)
    monkeypatch.setattr(gui.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(gui, "probe_media", lambda path: pytest.fail("resume must not probe new input"))
    monkeypatch.setattr(gui, "save_transcribe_settings", lambda values: pytest.fail("resume must not save new form"))

    gui.launch_gui(Controller())
    buttons["既存ジョブを開く"].command()
    buttons["文字起こしを再開"].command()

    assert calls[0][0] == failed_job
    assert "disabled" in buttons["音ハメ準備を開始"].states
    assert "disabled" in buttons["既存ジョブを開く"].states
    mode_box = next(widget for widget in widgets if "values" in widget.kwargs and len(widget.kwargs["values"]) == len(JobMode))
    assert "disabled" in mode_box.states
    buttons["文字起こしを開始"].command()
    assert len(calls) == 1
