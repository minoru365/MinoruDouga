# Local Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a resumable, Windows-local `transcribe` mode that turns a video or audio input into TXT/SRT/VTT and, for video input, an optional subtitle-burned MP4 without requiring Resolve.

**Architecture:** The existing CLI/GUI and job store call a `TranscribeService` that runs FFprobe/FFmpeg, starts a MinoruStudio-owned inference worker subprocess, validates its versioned JSON result, and renders artifacts. The worker alone imports `faster-whisper`; it uses a deterministic application model cache and never downloads unless the current CLI flag or GUI confirmation authorizes the selected model.

**Tech Stack:** Python 3.12, pytest, uv, `faster-whisper` 1.2.x/CTranslate2 CPU INT8, FFmpeg/FFprobe 8.x, Tkinter, JSON job packages

## Global Constraints

- Target Windows and Python `>=3.12,<3.13`; do not add macOS/Linux behavior in this phase.
- Add `faster-whisper>=1.2.1,<1.3` to the existing application-specific uv environment; never install it into Resolve or system Python.
- Keep the package version at `0.2.0` until automated gates and real local acceptance pass; only Task 10 promotes `0.3.0`.
- Default to model `small`, language `ja`, device `cpu`, compute type `int8`, VAD enabled, and word timestamps enabled.
- Store every persisted media time as a non-negative integer millisecond; never persist float seconds.
- Store models below `%LOCALAPPDATA%\MinoruStudio\models\faster-whisper\` and perform no network access without `-AllowModelDownload` or the current GUI confirmation.
- Never alter or delete source media, an existing job, or a successful artifact; new jobs use the existing `-002` suffix policy.
- Do not log transcript, segment, word, SRT, or VTT text.
- TXT preserves recognized wording; SRT/VTT allow at most 21 Unicode code points per line and two lines per cue.
- `-Preview` requires a video stream and creates a new MP4; audio-only preview fails before inference.
- Resolve subtitle placement, VOICEVOX, diarization, translation, live transcription, GPU setup, and cloud providers remain out of scope.
- The installed machine currently has `Gyan.FFmpeg 8.1.2` with `subtitles`, `afftdn`, and `loudnorm`; code must still detect missing tools and fail clearly.
- Any dependency download or real model download requires the normal tool approval at execution time.

---

### Task 1: Shared Millisecond Conversion and Worker Contracts

**Files:**
- Modify: `src/minoru_studio/timebase.py`
- Modify: `src/minoru_studio/beat_sync/analyzer.py`
- Create: `src/minoru_studio/transcribe/__init__.py`
- Create: `src/minoru_studio/transcribe/contracts.py`
- Modify: `tests/test_timebase.py`
- Modify: `tests/beat_sync/test_analyzer.py`
- Create: `tests/transcribe/__init__.py`
- Create: `tests/transcribe/test_contracts.py`

**Interfaces:**
- Produces: `timebase.seconds_to_milliseconds(value: float) -> int` using decimal half-up rounding.
- Produces: immutable `WorkerRequest`, `WordResult`, `SegmentResult`, and `WorkerResult` dataclasses.
- Produces: `save_worker_request`, `load_worker_request`, `save_worker_result`, and `load_worker_result` atomic JSON functions.
- Consumes: existing job paths; every request path must be absolute and every result timestamp must already be integer milliseconds.

- [ ] **Step 1: Write failing shared-time and contract tests**

Add tests proving exact half-up conversion, invalid float rejection, contract round-trip, boolean-as-integer rejection, timestamp ordering, path absoluteness, and unknown schema rejection. The core fixtures are:

```python
def test_seconds_to_milliseconds_uses_decimal_half_up():
    assert seconds_to_milliseconds(0.5024) == 502
    assert seconds_to_milliseconds(0.5025) == 503


def test_worker_result_round_trip_requires_integer_monotonic_times(tmp_path):
    result = WorkerResult(
        schema_version=1,
        model="small",
        provider_version="1.2.1",
        language="ja",
        language_probability=0.99,
        duration_ms=2_000,
        duration_after_vad_ms=1_600,
        no_speech=False,
        segments=(
            SegmentResult(
                start_ms=100,
                end_ms=800,
                text="こんにちは。",
                words=(WordResult(100, 800, "こんにちは。"),),
            ),
        ),
    )
    path = tmp_path / "raw-segments.json"
    save_worker_result(path, result)
    assert load_worker_result(path) == result


@pytest.mark.parametrize("bad", [True, 1.5, -1])
def test_worker_result_rejects_invalid_milliseconds(tmp_path, bad):
    payload = valid_result_payload()
    payload["segments"][0]["start_ms"] = bad
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ContractError):
        load_worker_result(path)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
uv run pytest tests/test_timebase.py tests/beat_sync/test_analyzer.py tests/transcribe/test_contracts.py -q
```

Expected: FAIL because the common converter and transcribe contract module do not exist.

- [ ] **Step 3: Implement the common converter and versioned JSON contract**

Add this common converter and retain a compatibility wrapper in `beat_sync.analyzer` that converts `ValueError` to `AnalysisError`:

```python
def seconds_to_milliseconds(value: float) -> int:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError("time values must be finite and non-negative")
    milliseconds = Decimal(str(numeric)) * 1_000
    return int(milliseconds.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
```

Define these exact contract fields in `contracts.py`:

```python
WORKER_SCHEMA_VERSION = 1


class ContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    schema_version: int
    input_wav: str
    output_json: str
    model: str
    language: str
    device: str
    compute_type: str
    vad_filter: bool
    word_timestamps: bool
    model_cache_dir: str
    allow_model_download: bool


@dataclass(frozen=True, slots=True)
class WordResult:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class SegmentResult:
    start_ms: int
    end_ms: int
    text: str
    words: tuple[WordResult, ...]


@dataclass(frozen=True, slots=True)
class WorkerResult:
    schema_version: int
    model: str
    provider_version: str
    language: str
    language_probability: float
    duration_ms: int
    duration_after_vad_ms: int
    no_speech: bool
    segments: tuple[SegmentResult, ...]
```

Validation must reject booleans for integer fields, non-finite probabilities, negative/out-of-duration times, `end_ms < start_ms`, overlapping or out-of-order segments/words, blank non-no-speech text, unknown model/device/compute values, relative request paths, and a `no_speech` result containing segments. JSON writes use a sibling UUID temporary file plus `os.replace`, `ensure_ascii=False`, sorted keys, and a final newline.

- [ ] **Step 4: Run focused and regression tests**

Run:

```powershell
uv run pytest tests/test_timebase.py tests/beat_sync/test_analyzer.py tests/transcribe/test_contracts.py -q
uv run pytest tests/beat_sync -q
```

Expected: all selected tests pass; beat-sync conversion remains unchanged.

- [ ] **Step 5: Commit Task 1**

```powershell
git add src/minoru_studio/timebase.py src/minoru_studio/beat_sync/analyzer.py src/minoru_studio/transcribe tests/test_timebase.py tests/beat_sync/test_analyzer.py tests/transcribe
git commit -m "feat: define transcription worker contracts"
```

---

### Task 2: Cancellable External Process Execution

**Files:**
- Modify: `src/minoru_studio/processes.py`
- Modify: `tests/test_processes.py`

**Interfaces:**
- Produces: `ProcessCancelledError(display_command: str)` that contains no captured output.
- Produces: `run_cancellable_process(args, *, cwd=None, timeout_s=None, secrets=(), cancel_event=None, poll_interval_s=0.1) -> ProcessResult`.
- Preserves: current `run_process` behavior and `ProcessTimeoutError` contract.

- [ ] **Step 1: Add failing cancellation and timeout tests**

Use a real child Python process and injected `threading.Event`:

```python
def test_cancellable_process_terminates_without_exposing_output(tmp_path):
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    try:
        with pytest.raises(ProcessCancelledError) as raised:
            run_cancellable_process(
                [sys.executable, "-c", "import time; print('private'); time.sleep(30)"],
                cancel_event=cancel,
                poll_interval_s=0.02,
            )
    finally:
        timer.cancel()
    assert "private" not in str(raised.value)


def test_cancellable_process_returns_captured_result():
    result = run_cancellable_process(
        [sys.executable, "-c", "print('ok')"],
        timeout_s=5,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
```

Also assert an already-set event starts no child, a timeout terminates the child, secret-shaped arguments stay redacted, and an empty `args` sequence fails before `Popen`. Assert `KeyboardInterrupt` during `communicate` terminates the child and is converted to `ProcessCancelledError` so Ctrl+C cannot leave an inference process running.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_processes.py -q`

Expected: FAIL because the cancellable API is missing.

- [ ] **Step 3: Implement a Popen polling loop**

Keep `shell=False`, UTF-8 replacement decoding, `CREATE_NO_WINDOW`, and display redaction. Use this control shape:

```python
while True:
    if cancel_event is not None and cancel_event.is_set():
        _terminate_process(process)
        process.communicate()
        raise ProcessCancelledError(display)
    if deadline is not None and time.monotonic() >= deadline:
        _terminate_process(process)
        process.communicate()
        raise ProcessTimeoutError(timeout_s, display)
    try:
        stdout, stderr = process.communicate(timeout=poll_interval_s)
        break
    except subprocess.TimeoutExpired:
        continue
```

`_terminate_process` calls `terminate()`, waits up to five seconds, then calls `kill()` and waits. Check cancellation once before `Popen`. Do not include stdout/stderr in cancellation or timeout exceptions. Leave `run_process` in place for short existing doctor calls.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_processes.py -q`

Expected: all process tests pass with no orphaned Python process.

- [ ] **Step 5: Commit Task 2**

```powershell
git add src/minoru_studio/processes.py tests/test_processes.py
git commit -m "feat: add cancellable process execution"
```

---

### Task 3: FFprobe, Audio Extraction, and Preview Pipeline

**Files:**
- Create: `src/minoru_studio/transcribe/media.py`
- Create: `tests/transcribe/test_media.py`
- Create: `tests/integration/test_transcribe_ffmpeg.py`

**Interfaces:**
- Produces: `MediaInfo(duration_ms: int, has_audio: bool, has_video: bool)`.
- Produces: `MediaToolVersions(ffmpeg: str, ffprobe: str)` and `read_media_tool_versions()`.
- Produces: `probe_media(path, runner=run_process) -> MediaInfo`.
- Produces: `extract_audio(source, destination, *, normalize, denoise, runner, cancel_event) -> Path`.
- Produces: `resolve_japanese_font(windows_dir: Path | None = None) -> FontChoice`.
- Produces: `render_preview(source, subtitles, destination, media_info, *, runner, cancel_event) -> Path`.
- Produces: `validate_preview(path, source_duration_ms, runner=run_process) -> None`.

- [ ] **Step 1: Write failing media tests with captured command arguments**

Tests must parse FFprobe JSON by stream type rather than extension and assert the exact no-filter extraction command:

```python
assert calls[0] == [
    "ffmpeg", "-nostdin", "-v", "error", "-y",
    "-i", str(source), "-map", "0:a:0", "-vn",
    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(temp_wav),
]
```

Add tests for missing audio, invalid/zero duration, audio-only preview rejection, first-line FFmpeg/FFprobe version capture, `afftdn=nr=10:nf=-80:tn=1`, two-pass `loudnorm=I=-16:LRA=11:TP=-1.5`, denoise-before-normalize ordering, malformed first-pass statistics, clipped PCM rejection, Japanese font priority, Windows filter-path escaping, and preview stream/duration validation at the inclusive 250 ms boundary.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/transcribe/test_media.py -q`

Expected: FAIL because `transcribe.media` does not exist.

- [ ] **Step 3: Implement probe and atomic media outputs**

Use these exact constants and records:

```python
DENOISE_FILTER = "afftdn=nr=10:nf=-80:tn=1"
LOUDNESS_TARGET = "loudnorm=I=-16:LRA=11:TP=-1.5"
PREVIEW_DURATION_TOLERANCE_MS = 250


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration_ms: int
    has_audio: bool
    has_video: bool


@dataclass(frozen=True, slots=True)
class FontChoice:
    family: str
    file: Path


@dataclass(frozen=True, slots=True)
class MediaToolVersions:
    ffmpeg: str
    ffprobe: str
```

`probe_media` calls:

```powershell
ffprobe -v error -show_entries format=duration:stream=codec_type -of json <input>
```

Extraction writes a UUID `.wav` sibling in `work/`, validates RIFF PCM, 16 kHz, mono, 16-bit, positive frames, and no sample at `-32768` or `32767`, then atomically replaces `inference.wav`. Normalization first runs a null-output measurement pass, parses the final JSON object from stderr, requires finite `input_i`, `input_lra`, `input_tp`, `input_thresh`, and `target_offset`, then constructs the measured second pass with `linear=true`. Both passes include denoise first when requested.

Font resolution checks `%WINDIR%\Fonts\YuGothR.ttc`, then `meiryo.ttc`, then `msgothic.ttc`, returning `Yu Gothic`, `Meiryo`, or `MS Gothic`. The preview command explicitly maps `0:v:0` and `0:a:0`, uses `libx264`, `yuv420p`, AAC, `+faststart`, the selected `fontsdir`, and `force_style=FontName=<family>`. It writes a UUID `.mp4` sibling and replaces the destination only after FFprobe validation.

- [ ] **Step 4: Run unit tests and the optional real FFmpeg integration test**

Run:

```powershell
uv run pytest tests/transcribe/test_media.py -q
uv run pytest tests/integration/test_transcribe_ffmpeg.py -q
```

Expected: unit tests pass. Integration test passes when `ffmpeg`/`ffprobe` resolve, otherwise it skips with the explicit reason `FFmpeg tools not on PATH`. When running Codex in the managed sandbox, use an approved unsandboxed test invocation so the installed WinGet package is visible; do not reinstall it.

- [ ] **Step 5: Commit Task 3**

```powershell
git add src/minoru_studio/transcribe/media.py tests/transcribe/test_media.py tests/integration/test_transcribe_ffmpeg.py
git commit -m "feat: prepare transcription media"
```

---

### Task 4: Transcript, SRT, and VTT Rendering

**Files:**
- Create: `src/minoru_studio/transcribe/subtitles.py`
- Create: `tests/transcribe/test_subtitles.py`

**Interfaces:**
- Consumes: validated `WorkerResult` from Task 1.
- Produces: immutable `Cue(start_ms: int, end_ms: int, lines: tuple[str, ...])`.
- Produces: `build_cues(result: WorkerResult) -> tuple[Cue, ...]`.
- Produces: `render_transcript`, `render_srt`, `render_vtt`, and `write_artifacts(job_dir, result) -> tuple[Path, Path, Path]`.

- [ ] **Step 1: Write failing renderer tests**

Cover transcript wording/order, strong punctuation flush, soft punctuation preferred near 42 characters, hard splitting, 21-character line wrap, two-line maximum, word timestamp use, proportional segment fallback, overlap repair, impossible positive interval failure, empty no-speech outputs, SRT commas, VTT periods/header, consecutive SRT indexes, and atomic replacement.

Use this boundary assertion:

```python
def test_cues_wrap_at_twenty_one_code_points_and_two_lines():
    result = result_with_segment("あ" * 42, start_ms=100, end_ms=2_100)
    cues = build_cues(result)
    assert cues[0].lines == ("あ" * 21, "あ" * 21)
    assert all(len(line) <= 21 for cue in cues for line in cue.lines)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/transcribe/test_subtitles.py -q`

Expected: FAIL because the renderer module is missing.

- [ ] **Step 3: Implement deterministic character/timestamp formatting**

Define:

```python
MAX_LINE_CODEPOINTS = 21
MAX_CUE_CODEPOINTS = 42
STRONG_BREAKS = frozenset("。！？!?")
SOFT_BREAKS = frozenset("、，,；;：:")


@dataclass(frozen=True, slots=True)
class Cue:
    start_ms: int
    end_ms: int
    lines: tuple[str, ...]
```

Expand valid word spans into timed Unicode code points by distributing each word interval with integer division and assigning remainder milliseconds in character order. If a segment has no valid word spans, distribute its interval across non-space text code points in the same way. Accumulate through strong punctuation or 42 code points; on overflow, split at the latest soft/strong break already in the buffer, otherwise hard-split at 42. Wrap each cue once at 21.

Clamp each cue start to the preceding cue end. Merge a zero-length rounded cue into an adjacent cue only when the merged text is at most 42 code points; otherwise extend into available neighboring silence. Raise `SubtitleError` if no positive, ordered interval can be assigned without reordering characters.

Write all three artifacts to UUID siblings and replace destinations only after parsing the generated SRT/VTT back into the same cue sequence. Empty no-speech SRT is a zero-cue file; empty VTT contains `WEBVTT` plus a blank line.

- [ ] **Step 4: Run renderer and contract tests**

Run:

```powershell
uv run pytest tests/transcribe/test_subtitles.py tests/transcribe/test_contracts.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 4**

```powershell
git add src/minoru_studio/transcribe/subtitles.py tests/transcribe/test_subtitles.py
git commit -m "feat: render transcription subtitles"
```

---

### Task 5: Model Cache, Faster-Whisper Worker, and Doctor Check

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/minoru_studio/transcribe/models.py`
- Create: `src/minoru_studio/transcribe/worker.py`
- Modify: `src/minoru_studio/doctor.py`
- Create: `tests/transcribe/test_models.py`
- Create: `tests/transcribe/test_worker.py`
- Modify: `tests/test_doctor.py`

**Interfaces:**
- Produces: `ModelSpec(name, estimated_download_bytes, required_free_bytes)` for `small` and `medium`.
- Produces: `default_model_cache_dir`, `model_directory`, `model_is_complete`, and `require_model_capacity`.
- Produces: `run_worker(request_path, *, model_factory=None, download_model_fn=None) -> WorkerResult`.
- Produces: module entry `python -m minoru_studio.transcribe.worker <worker-request.json>`.
- Consumes: Task 1 JSON contract and common millisecond conversion.

- [ ] **Step 1: Add failing model, worker, and doctor tests before the dependency**

Tests use fake provider objects and must not import/download a real model. Cover `%LOCALAPPDATA%` cache location, exact model estimates (500 MB/1 GB and 1.5 GB/3 GB), required cache files, free-space rejection, no-download cache miss, staging-directory download, partial download not becoming complete, cached local path loading, Japanese versus auto language argument, VAD/word timestamp arguments, generator consumption, integer-ms conversion, no-speech result, generic stderr without recognized text, and doctor metadata presence.

Core fake inference test:

```python
result = run_worker(
    request_path,
    model_factory=FakeWhisperModel,
    download_model_fn=fake_download_model,
)
assert result.model == "small"
assert result.language == "ja"
assert result.segments[0].start_ms == 503
assert load_worker_result(request.output_json) == result
assert FakeWhisperModel.kwargs == {"device": "cpu", "compute_type": "int8"}
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/transcribe/test_models.py tests/transcribe/test_worker.py tests/test_doctor.py -q
```

Expected: FAIL because model/worker APIs and the doctor check are missing.

- [ ] **Step 3: Add and lock the provider dependency**

Change the project dependency list to include:

```toml
dependencies = [
  "faster-whisper>=1.2.1,<1.3",
  "librosa>=0.11,<0.12",
]
```

Run `uv lock` followed by `uv sync --locked --dev`. This is an external dependency download and must use the execution environment's approval mechanism. Expected: lock succeeds on Python 3.12 and imports `faster_whisper` 1.2.x.

- [ ] **Step 4: Implement deterministic cache and worker behavior**

Use these model constants:

```python
MODEL_SPECS = {
    "small": ModelSpec("small", 500_000_000, 1_000_000_000),
    "medium": ModelSpec("medium", 1_500_000_000, 3_000_000_000),
}
REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json")
```

The final directory is `<cache>/<model>`. A permitted missing-model download goes to `<cache>/.<model>.partial-<uuid>` through `faster_whisper.utils.download_model(model, output_dir=...)`, validates required files, and uses `os.replace` to publish it. A concurrent winner's already complete final directory is accepted; partial directories are never accepted as cached models.

Cached inference constructs `WhisperModel(str(final_model_dir), device="cpu", compute_type="int8")` and calls:

```python
segments, info = model.transcribe(
    str(input_wav),
    language=None if request.language == "auto" else request.language,
    vad_filter=True,
    word_timestamps=True,
    beam_size=5,
)
```

Consume the lazy segment generator before writing output. Convert every provider time with the common half-up converter. `main()` prints only stable categories such as `model is not cached`, `model download failed`, or `inference worker failed`; it never prints exception strings or recognized content.

Doctor uses `importlib.metadata.version("faster-whisper")`, marks missing/import-incompatible as a required failure, and reports cached models only as an informational message on the successful dependency check.

- [ ] **Step 5: Run focused tests and dependency verification**

Run:

```powershell
uv run pytest tests/transcribe/test_models.py tests/transcribe/test_worker.py tests/test_doctor.py -q
uv run python -c "import faster_whisper; from importlib.metadata import version; print(version('faster-whisper'))"
uv run minoru-studio doctor --json
```

Expected: tests pass; version is in `[1.2.1,1.3)`; doctor reports `faster-whisper` OK. FFmpeg checks must also be OK in the approved unsandboxed environment.

- [ ] **Step 6: Commit Task 5**

```powershell
git add pyproject.toml uv.lock src/minoru_studio/transcribe/models.py src/minoru_studio/transcribe/worker.py src/minoru_studio/doctor.py tests/transcribe/test_models.py tests/transcribe/test_worker.py tests/test_doctor.py
git commit -m "feat: add local transcription worker"
```

---

### Task 6: Resumable Transcribe Service

**Files:**
- Create: `src/minoru_studio/transcribe/service.py`
- Modify: `src/minoru_studio/jobs/model.py`
- Create: `tests/transcribe/test_service.py`
- Modify: `tests/jobs/test_model.py`

**Interfaces:**
- Produces: `TranscribeRequest(input_path, name, output_dir, model, language, normalize, denoise, preview)`.
- Produces: `TranscribeService.create_and_run(request, *, allow_model_download=False, cancel_event=None, progress=None) -> Path`.
- Produces: `TranscribeService.resume(job_dir, *, allow_model_download=False, cancel_event=None, progress=None) -> Path`.
- Produces: content-free `TranscriptionFailed(job_dir, category)` and `TranscriptionInterrupted(job_dir)` exceptions.
- Produces: backward-compatible optional `JobManifest.tools: dict[str, str]` provenance field.
- Consumes: Tasks 1-5 contracts, media functions, model checks, worker subprocess, subtitle renderer, job store, logger, and artifact fingerprints.

- [ ] **Step 1: Write failing state-machine tests with injected collaborators**

Cover all five ordered steps, success artifacts, preview omission, audio-only preflight failure before worker, input fingerprint change, mode/status rejection, failed/interrupted resume, successful step reuse only after validation, invalid work-file rerun from that step, ephemeral download permission on resume, concurrent RUNNING rejection, cancellation to `interrupted`, ordinary exception to `failed`, empty no-speech success, provider/FFmpeg/FFprobe/Python provenance, old manifests without `tools`, transcript absence from `run.log`/`last_error`, and source hash preservation.

The success assertion is:

```python
manifest = JobStore().load(job_dir, recover_interrupted=False)
assert manifest.status is JobStatus.SUCCEEDED
assert list(manifest.steps) == [
    "probe-input", "extract-audio", "transcribe", "render-artifacts"
]
assert {item.kind for item in manifest.artifacts} == {
    "transcript-txt", "subtitles-srt", "subtitles-vtt"
}
```

- [ ] **Step 2: Run service tests and verify RED**

Run: `uv run pytest tests/transcribe/test_service.py -q`

Expected: FAIL because `TranscribeService` is missing.

- [ ] **Step 3: Implement the service with atomic per-step transitions**

Define the request exactly:

```python
@dataclass(frozen=True, slots=True)
class TranscribeRequest:
    input_path: Path
    name: str
    output_dir: Path
    model: str = "small"
    language: str = "ja"
    normalize: bool = False
    denoise: bool = False
    preview: bool = False
```

Create the job with one input and settings containing `model`, `language`, `device="cpu"`, `compute_type="int8"`, `vad_filter=True`, `word_timestamps=True`, filter booleans/constant strings, and preview/font selection. Persist step work as `work/media-info.json`, `work/inference.wav`, and `work/raw-segments.json` so validation can decide whether to reuse a completed step.

Add `tools: dict[str, str] = field(default_factory=dict)` to `JobManifest`, serialize it normally, and load it with `dict(data.get("tools", {}))` so every existing schema-version-1 job remains readable. On successful probe/transcription, record Python, FFmpeg, FFprobe, and faster-whisper versions in this field; do not place mutable version evidence in output-affecting settings.

At the start of create/resume, one `JobStore.update` atomically rejects an already RUNNING manifest and sets RUNNING. For each step: validate reusable output; otherwise mark RUNNING, call the collaborator, validate output, then mark SUCCEEDED with timestamps/exit code. Reset the first invalid step and all following step records/artifact registrations before rerunning. Never include recognized text in progress callbacks or exceptions.

Invoke the worker with:

```python
[
    sys.executable,
    "-m",
    "minoru_studio.transcribe.worker",
    str(job_dir / "work" / "worker-request.json"),
]
```

If the selected model is incomplete and the current permission is false, fail with `selected model is not cached; rerun with -AllowModelDownload` before starting the worker. Check free space before an authorized download. Cancellation marks the running step and job INTERRUPTED, then raises `TranscriptionInterrupted`; other failures mark both FAILED and raise `TranscriptionFailed` with a stable category such as `input validation`, `FFmpeg`, `model unavailable`, `worker`, `subtitle rendering`, or `preview`. The service must not call `logger.exception` and must not persist arbitrary collaborator/provider exception strings: log only step name, category, exception class, exit code, and content-free paths. A final success fingerprints all required artifacts and sets SUCCEEDED only after validation.

- [ ] **Step 4: Run service and foundation tests**

Run:

```powershell
uv run pytest tests/transcribe/test_service.py -q
uv run pytest tests/jobs tests/test_processes.py tests/test_redaction.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 6**

```powershell
git add src/minoru_studio/transcribe/service.py src/minoru_studio/jobs/model.py tests/transcribe/test_service.py tests/jobs/test_model.py
git commit -m "feat: orchestrate resumable transcription jobs"
```

---

### Task 7: Non-Interactive Transcribe CLI

**Files:**
- Create: `src/minoru_studio/transcribe/commands.py`
- Modify: `src/minoru_studio/cli.py`
- Create: `tests/transcribe/test_commands.py`

**Interfaces:**
- Produces canonical create command `minoru-studio transcribe <input> ...`.
- Produces resume command `minoru-studio transcribe resume <job-dir> [-AllowModelDownload]`.
- Consumes: Task 6 `TranscribeService`; non-interactive errors never open GUI/dialogs.

- [ ] **Step 1: Write failing CLI tests**

Cover PowerShell/GNU aliases, defaults, `small|medium`, `ja|auto|<two-or-three-letter-code>`, required Name/OutputDir, create request mapping, resume with ephemeral download permission, extra positional rejection, service failure exit 1/stderr, and output of the absolute job path.

```python
assert main([
    "transcribe", str(tmp_path / "input.mp4"),
    "-Name", "demo", "--output-dir", str(tmp_path),
]) == 0
assert calls[0].model == "small"
assert calls[0].language == "ja"
assert calls[0].normalize is False
```

- [ ] **Step 2: Run CLI tests and verify RED**

Run: `uv run pytest tests/transcribe/test_commands.py -q`

Expected: argparse rejects the missing subcommand.

- [ ] **Step 3: Implement parser and command dispatch**

Use two positionals, `input_or_action` and optional `resume_job`, so both canonical forms remain unambiguous. If `input_or_action == "resume"`, require `resume_job` and reject create-only flags. Otherwise reject a second positional and require Name/OutputDir.

Add exact option aliases:

```python
parser.add_argument("-Model", "--model", choices=("small", "medium"), default="small")
parser.add_argument("-Language", "--language", type=parse_language, default="ja")
parser.add_argument("-Normalize", "--normalize", action="store_true")
parser.add_argument("-Denoise", "--denoise", action="store_true")
parser.add_argument("-Preview", "--preview", action="store_true")
parser.add_argument(
    "-AllowModelDownload", "--allow-model-download",
    dest="allow_model_download", action="store_true",
)
```

`parse_language` case-folds `auto`, otherwise accepts only two or three ASCII letters and returns lower-case; the worker/provider rejects unsupported codes without fallback. Catch `TranscriptionFailed` and print its content-free message to stderr with exit 1. Catch `TranscriptionInterrupted`, print the resumable job path, and return exit 130.

- [ ] **Step 4: Run CLI and regression tests**

Run:

```powershell
uv run pytest tests/transcribe/test_commands.py tests/test_cli.py -q
uv run minoru-studio transcribe --help
```

Expected: tests pass and help lists all approved flags without opening GUI.

- [ ] **Step 5: Commit Task 7**

```powershell
git add src/minoru_studio/transcribe/commands.py src/minoru_studio/cli.py tests/transcribe/test_commands.py
git commit -m "feat: add transcribe CLI"
```

---

### Task 8: Launcher GUI, Settings, Progress, and Cancel

**Files:**
- Create: `src/minoru_studio/transcribe/settings.py`
- Create: `src/minoru_studio/transcribe/gui_state.py`
- Modify: `src/minoru_studio/beat_sync/settings.py`
- Modify: `src/minoru_studio/gui.py`
- Create: `tests/transcribe/test_settings.py`
- Create: `tests/transcribe/test_gui_state.py`
- Modify: `tests/beat_sync/test_settings.py`
- Modify: `tests/test_gui_controller.py`

**Interfaces:**
- Produces: allow-listed transcribe GUI settings with no download permission persistence.
- Produces: `TranscribeFormValues.to_request() -> TranscribeRequest` and `ModelPrompt` data for a pure, testable UI boundary.
- Adds controller methods `prepare_transcription`, `resume_transcription`, and `transcription_model_prompt`.
- Consumes: Task 6 service callbacks and a `threading.Event` for cancellation.

- [ ] **Step 1: Write failing pure-state and controller tests**

Assert settings persist only `input`, `name`, `output_dir`, `model`, `language`, `normalize`, `denoise`, and `preview`; reject blank input/name/output, invalid model/language, and preview with a probed audio-only input. Add migration tests proving legacy flat beat-sync settings still load, saving either mode preserves the other mode's section, and secret/authorization keys are dropped. Assert model prompt contains name, estimate, required free bytes, cache path, and cached flag but no authorization state. Assert injected transcribe service is used, progress is forwarded, and cancel event identity is preserved.

```python
controller = LauncherController(transcribe_service=service)
controller.prepare_transcription(
    input_path="input.mp4", name="demo", output_dir=str(tmp_path),
    model="small", language="ja", normalize=False, denoise=False,
    preview=True, allow_model_download=True,
    cancel_event=cancel, progress=events.append,
)
assert calls[0]["cancel_event"] is cancel
assert calls[0]["allow_model_download"] is True
```

- [ ] **Step 2: Run GUI-boundary tests and verify RED**

Run:

```powershell
uv run pytest tests/transcribe/test_settings.py tests/transcribe/test_gui_state.py tests/test_gui_controller.py -q
```

Expected: FAIL because the transcribe GUI modules/controller methods are absent.

- [ ] **Step 3: Implement pure settings/form state**

Change the shared config shape to top-level `beat_sync` and `transcribe` objects. `beat_sync.settings.load_settings` accepts the existing legacy flat object and returns the same public dictionary; its next save migrates that data into `beat_sync` while preserving an existing `transcribe` object. `transcribe.settings` uses the same UUID-temporary/`os.replace` writer and preserves `beat_sync`. Both writers reload the latest file immediately before replacing it and filter their own section through an allow-list. The transcribe allow-list excludes `allow_model_download`, transcript content, and model-cache internals.

Define:

```python
@dataclass(frozen=True, slots=True)
class TranscribeFormValues:
    input_path: str
    name: str
    output_dir: str
    model: str
    language: str
    normalize: bool
    denoise: bool
    preview: bool


@dataclass(frozen=True, slots=True)
class ModelPrompt:
    cached: bool
    model: str
    estimated_download_bytes: int
    required_free_bytes: int
    free_bytes: int
    cache_dir: str
```

The pure conversion validates strings/choices and constructs Task 6's request. Model prompt calculation performs filesystem/cache checks but no network access.

- [ ] **Step 4: Add the transcribe panel and responsive lifecycle**

In `gui.py`, inject `TranscribeService` without replacing a falsy injected service/controller. Add a transcribe label frame with input picker, model/language controls, three checkboxes, start/resume button, and cancel button. Mode switching shows exactly the selected mode panel.

On start: validate form, save allow-listed settings, inspect the model prompt, show `messagebox.askyesno` only when missing, reject insufficient space, create a fresh `threading.Event`, disable mutable fields, and run the controller in a daemon thread. Progress callbacks use `root.after(0, ...)` and display only step names. Cancel sets the event once and changes status to `キャンセル中…`; it does not mark state itself. Success/failure/interrupted callbacks re-enable controls.

When `既存ジョブを開く` loads a failed/interrupted transcribe job, retain its path and expose `文字起こしを再開`. For a missing model, repeat the current confirmation; do not reuse an earlier answer. A succeeded job is inspect-only.

- [ ] **Step 5: Run GUI tests and manual smoke launch**

Run:

```powershell
uv run pytest tests/transcribe/test_settings.py tests/transcribe/test_gui_state.py tests/beat_sync/test_settings.py tests/test_gui_controller.py -q
uv run python -m minoru_studio
```

Expected: automated tests pass. Manual smoke shows beat-sync unchanged, transcribe controls switch correctly, and closing the window starts no job. Close the GUI without downloading a model.

- [ ] **Step 6: Commit Task 8**

```powershell
git add src/minoru_studio/transcribe/settings.py src/minoru_studio/transcribe/gui_state.py src/minoru_studio/beat_sync/settings.py src/minoru_studio/gui.py tests/transcribe/test_settings.py tests/transcribe/test_gui_state.py tests/beat_sync/test_settings.py tests/test_gui_controller.py
git commit -m "feat: add transcription launcher UI"
```

---

### Task 9: Automated End-to-End Gate and Acceptance Runbook

**Files:**
- Create: `tests/acceptance/test_transcribe_preparation.py`
- Create: `docs/transcribe-acceptance.md`
- Modify: `README.md`

**Interfaces:**
- Produces: a fake-worker end-to-end test across CLI, job state, media fixtures, artifacts, preview validation, and source preservation.
- Produces: a real-model acceptance checklist with recorded evidence fields.
- Keeps: README workflow labeled pre-release and package version `0.2.0` until Task 10.

- [ ] **Step 1: Write the end-to-end acceptance test first**

Generate a one-second color video with a sine-wave audio track through the installed FFmpeg, inject a worker result containing Japanese text, invoke the real CLI/service/renderers, and assert:

```python
assert manifest.status.value == "succeeded"
assert (job_dir / "outputs" / "transcript.txt").read_text(encoding="utf-8")
assert (job_dir / "outputs" / "subtitles.srt").exists()
assert (job_dir / "outputs" / "subtitles.vtt").exists()
assert (job_dir / "outputs" / "preview.mp4").exists()
assert fingerprint_file(source) == source_before
assert "受入テスト" not in (job_dir / "logs" / "run.log").read_text(encoding="utf-8")
```

Also run an audio-only job without preview, a no-speech job, a changed-input resume rejection, and two same-name creates producing `-002`.

- [ ] **Step 2: Run the acceptance test and close integration gaps**

Run:

```powershell
uv run pytest tests/acceptance/test_transcribe_preparation.py -q
```

Expected: PASS in an approved unsandboxed invocation where WinGet FFmpeg is visible. Fix only defects within the approved spec; do not weaken assertions.

- [ ] **Step 3: Write the real acceptance runbook**

`docs/transcribe-acceptance.md` must start with unchecked scenarios and fields for date, MinoruStudio commit, Python/faster-whisper/FFmpeg versions, input path/hash/size/mtime, job directories, model cache path, command outputs, artifact hashes, subtitle timing validation, preview stream validation, cancellation/resume, offline-cache run, log scan, GUI smoke, and final Pass/Fail.

The runbook commands include:

```powershell
uv run minoru-studio transcribe <sample.mp4> -Name acceptance-transcribe -OutputDir <jobs> -Preview
uv run minoru-studio transcribe resume <failed-job> -AllowModelDownload
$env:HF_HUB_OFFLINE = "1"
uv run minoru-studio transcribe <sample.mp4> -Name acceptance-offline -OutputDir <jobs>
Remove-Item Env:HF_HUB_OFFLINE
```

The runbook must explicitly say not to disconnect or alter the whole machine network, not to use private footage, and not to commit model weights, input media, jobs, or transcript text.

- [ ] **Step 4: Add pre-release README instructions**

Document CLI/GUI syntax, local-only behavior, model confirmation, cache location, artifacts, resume, audio-only preview restriction, and FFmpeg/faster-whisper doctor checks under `## 文字起こし（0.3.0受入前）`. Do not claim real-model acceptance or promote the package version yet.

- [ ] **Step 5: Run the complete automated gate**

Run:

```powershell
uv sync --locked --dev
uv run pytest -q
uv run minoru-studio transcribe --help
uv run minoru-studio doctor --json
git diff --check
```

Expected: all tests pass; help exits 0; doctor reports Python, PowerShell, uv, FFmpeg, FFprobe, and faster-whisper OK; diff check reports no errors. Report test counts rather than full logs.

- [ ] **Step 6: Commit Task 9**

```powershell
git add tests/acceptance/test_transcribe_preparation.py docs/transcribe-acceptance.md README.md
git commit -m "docs: add transcription acceptance gate"
```

---

### Task 10: Real Model Acceptance and 0.3.0 Promotion

**Files:**
- Modify: `docs/transcribe-acceptance.md`
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/minoru_studio/__init__.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: an expendable short Japanese video explicitly selected by the user and explicit model-download authorization.
- Produces: completed acceptance evidence and package version `0.3.0` only after every scenario passes.

- [ ] **Step 1: Run real acceptance through the runbook**

Use a 10-30 second non-private Japanese sample. First run without authorization and verify no model download. Then explicitly authorize the `small` download, resume, inspect TXT/SRT/VTT and preview, cancel/resume a separate job, repeat with `HF_HUB_OFFLINE=1`, scan logs, and smoke-test GUI. Record actual versions, paths, hashes, counts, and Pass/Fail in the runbook; never paste transcript content into the runbook or logs.

Expected: all ten real scenarios from the approved spec pass. If model, FFmpeg, or Windows behavior differs, stop and return to the owning task instead of changing the acceptance rule.

- [ ] **Step 2: Promote the version only after recorded Pass**

Change both declarations and the CLI test:

```toml
version = "0.3.0"
```

```python
__version__ = "0.3.0"
```

Run `uv lock` so the root package entry is `0.3.0`. Remove the README's `（0.3.0受入前）` qualifier and present transcribe as a supported workflow.

- [ ] **Step 3: Run final verification**

Run:

```powershell
uv sync --locked --dev
uv run pytest -q
uv run minoru-studio --version
uv run minoru-studio transcribe --help
uv run minoru-studio doctor --json
git diff --check
git status --short
```

Expected: all tests pass; version prints `0.3.0`; help and doctor exit 0; diff check is clean; status lists only the intended release files before commit.

- [ ] **Step 4: Commit the accepted release**

```powershell
git add docs/transcribe-acceptance.md README.md pyproject.toml uv.lock src/minoru_studio/__init__.py tests/test_cli.py
git commit -m "feat: complete local transcription workflow"
```

- [ ] **Step 5: Verify the final commit and clean tree**

Run:

```powershell
git status --short
git log -1 --oneline
```

Expected: status is empty and the latest commit is `feat: complete local transcription workflow`.
