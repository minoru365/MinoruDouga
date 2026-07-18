from __future__ import annotations

from pathlib import Path

import pytest

from minoru_studio.transcribe.contracts import WorkerRequest, load_worker_result, save_worker_request
from minoru_studio.transcribe.models import REQUIRED_MODEL_FILES
from minoru_studio.transcribe.worker import ModelNotCachedError, main, run_worker


class FakeWord:
    def __init__(self, start: float, end: float, word: str):
        self.start = start
        self.end = end
        self.word = word


class FakeSegment:
    def __init__(self):
        self.start = 0.5025
        self.end = 1.5005
        self.text = "recognized text must not reach stderr"
        self.words = [FakeWord(0.5025, 1.5005, "recognized text")]


class FakeInfo:
    language = "ja"
    language_probability = 0.99
    duration = 2.0
    duration_after_vad = 1.75


class FakeWhisperModel:
    kwargs: dict[str, str]
    path: str
    transcribe_kwargs: dict[str, object]
    generator_consumed = False

    def __init__(self, path: str, **kwargs: str):
        type(self).path = path
        type(self).kwargs = kwargs

    def transcribe(self, input_wav: str, **kwargs: object):
        type(self).transcribe_kwargs = kwargs

        def segments():
            yield FakeSegment()
            type(self).generator_consumed = True

        return segments(), FakeInfo()


def make_request(tmp_path: Path, *, language: str = "ja", allow_model_download: bool = True) -> tuple[Path, WorkerRequest]:
    request = WorkerRequest(
        schema_version=1,
        input_wav=str(tmp_path / "inference.wav"),
        output_json=str(tmp_path / "result.json"),
        model="small",
        language=language,
        device="cpu",
        compute_type="int8",
        vad_filter=True,
        word_timestamps=True,
        model_cache_dir=str(tmp_path / "models"),
        allow_model_download=allow_model_download,
    )
    path = tmp_path / "request.json"
    save_worker_request(path, request)
    return path, request


def fake_download_model(name: str, *, output_dir: str) -> None:
    directory = Path(output_dir)
    assert directory.name.startswith(".small.partial-")
    directory.mkdir(parents=True)
    for filename in REQUIRED_MODEL_FILES:
        (directory / filename).write_text("model", encoding="utf-8")


def test_worker_downloads_to_staging_consumes_generator_and_writes_integer_results(tmp_path: Path, monkeypatch):
    import minoru_studio.transcribe.worker as worker

    monkeypatch.setattr(worker, "_provider_version", lambda: "1.2.1")
    request_path, request = make_request(tmp_path)

    result = run_worker(
        request_path,
        model_factory=FakeWhisperModel,
        download_model_fn=fake_download_model,
    )

    assert result.model == "small"
    assert result.language == "ja"
    assert result.segments[0].start_ms == 503
    assert result.segments[0].end_ms == 1501
    assert load_worker_result(request.output_json) == result
    assert FakeWhisperModel.path == str(Path(request.model_cache_dir) / "small")
    assert FakeWhisperModel.kwargs == {"device": "cpu", "compute_type": "int8"}
    assert FakeWhisperModel.transcribe_kwargs == {
        "language": "ja", "vad_filter": True, "word_timestamps": True, "beam_size": 5
    }
    assert FakeWhisperModel.generator_consumed


def test_worker_uses_cached_model_and_auto_language_without_download(tmp_path: Path, monkeypatch):
    import minoru_studio.transcribe.worker as worker

    monkeypatch.setattr(worker, "_provider_version", lambda: "1.2.1")
    request_path, request = make_request(tmp_path, language="auto", allow_model_download=False)
    cached = Path(request.model_cache_dir) / "small"
    cached.mkdir(parents=True)
    for filename in REQUIRED_MODEL_FILES:
        (cached / filename).write_text("model", encoding="utf-8")

    result = run_worker(request_path, model_factory=FakeWhisperModel)

    assert result.language == "ja"
    assert FakeWhisperModel.transcribe_kwargs["language"] is None


def test_missing_model_never_downloads_without_authorization(tmp_path: Path):
    request_path, _ = make_request(tmp_path, allow_model_download=False)

    with pytest.raises(ModelNotCachedError):
        run_worker(request_path, download_model_fn=lambda *args, **kwargs: pytest.fail("downloaded"))


def test_no_speech_result_is_empty(tmp_path: Path, monkeypatch):
    import minoru_studio.transcribe.worker as worker

    class NoSpeechModel(FakeWhisperModel):
        def transcribe(self, input_wav: str, **kwargs: object):
            return iter(()), FakeInfo()

    monkeypatch.setattr(worker, "_provider_version", lambda: "1.2.1")
    request_path, _ = make_request(tmp_path)
    result = run_worker(request_path, model_factory=NoSpeechModel, download_model_fn=fake_download_model)

    assert result.no_speech is True
    assert result.segments == ()


def test_module_entry_reports_content_free_cache_error(tmp_path: Path, capsys):
    request_path, _ = make_request(tmp_path, allow_model_download=False)

    assert main([str(request_path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "model is not cached"
    assert "recognized" not in captured.err
