# MinoruStudio Phase 3: Local Transcription Design

- Date: 2026-07-19
- Status: Approved
- Scope: Local audio extraction, transcription, subtitle artifacts, and optional preview video
- Platform: Windows

## 1. Context

MinoruStudio Phase 1 established the launcher, job package, locking, process
execution, logging, environment checks, and integer-millisecond time utilities.
Phase 2 established the first complete mode and the Resolve adapter through the
`beat-sync` workflow.

Phase 3 adds the `transcribe` mode. It converts a local video or audio input into
an editable plain-text transcript and SRT/VTT subtitle files without requiring
DaVinci Resolve. At explicit request, it also creates a separate video with the
subtitles burned in for review.

The mode is local by default and must not send the input, extracted audio, or
recognized text to a cloud service. The only permitted network operation is an
explicitly authorized first-time model download.

## 2. Goals

1. Accept a local video or audio file containing a decodable audio stream.
2. Extract a normalized inference WAV with FFmpeg while preserving the source.
3. Transcribe Japanese by default with `faster-whisper` using CPU INT8 and the
   `small` model.
4. Allow automatic language detection or an explicit supported language code.
5. Produce `transcript.txt`, `subtitles.srt`, and `subtitles.vtt` without
   Resolve.
6. Preserve recognized wording while formatting SRT/VTT into readable,
   non-overlapping cues.
7. Optionally produce a separate subtitle-burned `preview.mp4` for video input.
8. Support the mode from both the non-interactive CLI and launcher GUI.
9. Record durable step state so an interrupted or failed job can resume when
   its input and settings are unchanged.
10. Isolate model inference in a MinoruStudio-owned worker subprocess behind a
    versioned JSON contract.

## 3. Non-goals

This phase does not include:

- applying subtitles to a Resolve timeline;
- narration or VOICEVOX integration;
- speaker diarization or speaker labels;
- translation, summarization, correction, or rewriting of recognized text;
- live microphone transcription or streaming;
- GPU/CUDA setup or automatic hardware selection;
- cloud transcription providers;
- preview generation for audio-only inputs;
- manual subtitle editing inside MinoruStudio;
- deleting or modifying source media.

Resolve subtitle placement remains a later, separately specified phase.

## 4. Chosen Architecture

The chosen design uses the existing MinoruStudio core plus a small worker
subprocess owned by this repository.

```text
CLI / launcher GUI
  -> TranscribeService
       -> FFprobe input inspection
       -> FFmpeg WAV extraction
       -> worker-request.json
       -> MinoruStudio worker subprocess
            -> faster-whisper
            -> raw-segments.json
       -> worker-output validation
       -> TXT / SRT / VTT rendering
       -> optional FFmpeg preview rendering
```

### 4.1 MinoruStudio core

The core owns:

- CLI and GUI inputs;
- job creation, locking, state transitions, input fingerprints, and logs;
- FFprobe validation and FFmpeg command construction;
- worker lifecycle, exit-code handling, cancellation, and output validation;
- subtitle cue formatting and artifact validation;
- optional preview generation;
- model-download policy and user-facing messages.

The core never imports model inference into the GUI process.

### 4.2 Worker subprocess

The worker is a private MinoruStudio entry point, not a public third-party CLI.
It owns only:

- loading the requested `faster-whisper` model;
- running VAD-enabled transcription;
- requesting word timestamps;
- writing the raw recognized segments and metadata;
- returning a deterministic exit status.

It does not create jobs, render subtitles, call FFmpeg, open GUI dialogs, or
choose fallback providers. Keeping the boundary small isolates inference
memory and crashes while avoiding a dependency on an unrelated CLI's flags and
output format.

### 4.3 Dependency boundary

`faster-whisper` is installed and version-locked in MinoruStudio's existing
uv-managed, application-specific Python environment. It is not installed into
Resolve's Python or the system Python. The selected compatible version range is
recorded in `pyproject.toml` and resolved exactly in `uv.lock` during
implementation.

The model weights are separate runtime data and are not downloaded during an
ordinary offline transcription attempt without explicit permission.

## 5. Command and GUI Contract

### 5.1 CLI

The canonical command is:

```powershell
minoru-studio transcribe .\input.mp4 `
  -Name interview `
  -OutputDir .\jobs `
  -Model small `
  -Language ja `
  -AllowModelDownload
```

The command accepts PowerShell-style aliases and conventional GNU-style long
names. Its mode-specific options are:

| Option | Default | Meaning |
|---|---|---|
| positional `input` | required | Local video or audio input |
| `-Name`, `--name` | required | Job name |
| `-OutputDir`, `--output-dir` | required | Parent directory for the job |
| `-Model`, `--model` | `small` | `small` or `medium` |
| `-Language`, `--language` | `ja` | `ja`, `auto`, or a supported language code |
| `-Normalize`, `--normalize` | off | Conservative audio normalization before inference |
| `-Denoise`, `--denoise` | off | Conservative noise reduction before inference |
| `-Preview`, `--preview` | off | Create a burned-subtitle MP4 for video input |
| `-AllowModelDownload`, `--allow-model-download` | off | Permit the selected missing model to be downloaded |

Resume uses:

```powershell
minoru-studio transcribe resume <job-dir> [-AllowModelDownload]
```

Missing required arguments return an error and usage text; a non-interactive
command never opens the GUI or a confirmation dialog. `-AllowModelDownload` is
the explicit download confirmation for that invocation. It does not become a
persisted blanket permission for other models. It may be supplied on `resume`
after an earlier missing-model or interrupted-download failure; this ephemeral
permission is not an output-affecting setting and does not require a new job.

### 5.2 Launcher GUI

The no-argument launcher adds a `transcribe` mode containing:

- video/audio file picker;
- job name and output-directory fields;
- model choice (`small` or `medium`);
- language choice (Japanese, automatic detection, or explicit code);
- normalization and noise-reduction checkboxes;
- preview checkbox;
- preflight summary, progress state, cancel action, and result summary.

The GUI calls the same service as the CLI from a background thread and contains
no transcription, subtitle, or model-cache rules. If a selected model is
missing, the GUI displays its name, estimated download size, and exact cache
location and asks for confirmation before starting the network operation. For
the approved models, preflight uses pinned offline estimates of about 0.5 GB
for `small` and 1.5 GB for `medium`, and requires at least 1 GB and 3 GB of free
cache-volume space respectively. These are labeled estimates rather than
queried over the network.

## 6. Job Layout and State

A successful job has this relevant layout:

```text
<name>.media-job/
  job.json
  outputs/
    transcript.txt
    subtitles.srt
    subtitles.vtt
    preview.mp4              # only when requested
  work/
    inference.wav
    worker-request.json
    raw-segments.json
  logs/
    run.log
```

The source input remains at its original path. `job.json` records its normalized
absolute path, byte size, modification time, and SHA-256 through the existing
`InputRef` contract.

The mode records these ordered steps:

1. `probe-input`
2. `extract-audio`
3. `transcribe`
4. `render-artifacts`
5. `render-preview`, only when requested

The settings saved before execution include the model, language, CPU device,
INT8 compute type, VAD enablement, word-timestamp enablement, audio-filter
choices, and preview choice. Model/provider and FFmpeg/FFprobe versions are
recorded with the job results.

Every successful output is fingerprinted as an `ArtifactRecord`. The worker
request and raw output remain internal work files and are not user-facing
artifacts.

## 7. Input Inspection and Audio Extraction

FFprobe validates the input by content rather than by filename extension. The
input must exist, be a regular file, contain at least one decodable audio
stream, and report a finite positive duration. A video preview additionally
requires at least one decodable video stream.

FFmpeg extracts the selected first audio stream to a 16 kHz, mono, signed
16-bit PCM WAV at `work/inference.wav`. With neither optional filter enabled,
no normalization or denoising is applied.

Normalization and denoising are independent, explicit options. Denoising uses
`afftdn=nr=10:nf=-80:tn=1`. Normalization uses two-pass EBU R128 `loudnorm`
with targets `I=-16:LRA=11:TP=-1.5`; its measured first-pass values are passed
to the second pass with `linear=true`. When both are selected, `afftdn` precedes
`loudnorm` in both passes. The filter arguments are constants owned by the
audio-extraction module, covered by command-construction tests, and recorded in
job settings. They must not silently activate when the corresponding option is
absent. The final PCM samples are checked for clipping before inference.

An empty, corrupt, or zero-duration extracted WAV fails `extract-audio` even if
FFmpeg exits with status zero.

## 8. Worker JSON Contract

The worker boundary is versioned independently from `job.json`.

A representative request is:

```json
{
  "schema_version": 1,
  "input_wav": "C:\\jobs\\demo.media-job\\work\\inference.wav",
  "output_json": "C:\\jobs\\demo.media-job\\work\\raw-segments.json",
  "model": "small",
  "language": "ja",
  "device": "cpu",
  "compute_type": "int8",
  "vad_filter": true,
  "word_timestamps": true,
  "model_cache_dir": "C:\\Users\\user\\AppData\\Local\\MinoruStudio\\models\\faster-whisper",
  "allow_model_download": false
}
```

The output contains:

- schema version;
- selected model and provider version;
- detected language and probability;
- input duration and duration after VAD in integer milliseconds;
- ordered segments with integer `start_ms`, `end_ms`, text, and ordered words;
- each word's integer start/end and recognized text;
- a no-speech result flag.

The core rejects an output whose schema is unknown, whose path escapes the
selected job, whose timestamps are negative/non-monotonic/outside the input,
or whose claimed model/settings conflict with the request. Floating-point
seconds from `faster-whisper` are converted to deterministic nearest integer
milliseconds inside the worker before persistence.

## 9. Model and Network Policy

Models are stored under:

```text
%LOCALAPPDATA%\MinoruStudio\models\faster-whisper\
```

The rules are:

1. An already cached model is loaded in local-only mode.
2. A missing model with no current authorization stops before network access.
3. CLI authorization is the explicit `-AllowModelDownload` flag.
4. GUI authorization is a confirmation dialog for the named model and cache
   location.
5. Authorization permits only the selected model for the current invocation.
6. A failed download fails the job; no other model or provider is selected.
7. A completed cached model can be used while offline.

The preflight check and the worker both enforce this boundary. The worker is
passed an explicit boolean and uses the provider's local-files-only behavior
when it is false. The application never treats a partially downloaded model as
available.

## 10. Transcript and Subtitle Rules

### 10.1 Transcript

`transcript.txt` contains recognized segment text in chronological order. The
renderer removes provider-introduced leading/trailing whitespace from each
segment and joins non-empty segments with line breaks. It does not translate,
summarize, correct, censor, or otherwise rewrite recognized words.

### 10.2 Subtitle cues

SRT and VTT use the same cue list. The formatter:

1. prefers word timestamps when they are present and valid;
2. splits at Japanese or Western sentence punctuation before a hard length
   boundary;
3. permits at most 21 Unicode code points per line and two lines per cue;
4. hard-splits an unbroken token only when no punctuation/whitespace boundary
   fits within the limit;
5. preserves recognized character order and wording;
6. stores all internal cue boundaries as non-negative integer milliseconds;
7. produces monotonically increasing, non-overlapping cue intervals within the
   source duration;
8. merges a zero-length fragment produced by millisecond rounding into an
   adjacent cue when that can be done within the 42-character cue limit,
   otherwise expands it only
   into available neighboring silence;
9. fails artifact rendering if a positive, non-overlapping interval cannot be
   assigned without changing character order.

When a valid segment has no usable word timestamps, the formatter first splits
its text by the same punctuation/length rules and divides the segment's integer
millisecond interval among the resulting cues in proportion to their non-space
character counts. Remainder milliseconds are assigned in cue order so the last
cue ends exactly at the original segment end.

SRT timestamps use `HH:MM:SS,mmm`; VTT timestamps use `HH:MM:SS.mmm`. SRT cue
numbers are consecutive starting at one. VTT begins with the required
`WEBVTT` header.

When valid inference completes with no speech, the job creates empty
`transcript.txt`, a structurally valid empty SRT, and a structurally valid empty
VTT. It succeeds with a no-speech warning rather than inventing text or
reporting a provider failure.

## 11. Preview Rendering

Preview generation is opt-in and runs only after TXT/SRT/VTT validation.
FFmpeg reads the original video and `subtitles.srt`, burns the captions into a
new `outputs/preview.mp4`, and includes the original audio. It never edits the
source in place.

Preflight resolves a Japanese Windows font in the fixed order `Yu Gothic`,
`Meiryo`, then `MS Gothic` from `%WINDIR%\Fonts`, records the selected family,
and fails before inference if none is installed. Preview rendering passes that
font and font directory explicitly to FFmpeg instead of depending on a default
font. FFprobe then requires the preview to contain at least one video stream,
at least one audio stream, and a finite positive duration within 250
milliseconds of the source duration.

Requesting `-Preview` for an audio-only input fails preflight with a clear
message before inference starts. Preview rendering failure marks only the
`render-preview` step failed and retains the already generated transcript and
subtitle artifacts for inspection and resume.

## 12. Failure, Cancellation, and Resume

Every external process must have a nonzero-exit failure path and an independent
artifact-validation failure path. A zero exit code never overrides a missing,
empty where forbidden, malformed, or inconsistent result.

The service records concise errors in `job.json` and diagnostics in
`logs/run.log`. Logs may contain paths, settings, versions, exit codes, and
counts, but must not contain recognized segment, word, subtitle, or transcript
text.

On cancellation, MinoruStudio terminates the active FFmpeg or worker process,
marks the running step and job `interrupted`, and preserves the source and all
completed work/artifacts. It does not report the job as failed or succeeded.

Resume is allowed only for a `pending`, `interrupted`, or failed job whose input
fingerprint and persisted settings are unchanged. Completed steps are reused
only after their expected work files or artifact fingerprints pass validation.
The first incomplete or invalid step and all following steps run again.

If the input fingerprint or any output-affecting setting differs, the service
does not mutate the old job and instructs the user to create a new uniquely
named job. Existing job directories follow the current `-002` suffix policy and
are never silently replaced.

## 13. Privacy and Safety

- Source video/audio and recognized text remain local.
- No cloud transcription or silent provider fallback exists.
- Network access is limited to an explicitly authorized model download.
- Source media is opened read-only and never overwritten or deleted.
- Preview and work files are written only inside the newly selected job.
- Artifact and work paths are resolved and checked to remain inside the job.
- Model-cache paths are resolved and checked to remain inside the configured
  MinoruStudio model root.
- Logs and errors redact recognized content and any secret-like command values.
- A job succeeds only when every required step and artifact validation passes.

## 14. Verification Strategy

### 14.1 Automated tests

Ordinary automated tests use a fake worker and never download a model. They
cover:

- CLI parsing, required arguments, aliases, and defaults;
- launcher-GUI field mapping and background execution;
- Japanese, whitespace-containing, and long Windows paths;
- audio-stream and preview video-stream preflight;
- exact FFmpeg extraction and optional-filter command construction;
- worker request/output schema round trips and malformed-output rejection;
- deterministic seconds-to-millisecond conversion at the worker boundary;
- transcript preservation;
- punctuation-first cue splitting, 21-character lines, and two-line limit;
- monotonic, positive, non-overlapping SRT/VTT timestamps;
- empty/no-speech artifacts;
- FFmpeg and worker nonzero exits and zero-exit/missing-output failures;
- model-missing behavior without network authorization;
- model authorization not persisting across models or invocations;
- cancellation and interrupted-state resume;
- input/settings mismatch rejection;
- no source or existing-job modification;
- transcript text absence from logs;
- artifact and model-cache path containment;
- preview audio/video stream and duration validation;
- all existing foundation and beat-sync behavior.

A local integration fixture uses synthetic audio plus a fake worker to exercise
real FFmpeg extraction, subtitle burning, and FFprobe inspection without
requiring model weights.

Mechanical gates include:

```powershell
uv sync --locked --dev
uv run pytest -q
uv run minoru-studio transcribe --help
uv run minoru-studio doctor --json
```

`doctor` reports the importability and version of the pinned `faster-whisper`
dependency. A missing model is reported as runtime data, not as a failed base
installation, because model selection and download authorization belong to the
individual transcribe job.

### 14.2 Real local acceptance

A separate, explicitly initiated acceptance run uses an expendable short
Japanese video and the real `small` model. It verifies:

1. the first run without authorization performs no download and explains the
   required action;
2. an authorized first run shows the model/cache details and completes the
   model download;
3. TXT, SRT, and VTT are created and the principal spoken content is
   recognizable to a human reviewer;
4. subtitle times are within the media duration, positive, ordered, and
   non-overlapping;
5. the optional preview contains visible Japanese subtitles, video, and the
   original audio;
6. input file size, modification time, and SHA-256 remain unchanged;
7. cancellation leaves an interrupted job that can resume;
8. a repeated create operation uses a new suffixed directory;
9. an already cached model succeeds with network access unavailable;
10. neither the run log nor job error fields contain transcript text.

No exact word-error-rate gate is imposed on arbitrary real recordings because
recording quality affects model accuracy. Mechanical artifact checks are
mandatory; the short sample's transcription is accepted separately by a human.

## 15. Residual Risks

- `faster-whisper` accuracy and CPU runtime depend on recording quality and
  hardware.
- Model download layout and local-only behavior may vary across compatible
  provider versions and require contract tests around the pinned version.
- Word-level timestamps can contain zero-length or overlapping values that the
  formatter must repair conservatively or reject.
- Japanese subtitle readability cannot be fully measured by character counts;
  the initial 21-character rule may need later user-configurable refinement.
- Subtitle burning depends on FFmpeg's subtitle filter and available font
  support on the installed Windows system.
- Forcefully terminating native inference may leave temporary or partial model
  files; incomplete cache entries must never be treated as complete.
- Real model acceptance requires an explicit download and can be slower than
  the automated suite.

## 16. Acceptance Criteria

Phase 3 is complete when:

1. the approved CLI and GUI create and run a `transcribe` job;
2. default Japanese CPU INT8 `small` transcription works through the private
   worker subprocess;
3. TXT, SRT, and VTT are valid, local, editable artifacts;
4. subtitles preserve recognized wording and satisfy the approved line and
   timestamp rules;
5. optional preview generation works for video input and rejects audio-only
   input before inference;
6. missing models never trigger unauthorized network access;
7. cancellation and unchanged-job resume work without overwriting data;
8. source media and existing jobs remain unchanged;
9. automated gates and the real local acceptance scenarios pass;
10. Phase 1/2 tests remain green and Resolve is not required for transcription.

Only after all automated gates and real local acceptance pass are the package
version promoted from `0.2.0` to `0.3.0` and README instructions changed to
present `transcribe` as an available workflow.
