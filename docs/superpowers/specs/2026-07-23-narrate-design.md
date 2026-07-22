# MinoruStudio Phase 5: Local Narration Design

- Date: 2026-07-23
- Status: Approved for implementation planning
- Scope: Local VOICEVOX narration, subtitles, and optional review preview
- Platform: Windows
- Governing design: [`../../media-automation-design.md`](../../media-automation-design.md),
  especially sections 7.3, 10.3, 11.2, 13, 15, and 17

## 1. Context

`script-draft` now creates local representative frames and a human-editable
`script.md`. This phase adds `narrate`: it turns human-approved narration text
into local speech audio and subtitle artifacts. It does not apply them to
DaVinci Resolve.

VOICEVOX CPU 0.25.2 is installed on the current Windows machine. Its local API
is available at `http://127.0.0.1:50021`, and its speaker list currently
contains `ずんだもん` / `ノーマル` (speaker ID 2). The implementation must not
hard-code ID 2: it resolves the configured speaker name and style name from the
live local API before synthesis and records the resulting ID in the job.

The global job, privacy, artifact, no-overwrite, and Resolve deferral rules
remain defined by the governing design and existing modes. This document
defines only the `narrate` additions.

## 2. Goals and Non-goals

### Goals

1. Accept one local video and either a plain `script.txt` or a `script.md`
   created by `script-draft`.
2. Generate utterance WAVs through the locally running VOICEVOX Engine using
   `ずんだもん` / `ノーマル` at speech speed `1.0`.
3. Insert exactly `300 ms` of silence between non-empty utterances, concatenate
   the resulting audio, and derive SRT/VTT from the actual measured durations.
4. Save editable WAV, SRT, and VTT artifacts in a resumable job package.
5. Optionally render a review MP4 using the original video image, generated
   narration audio, and burned subtitles.
6. Warn, without altering speech or source media, when narration exceeds the
   source video duration.
7. Support CLI create/resume and the existing launcher GUI.

### Non-goals

This phase does not include:

- cloud TTS, OpenAI APIs, or any network request other than loopback HTTP to a
  locally running VOICEVOX Engine;
- automatic installation or automatic startup of VOICEVOX;
- multiple speakers in a single job, style selection controls, or configurable
  speech speed/silence/segmentation values;
- automatic rewriting, shortening, translation, pronunciation editing, or
  speed adjustment of the human script;
- preserving or mixing original video audio into narration preview;
- Resolve timeline application, final rendering, or source-media modification.

## 3. Input Contract

The create command is:

```powershell
minoru-studio narrate .\input.mp4 -Script .\script.md -Name product-demo -OutputDir .\jobs
```

The optional preview flag is `-Preview`. Resume is:

```powershell
minoru-studio narrate resume .\jobs\product-demo.media-job
```

Create requires one local regular video file, `-Script`, `-Name`, and
`-OutputDir`. Resume accepts only the job directory. A non-interactive command
never opens a dialog; it returns `0` on success, `130` on interruption, and
`1` with a concise stable category on failure.

The input video must have a decodable video stream, a finite positive duration,
and positive dimensions. It need not have an audio stream because the preview
uses generated narration audio. The source remains read-only.

### 3.1 Script formats

`script.txt` is UTF-8 text. Trim each line, discard blank lines, and join
remaining lines with a single newline before sentence segmentation.

`script.md` is a `script-draft` template. Read only content below a heading
whose normalized title is `ナレーション`, stopping at the next heading of the
same or higher level. Discard blank lines and Markdown image/link-only lines;
do not read `画面の説明` or `操作` fields. Process narration sections in document
order. A Markdown file with no narration content is invalid.

Both inputs produce a single ordered text stream. Split it at Japanese sentence
punctuation (`。`, `！`, `？`) or Western sentence punctuation (`.`, `!`, `?`),
then at whitespace when needed. No utterance exceeds 60 Unicode code points.
An unbroken token longer than 60 code points is split at the 60-code-point
boundary without changing its character order. Empty results fail preflight.

## 4. VOICEVOX Contract

MinoruStudio calls only the local base URL `http://127.0.0.1:50021`. It first
requests `/version` and `/speakers`, resolves the exact names `ずんだもん` and
`ノーマル`, and records Engine version, speaker name, style name, and resolved
speaker ID. Failure to reach the Engine, absence of either name, or an
unsupported response fails before any output is generated with a stable
`VOICEVOX unavailable` category and an instruction to start the Engine.

For each utterance, the service:

1. calls `POST /audio_query?text=<UTF-8 text>&speaker=<resolved-id>`;
2. sets only `speedScale` to `1.0` in the returned query JSON;
3. calls `POST /synthesis?speaker=<resolved-id>` with that JSON;
4. validates the returned non-empty WAV and measures its duration with
   FFprobe.

The Engine is not auto-started, downloaded, updated, or contacted outside
loopback. The resolved ID is runtime provenance, not an input setting.

## 5. Job Layout, Timing, and Artifacts

The fixed persisted settings are:

| Setting | Value |
|---|---:|
| Speaker | `ずんだもん / ノーマル` |
| Speech speed | `1.0` |
| Inter-utterance silence | `300 ms` |
| Maximum utterance length | `60` Unicode code points |
| Preview | `false` unless explicitly requested |

The ordered steps are:

1. `probe-input`
2. `parse-script`
3. `synthesize-utterances`
4. `concat-audio`
5. `render-artifacts`
6. `render-preview` only when requested

Relevant output layout:

```text
<name>.media-job/
  outputs/
    utterances/
      utterance-0001.wav
      ...
    narration.wav
    subtitles.srt
    subtitles.vtt
    preview.mp4                 # only when requested
  work/
    video-info.json
    utterances.json
    voicevox-provenance.json
```

Each utterance starts at zero for the first utterance, or exactly 300 ms after
the preceding utterance ends. Durations come from FFprobe measurements of the
actual WAVs, are converted to integer milliseconds, and are used unchanged for
both SRT and VTT. `narration.wav` includes the inserted silences. Its duration
must equal the final utterance end time within one millisecond of FFprobe
rounding. There is no attempt to align speech to `script.md` frame timestamps
in this phase.

SRT/VTT cue wording preserves the utterance text. SRT uses
`HH:MM:SS,mmm`; VTT uses `HH:MM:SS.mmm`. All cues are positive, chronological,
and non-overlapping. Output and artifact paths must resolve under the new job;
manifest artifact records must exactly match the required outputs.

## 6. Preview and Duration Warning

`-Preview` is explicit. It uses the original video stream, generated
`narration.wav`, and `subtitles.srt` to create a separate `outputs/preview.mp4`.
The original audio is not mapped into the preview. Japanese font selection and
stream/duration validation reuse the established transcribe preview rules.

If generated narration is longer than the input video, the job still succeeds
after required artifacts validate. It records a content-free warning containing
only source and narration durations. It must not change speech speed, delete
audio, truncate subtitles, shorten text, or modify source video.

## 7. Failure, Cancellation, and Resume

Every external call has HTTP-status/response validation; FFmpeg/FFprobe calls
have nonzero-exit and output-validation failure paths. Logs may contain paths,
durations, counts, versions, speaker identity, HTTP status, and stable failure
categories. They must not contain script text, utterance text, VOICEVOX query
payload text, or generated audio data.

Cancellation terminates the active FFmpeg process or stops before the next
VOICEVOX request, marks the active step and job `interrupted`, and preserves
completed artifacts. It never reports success.

Resume is available only for pending, interrupted, or failed narration jobs
whose single input fingerprint and every persisted setting exactly match.
Completed steps are reused only if all their work/output files, path
containment, WAV structures, measured durations, and artifact fingerprints
validate. The first invalid/incomplete step and later steps rerun. Existing
successful outputs are never overwritten; a different input/settings request
uses a new suffixed job directory.

## 8. Verification and Acceptance Boundary

The implementation must add deterministic local tests for script parsing,
segmentation, local-API request/response validation, speaker resolution,
integer timing, subtitle rendering, concat command construction, source
immutability, cancellation, resume, warning behavior, CLI, GUI, and artifact
set equality. Tests use a fake loopback client and synthetic WAV/FFmpeg fixtures;
they must not call a real Engine.

A later consolidated real acceptance will use the installed local VOICEVOX
Engine, verify `ずんだもん / ノーマル` synthesis and a human-audible preview, and
confirm no source modification or external network request. Resolve placement
and its acceptance remain a separate final phase.
