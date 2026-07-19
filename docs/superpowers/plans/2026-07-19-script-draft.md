# Script Draft Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the local-only `script-draft` mode, which extracts representative
video frames and creates an editable Markdown script template without altering
source media or invoking AI, network, or Resolve.

**Architecture:** Add an isolated `minoru_studio.script_draft` package beside
`transcribe`. Its media layer owns FFprobe/FFmpeg candidate extraction, its
artifact layer owns deterministic merging and local rendering, and its service
owns the existing job-store lifecycle. Thin CLI and Tk launcher adapters create
the same request and never contain media-processing rules.

**Tech Stack:** Python 3.12, existing `JobStore`, FFmpeg/FFprobe, Tkinter,
pytest; no new runtime dependency.

## Global Constraints

- Windows only; do not introduce a non-Windows target.
- Persist all media time values as integer milliseconds.
- Use fixed settings: scene threshold `0.30`, interval `5000 ms`, merge
  tolerance `100 ms`, PNG frames, maximum long edge `1280 px`, no upscaling.
- The initial CLI and GUI expose no sampling or image-size control.
- Do not send images or metadata externally, call an image model, call Resolve,
  create narration, or modify/delete source media or an existing job.
- Every output and work path must resolve inside the newly created job.
- Resume only unchanged input fingerprints and identical persisted settings;
  use the existing suffixed job-directory policy for a new run.
- Preserve the current `beat-sync`, `transcribe`, and Resolve-adapter behavior.
- Per user direction, write and run the focused and full test suites together
  in Task 7 after the implementation tasks. Do not claim real-media acceptance
  or release the mode in README before the later consolidated acceptance phase.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/minoru_studio/jobs/model.py` | Register deterministic manifest step ordering for `script-draft`. |
| `src/minoru_studio/script_draft/models.py` | Immutable request, video metadata, candidate, and index-entry contracts. |
| `src/minoru_studio/script_draft/media.py` | FFprobe validation, FFmpeg candidate commands, `showinfo` parsing, PNG validation. |
| `src/minoru_studio/script_draft/artifacts.py` | Candidate merge, atomic image/index/template rendering, artifact validation. |
| `src/minoru_studio/script_draft/service.py` | Job create/resume, step lifecycle, cancellation, reconciliation, and log-safe failures. |
| `src/minoru_studio/script_draft/commands.py` | Non-interactive create/resume CLI adapter. |
| `src/minoru_studio/script_draft/gui_state.py` | GUI form validation and request conversion. |
| `src/minoru_studio/script_draft/settings.py` | Persist only this mode's GUI field values. |
| `src/minoru_studio/cli.py` | Register the fixed `script-draft` parser. |
| `src/minoru_studio/gui.py` | Wire the new mode into the launcher and preserve its settings. |
| `src/minoru_studio/beat_sync/settings.py` / `src/minoru_studio/transcribe/settings.py` | Preserve the new `script_draft` config section when other modes save. |
| `tests/script_draft/` | Deferred focused contract coverage for all new package boundaries. |
| `tests/jobs/test_model.py`, `tests/test_cli.py`, `tests/test_gui_controller.py` | Regression coverage for mode registration and launcher integration. |

## Interfaces shared by later tasks

```python
# src/minoru_studio/script_draft/models.py
@dataclass(frozen=True, slots=True)
class ScriptDraftRequest:
    input_path: Path
    name: str
    output_dir: Path

@dataclass(frozen=True, slots=True)
class VideoInfo:
    duration_ms: int
    width: int
    height: int

@dataclass(frozen=True, slots=True)
class FrameCandidate:
    time_ms: int
    source_path: Path
    reason: Literal["scene", "interval"]

@dataclass(frozen=True, slots=True)
class FrameIndexEntry:
    index: int
    time_ms: int
    image_path: str
    reasons: tuple[Literal["scene", "interval"], ...]
```

```python
# src/minoru_studio/script_draft/media.py
def probe_video(path: Path, *, runner: Runner = run_process) -> VideoInfo: ...
def extract_scene_candidates(source: Path, work_dir: Path, *, runner: Runner,
                             cancel_event: object | None) -> list[FrameCandidate]: ...
def extract_interval_candidates(source: Path, work_dir: Path, *, runner: Runner,
                                cancel_event: object | None) -> list[FrameCandidate]: ...

# src/minoru_studio/script_draft/artifacts.py
def merge_candidates(candidates: Sequence[FrameCandidate]) -> list[tuple[FrameCandidate, tuple[str, ...]]]: ...
def render_artifacts(outputs_dir: Path, info: VideoInfo,
                     merged: Sequence[tuple[FrameCandidate, tuple[str, ...]]]) -> list[FrameIndexEntry]: ...
def artifacts_valid(job_dir: Path) -> bool: ...

# src/minoru_studio/script_draft/service.py
class ScriptDraftService:
    def create_and_run(self, request: ScriptDraftRequest, *, cancel_event: object | None = None,
                       progress: Callable[[str], object] | None = None) -> Path: ...
    def resume(self, job_dir: Path, *, cancel_event: object | None = None,
               progress: Callable[[str], object] | None = None) -> Path: ...
```

### Task 1: Register the mode contracts and manifest ordering

**Files:**
- Modify: `src/minoru_studio/jobs/model.py`
- Create: `src/minoru_studio/script_draft/__init__.py`
- Create: `src/minoru_studio/script_draft/models.py`
- Modify: `tests/jobs/test_model.py`
- Create: `tests/script_draft/__init__.py`
- Create: `tests/script_draft/test_models.py`

**Consumes:** Existing `JobMode.SCRIPT_DRAFT`, `seconds_to_milliseconds`, and
the `JobManifest` serialization contract.

**Produces:** The data contracts in the shared-interface section and manifest
ordering for `probe-input`, `extract-scene-frames`, `extract-interval-frames`,
and `render-draft`.

- [ ] **Step 1: Add the new step-order registry before changing deserialization.**

  Replace the transcribe-only ordering helper with a mode-keyed registry:

  ```python
  _STEP_ORDERS = {
      JobMode.TRANSCRIBE: (
          "probe-input", "extract-audio", "transcribe",
          "render-artifacts", "render-preview",
      ),
      JobMode.SCRIPT_DRAFT: (
          "probe-input", "extract-scene-frames",
          "extract-interval-frames", "render-draft",
      ),
  }

  def _ordered_steps(
      steps: Mapping[str, Any], mode: JobMode,
  ) -> list[tuple[str, Any]]:
      known = [name for name in _STEP_ORDERS.get(mode, ()) if name in steps]
      remaining = [name for name in steps if name not in known]
      return [(name, steps[name]) for name in (*known, *remaining)]
  ```

  Parse `mode = JobMode(data["mode"])` once in `manifest_from_dict`, pass it
  to `_ordered_steps`, and use that same value in `JobManifest`.

- [ ] **Step 2: Add the immutable script-draft model definitions.**

  In `models.py`, use `Literal` for the two legal reasons and reject invalid
  values in each `__post_init__`. Require native `int` (not `bool`) and
  non-negative `time_ms`; require positive `VideoInfo` dimensions/duration;
  require an existing relative PNG path only at artifact validation, not in the
  pure contracts. Define the shared constants:

  ```python
  SCENE_THRESHOLD = 0.30
  INTERVAL_MS = 5_000
  MERGE_TOLERANCE_MS = 100
  MAX_FRAME_EDGE = 1_280
  FRAME_FORMAT = "png"
  ```

- [ ] **Step 3: Record the task-local implementation commit.**

  ```powershell
  git add src/minoru_studio/jobs/model.py src/minoru_studio/script_draft tests/jobs/test_model.py tests/script_draft
  git commit -m "feat: add script draft contracts"
  ```

### Task 2: Implement isolated video probing and candidate extraction

**Files:**
- Create: `src/minoru_studio/script_draft/media.py`
- Create: `tests/script_draft/test_media.py`

**Consumes:** `VideoInfo`, `FrameCandidate`, fixed constants, existing
`ProcessResult`, `run_process`, `run_cancellable_process`, and
`seconds_to_milliseconds`.

**Produces:** Content-validated video metadata and two candidate lists whose
source files reside below the supplied work directory.

- [ ] **Step 1: Implement video-only FFprobe parsing.**

  Query the first video stream's width and height together with format duration:

  ```python
  ["ffprobe", "-v", "error", "-select_streams", "v:0",
   "-show_entries", "format=duration:stream=width,height,codec_type",
   "-of", "json", str(path)]
  ```

  Require a zero exit code, a finite positive duration after
  `seconds_to_milliseconds`, one `video` stream, and positive integer width and
  height. Raise `ValueError("input has no valid video stream")` for every
  malformed/no-video case; do not inspect or require audio.

- [ ] **Step 2: Implement the two FFmpeg commands with temporary work output.**

  Both commands use `-nostdin -v info -y -i <source> -an`, `showinfo`, and the
  filter suffix:

  ```text
  scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease
  ```

  The scene filter prefix is `select='gt(scene,0.30)',showinfo,`; the interval
  prefix is `fps=1/5,showinfo,`. Write each pass below
  `work/scene-frames/` or `work/interval-frames/` with sequential temporary
  names. Parse `showinfo` `pts_time` values in output order, convert them to
  integer milliseconds, and pair them with the corresponding generated PNG.
  Reject a nonzero process result, a count mismatch, non-finite timestamp, a
  missing/non-PNG file, or a frame exceeding 1280px. Read PNG dimensions from
  its signature/IHDR bytes; do not add Pillow.

- [ ] **Step 3: Preserve only safe, cancellable process behavior.**

  Use `run_cancellable_process` by default and pass the service cancellation
  event through unchanged. Create each work directory before invoking FFmpeg.
  Confirm a resolved candidate file is below the resolved work directory before
  returning it. Never put source-video content or FFmpeg stderr into an error.

- [ ] **Step 4: Record the task-local implementation commit.**

  ```powershell
  git add src/minoru_studio/script_draft/media.py tests/script_draft/test_media.py
  git commit -m "feat: extract script draft frame candidates"
  ```

### Task 3: Render deterministic frame, index, and Markdown artifacts

**Files:**
- Create: `src/minoru_studio/script_draft/artifacts.py`
- Create: `tests/script_draft/test_artifacts.py`

**Consumes:** `FrameCandidate`, `FrameIndexEntry`, `VideoInfo`, and fixed
constants from Task 1.

**Produces:** Valid `outputs/frames/*.png`, `outputs/frame-index.json`, and
`outputs/script.md`, plus artifact validation reusable by resume.

- [ ] **Step 1: Implement deterministic merge and naming.**

  Sort candidates by `(time_ms, reason)` where `scene` sorts before `interval`.
  Merge adjacent candidates no more than `100 ms` apart. Preserve the scene
  candidate image when either candidate has reason `scene`; union reasons in
  the fixed order `("scene", "interval")`. Assign chronological one-based
  indices and destination names using:

  ```python
  destination = outputs_dir / "frames" / f"frame-{index:04d}.png"
  ```

  Copy each validated work PNG to a unique temporary sibling, then atomically
  replace the new destination. Because the job is new, reject an existing final
  destination instead of replacing it.

- [ ] **Step 2: Render the index and script from the same entries.**

  Atomically write UTF-8 JSON with `schema_version`, `duration_ms`, `width`,
  `height`, fixed settings, and entries shaped as:

  ```json
  {"index": 1, "time_ms": 0, "image_path": "frames/frame-0001.png", "reasons": ["interval"]}
  ```

  Render `script.md` with `# Script Draft`, a source metadata table, and one
  repeatable section:

  ```markdown
  ## 00:00:00.000 — Frame 0001

  ![Frame 0001](frames/frame-0001.png)

  ### 画面の説明

  ### 操作

  ### ナレーション
  ```

  Format time from the stored integer milliseconds. Do not add image
  descriptions, captions, or inferred narration.

- [ ] **Step 3: Implement complete artifact validation.**

  `artifacts_valid(job_dir)` must load JSON, revalidate schema/types/order and
  PNG dimensions, ensure every relative image path resolves below
  `outputs/frames`, ensure `script.md` contains each expected relative link,
  and compare all expected `ArtifactRecord` fingerprints. It returns `False`
  rather than throwing for malformed/missing persisted output.

- [ ] **Step 4: Record the task-local implementation commit.**

  ```powershell
  git add src/minoru_studio/script_draft/artifacts.py tests/script_draft/test_artifacts.py
  git commit -m "feat: render script draft artifacts"
  ```

### Task 4: Orchestrate the resumable job service

**Files:**
- Create: `src/minoru_studio/script_draft/service.py`
- Create: `tests/script_draft/test_service.py`

**Consumes:** Tasks 1–3 and existing `JobStore`, locks, fingerprints, logging,
and cancellable process types.

**Produces:** `ScriptDraftService`, content-free `ScriptDraftFailed` and
`ScriptDraftInterrupted` errors, and a durable four-step job lifecycle.

- [ ] **Step 1: Create and claim jobs through the existing store.**

  Implement `create_and_run` with this persisted settings mapping:

  ```python
  {
      "scene_threshold": 0.30,
      "interval_ms": 5_000,
      "merge_tolerance_ms": 100,
      "max_frame_edge": 1_280,
      "frame_format": "png",
  }
  ```

  Use `JobMode.SCRIPT_DRAFT`, one input path, and the existing `JobStore.create`
  fingerprint. `resume` loads only the same mode in `pending`, `failed`, or
  `interrupted`; it reconstructs the request from the manifest and rejects a
  changed fingerprint or settings before any process call.

- [ ] **Step 2: Execute and reconcile the four named steps.**

  `probe-input` writes `work/video-info.json` and records FFmpeg/FFprobe
  provenance. `extract-scene-frames` and `extract-interval-frames` write only
  their own work directories. `render-draft` merges both lists, writes final
  artifacts, and creates artifact fingerprints for every PNG, JSON, and
  Markdown file. Use a validator map equivalent to:

  ```python
  validators = {
      "probe-input": lambda: load_video_info(job_dir, context),
      "extract-scene-frames": lambda: load_candidates(job_dir, "scene"),
      "extract-interval-frames": lambda: load_candidates(job_dir, "interval"),
      "render-draft": lambda: artifacts_valid(job_dir),
  }
  ```

  On the first invalid/incomplete step, reset it and later steps to pending;
  retain earlier validated work. A job succeeds only after final artifact
  validation succeeds.

- [ ] **Step 3: Make failure and interruption durable.**

  Reuse the transcribe service's step state transitions and logger pattern.
  Map probe failures to `input validation`, both extraction steps to `FFmpeg`,
  and render failures to `draft rendering`. On `ProcessCancelledError` or
  `KeyboardInterrupt`, mark the running step/job interrupted and raise
  `ScriptDraftInterrupted(job_dir)`. All user-facing errors contain only the
  stable category and never FFmpeg stderr or frame data.

- [ ] **Step 4: Record the task-local implementation commit.**

  ```powershell
  git add src/minoru_studio/script_draft/service.py tests/script_draft/test_service.py
  git commit -m "feat: run resumable script draft jobs"
  ```

### Task 5: Add non-interactive CLI and persistent GUI state

**Files:**
- Create: `src/minoru_studio/script_draft/commands.py`
- Create: `src/minoru_studio/script_draft/gui_state.py`
- Create: `src/minoru_studio/script_draft/settings.py`
- Modify: `src/minoru_studio/cli.py`
- Modify: `src/minoru_studio/beat_sync/settings.py`
- Modify: `src/minoru_studio/transcribe/settings.py`
- Create: `tests/script_draft/test_commands.py`
- Create: `tests/script_draft/test_gui_state.py`
- Create: `tests/script_draft/test_settings.py`
- Modify: `tests/beat_sync/test_settings.py`
- Modify: `tests/transcribe/test_settings.py`

**Consumes:** `ScriptDraftRequest`, `ScriptDraftService`, and the current
`transcribe` parser/form/settings patterns.

**Produces:** The exact create/resume CLI contract and a saved GUI form that
does not erase another mode's settings section.

- [ ] **Step 1: Implement the parser and command adapter.**

  Register:

  ```python
  script_draft_parser = subparsers.add_parser(
      "script-draft", help="extract local video frames and create a script template",
  )
  script_draft_parser.add_argument("input_or_action")
  script_draft_parser.add_argument("resume_job", nargs="?")
  script_draft_parser.add_argument("-Name", "--name", dest="name")
  script_draft_parser.add_argument("-OutputDir", "--output-dir", dest="output_dir")
  ```

  Reject missing create `-Name`/`-OutputDir`, a second create positional path,
  and all create options on `resume`. `create_and_run` prints the resolved job
  path and returns `0`; `ScriptDraftFailed` prints its stable category to stderr
  and returns `1`; `ScriptDraftInterrupted` prints the resolved job path to
  stderr and returns `130`.

- [ ] **Step 2: Implement form state and settings preservation.**

  `ScriptDraftFormValues(input_path, name, output_dir).to_request()` strips and
  requires all strings, then creates `ScriptDraftRequest` from `Path` values.
  `script_draft/settings.py` permits only `input`, `name`, and `output_dir`.
  When any of the three settings modules saves, read the existing JSON object,
  replace only its own `beat_sync`, `transcribe`, or `script_draft` section,
  and atomically write that complete object. Preserve unknown top-level
  sections; retain the beat-sync reader's legacy flat-file fallback.

- [ ] **Step 3: Record the task-local implementation commit.**

  ```powershell
  git add src/minoru_studio/script_draft src/minoru_studio/cli.py src/minoru_studio/beat_sync/settings.py src/minoru_studio/transcribe/settings.py tests/script_draft tests/beat_sync/test_settings.py tests/transcribe/test_settings.py
  git commit -m "feat: add script draft command and form state"
  ```

### Task 6: Integrate the mode into the Tk launcher

**Files:**
- Modify: `src/minoru_studio/gui.py`
- Modify: `tests/test_gui_controller.py`

**Consumes:** Task 5 form/settings contracts and Task 4 service errors.

**Produces:** A selectable `script-draft` launcher panel with source picker,
background execution, cancellation, and resumable-job handling.

- [ ] **Step 1: Extend `LauncherController` without changing other mode methods.**

  Add an optional `script_draft_service: ScriptDraftService | None` constructor
  dependency, plus `prepare_script_draft(...)` and `resume_script_draft(...)`.
  Both methods construct/use `ScriptDraftFormValues` and forward the existing
  `cancel_event` and `progress` arguments unchanged.

- [ ] **Step 2: Add the fixed-mode GUI panel.**

  Add `JobMode.SCRIPT_DRAFT` to the mode chooser and `mode_defaults`. Create a
  label frame titled `台本下書き` with a video-only file picker, a `台本下書きを開始`
  button, and the shared cancel button. Use file types for common video formats
  plus `All files`; do not offer audio, threshold, interval, AI, or Resolve
  controls. Save the three settings before starting work.

- [ ] **Step 3: Match the transcribe interaction guarantees.**

  Run the service on a daemon thread, disable mode/input/name/output controls
  while active, publish the current step through `root.after`, and restore
  controls on every completion path. For failed/interrupted opened jobs, change
  the button to `台本下書きを再開`; for a succeeded job disable it as
  `完了済みジョブ（確認のみ）`. Show stable failure categories in a dialog and
  never display process stderr.

- [ ] **Step 4: Record the task-local implementation commit.**

  ```powershell
  git add src/minoru_studio/gui.py tests/test_gui_controller.py
  git commit -m "feat: add script draft launcher workflow"
  ```

### Task 7: Perform the deferred contract test batch and document the unaccepted state

**Files:**
- Complete: `tests/script_draft/test_models.py`
- Complete: `tests/script_draft/test_media.py`
- Complete: `tests/script_draft/test_artifacts.py`
- Complete: `tests/script_draft/test_service.py`
- Complete: `tests/script_draft/test_commands.py`
- Complete: `tests/script_draft/test_gui_state.py`
- Modify: `tests/jobs/test_model.py`, `tests/test_cli.py`, `tests/test_gui_controller.py`
- Modify: `docs/agent-handoff.md`

**Consumes:** Every production interface introduced in Tasks 1–6.

**Produces:** Focused contract evidence and a handoff record that marks the
mode as implemented-but-awaiting consolidated acceptance.

- [ ] **Step 1: Add deterministic unit contracts.**

  Cover fixed settings, `VideoInfo` rejection, candidate merge precedence and
100ms boundary, no-upscale PNG validation, time formatting, index ordering,
relative Markdown links, parser error shapes, form validation, and preservation
of all three settings sections. Mock process runners; no test may invoke a
network service, Resolve, or an image model.

- [ ] **Step 2: Add service lifecycle contracts with local fakes.**

  Use fake probe/extractor/render collaborators to assert the four step names,
artifact fingerprints, audio-less video acceptance, no-video preflight failure,
nonzero/missing-frame failure, cancellation, first-invalid-step resume,
  input-change rejection, and that source bytes/mtime remain unchanged. Include
  one synthetic local FFmpeg fixture that is explicitly skipped when the
  existing doctor check reports FFmpeg unavailable; do not replace it with a
  network or cloud-media fixture.

- [ ] **Step 3: Run focused then full mechanical gates.**

  ```powershell
  uv run pytest tests/script_draft tests/jobs/test_model.py tests/test_cli.py tests/test_gui_controller.py -q
  uv run pytest -q
  uv run minoru-studio script-draft --help
  uv run minoru-studio doctor --json
  ```

  Expected outcome: every selected test passes; help shows create/resume and
  only `-Name`/`-OutputDir`; doctor remains healthy. Stop for user direction if
  an unexpected dependency, environment, or unrelated regression appears.

- [ ] **Step 4: Update the handoff state without declaring release acceptance.**

  Change the `script-draft` row in `docs/agent-handoff.md` to
  `実装・機械確認済み。実動画のまとめて受入待ち` only after Step 3 succeeds.
  Do not add normal end-user README promotion or a real-acceptance claim.

- [ ] **Step 5: Record the task-local implementation commit.**

  ```powershell
  git add tests docs/agent-handoff.md
  git commit -m "test: verify script draft contracts"
  ```

## Final review checklist for the implementing agent

- [ ] `script-draft` never accepts an audio-only source and never needs an
  audio stream for a valid video.
- [ ] Every persisted source time is an integer millisecond; no FFmpeg float
  time is saved directly.
- [ ] Output image paths and all artifacts are contained under the new job.
- [ ] The scene/interval merge preserves both reasons and prefers the scene
  image without duplicate output frames.
- [ ] `script.md` is a blank human template, not an inferred script.
- [ ] Existing mode settings survive saving this mode and vice versa.
- [ ] No network, AI, Resolve, source overwrite, or release/version bump has
  been added.
