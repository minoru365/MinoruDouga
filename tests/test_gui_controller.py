from types import SimpleNamespace
from pathlib import Path

import pytest

from minoru_studio import gui
from minoru_studio.gui import LauncherController
from minoru_studio.transcribe.gui_state import ModelPrompt
from minoru_studio.jobs.model import JobMode, JobStatus
from minoru_studio.narrate.models import DurationWarning


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


def test_controller_estimates_beat_sync_material_count_with_injected_analyzer():
    from minoru_studio.beat_sync.models import BeatAnalysis

    class Analyzer:
        def analyze(self, path):
            assert str(path) == "song.wav"
            return BeatAnalysis(
                4_000, 120.0, (500, 1_000, 1_500, 2_000, 2_500, 3_000, 3_500),
                (0, 500, 1_000, 1_500, 2_000, 2_500, 3_000, 3_500, 4_000),
            )

    class Service:
        analyzer = Analyzer()

    controller = LauncherController(beat_sync_service=Service())
    bpm, duration_ms, count = controller.estimate_beat_sync_material_count(
        music="song.wav", every_n=4
    )
    assert bpm == 120.0
    assert duration_ms == 4_000
    assert count == 2


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


def test_controller_prepares_script_draft_with_injected_service_and_forwards_callbacks(tmp_path):
    calls = []
    events = []

    class Service:
        def create_and_run(self, request, **kwargs):
            calls.append({"request": request, **kwargs})
            kwargs["progress"]("render-draft")
            return tmp_path / "demo.media-job"

    cancel = __import__("threading").Event()
    controller = LauncherController(script_draft_service=Service())

    result = controller.prepare_script_draft(
        input_path="input.mp4",
        name="demo",
        output_dir=str(tmp_path),
        cancel_event=cancel,
        progress=events.append,
    )

    assert result.name == "demo.media-job"
    assert calls[0]["request"].input_path.name == "input.mp4"
    assert calls[0]["cancel_event"] is cancel
    assert events == ["render-draft"]


def test_controller_resumes_script_draft_and_keeps_falsy_injected_service(tmp_path):
    calls = []

    class FalsyService:
        def __bool__(self):
            return False

        def resume(self, job_dir, **kwargs):
            calls.append((job_dir, kwargs))
            return tmp_path / "demo.media-job"

    cancel = __import__("threading").Event()
    controller = LauncherController(script_draft_service=FalsyService())

    assert controller.resume_script_draft(
        str(tmp_path / "demo.media-job"),
        cancel_event=cancel,
    ) == tmp_path / "demo.media-job"
    assert calls[0][1]["cancel_event"] is cancel


def test_controller_prepares_narration_forwards_callbacks_and_reads_warning(monkeypatch, tmp_path):
    calls = []
    events = []
    warning = DurationWarning(1_000, 1_300)

    class Service:
        def create_and_run(self, request, **kwargs):
            calls.append({"request": request, **kwargs})
            kwargs["progress"]("synthesize-utterances")
            return tmp_path / "demo.media-job"

    monkeypatch.setattr(gui, "read_duration_warning", lambda job_dir: warning)
    cancel = __import__("threading").Event()
    controller = LauncherController(narrate_service=Service())

    job_dir, result_warning = controller.prepare_narration(
        input_path="input.mp4", script_path="script.md", name="demo",
        output_dir=str(tmp_path), preview=True, cancel_event=cancel,
        progress=events.append,
    )

    assert job_dir == tmp_path / "demo.media-job"
    assert result_warning is warning
    assert calls[0]["request"].script_path == Path("script.md")
    assert calls[0]["request"].preview is True
    assert calls[0]["cancel_event"] is cancel
    assert events == ["synthesize-utterances"]


def test_narration_progress_message_never_includes_untrusted_service_text():
    untrusted = "# ナレーション\n秘密の台本テキスト"

    message = gui._narration_progress_message(untrusted)

    assert message == "ナレーション: 処理中…"
    assert untrusted not in message


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
            self.bindings = {}
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
        def bind(self, event, callback):
            self.bindings[event] = callback

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
    monkeypatch.setattr(gui, "load_beat_sync_settings", lambda: {"name": "beat-name", "output_dir": "beat-output"})
    monkeypatch.setattr(gui, "load_transcribe_settings", lambda: {"name": "transcribe-name", "output_dir": "transcribe-output"})

    gui.launch_gui(Controller())
    mode_box = next(widget for widget in widgets if "values" in widget.kwargs and len(widget.kwargs["values"]) == len(JobMode))
    name_entry = next(
        widget
        for widget in widgets
        if "textvariable" in widget.kwargs
        and widget.kwargs["textvariable"].get() == "beat-name"
    )
    mode_var = mode_box.kwargs["textvariable"]
    mode_var.set(JobMode.REPO_DEMO.value)
    mode_box.bindings["<<ComboboxSelected>>"]()
    mode_var.set(JobMode.BEAT_SYNC.value)
    mode_box.bindings["<<ComboboxSelected>>"]()
    assert name_entry.kwargs["textvariable"].get() == "beat-name"
    mode_var.set(JobMode.TRANSCRIBE.value)
    mode_box.bindings["<<ComboboxSelected>>"]()
    assert name_entry.kwargs["textvariable"].get() == "transcribe-name"
    buttons["既存ジョブを開く"].command()
    buttons["文字起こしを再開"].command()

    assert calls[0][0] == failed_job
    assert "disabled" in buttons["音ハメ準備を開始"].states
    assert "disabled" in buttons["既存ジョブを開く"].states
    assert "disabled" in mode_box.states
    buttons["文字起こしを開始"].command()
    assert len(calls) == 1


def test_completed_transcription_inspection_resets_to_new_launcher_after_leaving_mode(monkeypatch, tmp_path):
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
            self.bindings = {}
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

        def bind(self, event, callback):
            self.bindings[event] = callback

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

    completed_job = tmp_path / "completed.media-job"
    manifest = SimpleNamespace(
        name="completed",
        mode=JobMode.TRANSCRIBE,
        status=JobStatus.SUCCEEDED,
        settings={"model": "small", "language": "ja"},
    )
    create_calls = []

    class Controller:
        def inspect_job(self, path):
            return manifest

        def transcription_model_prompt(self, model):
            assert model == "small"
            return ModelPrompt(True, model, 0, 0, 0, "cache")

        def prepare_transcription(self, **kwargs):
            create_calls.append(kwargs)
            return tmp_path / "new.media-job"

        def resume_transcription(self, *args, **kwargs):
            raise AssertionError("completed inspection must not resume")

    monkeypatch.setattr(gui.tk, "Tk", Root)
    monkeypatch.setattr(gui.tk, "StringVar", Variable)
    monkeypatch.setattr(gui.tk, "BooleanVar", Variable)
    for name in ("Frame", "Label", "Entry", "Button", "Combobox", "LabelFrame", "Spinbox", "Checkbutton", "Radiobutton", "Separator"):
        monkeypatch.setattr(gui.ttk, name, Widget)
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: str(completed_job))
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: None)
    monkeypatch.setattr(gui.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(gui, "load_beat_sync_settings", lambda: {"name": "beat-name", "output_dir": "beat-output"})
    monkeypatch.setattr(
        gui,
        "load_transcribe_settings",
        lambda: {"input": "input.mp4", "name": "transcribe-name", "output_dir": str(tmp_path)},
    )
    monkeypatch.setattr(gui, "save_transcribe_settings", lambda values: None)

    gui.launch_gui(Controller())
    mode_box = next(widget for widget in widgets if "values" in widget.kwargs and len(widget.kwargs["values"]) == len(JobMode))
    transcribe_button = buttons["文字起こしを開始"]
    mode_var = mode_box.kwargs["textvariable"]
    buttons["既存ジョブを開く"].command()

    assert transcribe_button.text == "完了済みジョブ（確認のみ）"
    assert transcribe_button.states[-1] == "disabled"

    mode_var.set(JobMode.REPO_DEMO.value)
    mode_box.bindings["<<ComboboxSelected>>"]()
    mode_var.set(JobMode.TRANSCRIBE.value)
    mode_box.bindings["<<ComboboxSelected>>"]()

    assert transcribe_button.text == "文字起こしを開始"
    assert transcribe_button.states[-1] == "!disabled"

    transcribe_button.command()

    assert len(create_calls) == 1
