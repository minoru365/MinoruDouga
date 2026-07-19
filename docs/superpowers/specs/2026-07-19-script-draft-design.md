# MinoruStudio Phase 4: Script Draft Design

- Date: 2026-07-19
- Status: Approved for implementation planning
- Scope: Local representative-frame extraction and an editable video-script template
- Platform: Windows
- Governing design: [`../../media-automation-design.md`](../../media-automation-design.md),
  especially sections 7.4, 13, 15, and 17

## 1. Context

The completed `beat-sync` and `transcribe` modes establish MinoruStudio's CLI,
launcher GUI, job package, resumable step execution, FFmpeg/FFprobe process
handling, integer-millisecond time utilities, and source-preservation rules.

This phase implements the next fixed mode, `script-draft`. It turns one local
video into a small, chronological set of representative images and a Markdown
template a human can use to draft narration and screen actions. It does not
interpret the images, create narration, or contact an external service.

The global safety, logging, job-layout, and no-overwrite requirements remain
in the governing design and existing mode implementations; this document
specifies only the `script-draft` additions.

## 2. Goals and Non-goals

### Goals

1. Accept one local video with a decodable video stream and finite positive
   duration.
2. Extract representative frames from both scene changes and five-second
   intervals.
3. Save review-friendly frame images at no more than 1280 pixels on the long
   edge, without enlarging a smaller source.
4. Record actual source times as integer milliseconds, source resolution, and
   each frame's selection reason in a machine-readable index.
5. Generate an editable `script.md` with one section per selected frame.
6. Support non-interactive CLI create/resume and the existing launcher GUI.
7. Preserve completed work and permit a same-input, same-settings job to
   resume after interruption or a recoverable failure.

### Non-goals

This phase does not include:

- image description, OCR, image-model use, or any external image upload;
- automatic script, narration, subtitle, or music generation;
- audio-only input;
- user-configurable sampling rules in the initial UI or CLI;
- Resolve staging or timeline application;
- modifying, deleting, or replacing source media or a previous job.

## 3. Chosen Extraction Contract

The implementation uses FFmpeg and FFprobe only; it adds neither OpenCV nor a
new media-analysis dependency.

Two FFmpeg extraction passes produce candidate frames:

1. **Scene pass:** select frames whose FFmpeg scene-change score is at least
   `0.30`.
2. **Interval pass:** select a frame at `0 ms` and then every `5000 ms` while
   the target time is within the reported source duration.

Candidate timestamps are derived from the selected frames' actual presentation
timestamps and converted deterministically to integer milliseconds. Candidates
are sorted chronologically. Candidates from the same source frame, or whose
timestamps are at most `100 ms` apart, become one output entry; that entry
records every applicable reason (`scene` and/or `interval`). A scene candidate
is preferred for the saved image when candidates are merged.

The output image format is PNG. The FFmpeg scale filter preserves aspect ratio,
uses a 1280-pixel maximum long edge, and never upscales. Image names are stable
within a job: `frame-0001.png`, `frame-0002.png`, and so on in chronological
order. A video with no detected scene changes is valid: its interval frames are
sufficient. A valid video must yield at least the `0 ms` interval frame; if it
does not, the extraction step fails rather than producing an unusable template.

These initial constants are fixed mode settings, persisted in the job manifest,
not user-facing tuning controls:

| Setting | Value |
|---|---:|
| Scene-change threshold | `0.30` |
| Interval | `5000 ms` |
| Merge tolerance | `100 ms` |
| Maximum image long edge | `1280 px` |
| Image format | `png` |

## 4. CLI and GUI Contract

The create command is:

```powershell
minoru-studio script-draft .\input.mp4 -Name product-walkthrough -OutputDir .\jobs
```

The resume command is:

```powershell
minoru-studio script-draft resume .\jobs\product-walkthrough.media-job
```

`-Name` and `-OutputDir` are required for creation. They are invalid for
`resume`. The command accepts no sampling, image-size, AI, or Resolve option.
It prints the resolved job directory on success, returns `130` on cancellation,
and returns a concise error on failure. A non-interactive command never opens
the GUI.

The launcher adds a `script-draft` mode with a video picker, job name,
output-directory field, preflight/result summary, progress indication, and
cancel action. It has no extraction-tuning controls. It converts its values to
the same request object used by the CLI and runs that service in the existing
background-execution pattern.

## 5. Job Contract and Artifacts

The job uses `JobMode.SCRIPT_DRAFT`, the existing input fingerprint contract,
and these ordered steps:

1. `probe-input`
2. `extract-scene-frames`
3. `extract-interval-frames`
4. `render-draft`

The manifest records the fixed extraction settings, original video width and
height, reported duration in milliseconds, and FFmpeg/FFprobe versions. The
mode must register this step ordering rather than rely on the transcribe-only
ordering currently used for manifest presentation.

A successful job has these user-facing artifacts:

```text
<name>.media-job/
  outputs/
    frames/
      frame-0001.png
      frame-0002.png
      ...
    frame-index.json
    script.md
```

`frame-index.json` is UTF-8 JSON with `schema_version: 1`, source duration and
resolution, fixed extraction settings, and chronological entries. Every entry
contains a one-based `index`, integer `time_ms`, an `image_path` relative to
`outputs/`, and non-empty `reasons` containing `scene`, `interval`, or both.
It must not contain a source-media copy or image content.

`script.md` begins with the source duration/resolution and a brief statement
that the document is a human-editable draft. For each frame-index entry it
contains a heading with the millisecond-formatted time, a relative Markdown
image link, and empty sections titled `画面の説明`, `操作`, and `ナレーション`.
It never invents descriptions or narration.

The frame files, index, and template each receive existing artifact records.
All paths are resolved and verified to remain inside the selected job before
writing or accepting them on resume.

## 6. Failure, Cancellation, and Resume

Preflight fails before extraction if the source is absent, not a regular file,
has no decodable video stream, has no finite positive duration, or exposes no
positive source dimensions. Audio streams are optional and ignored.

Every FFmpeg invocation has both nonzero-exit handling and independent output
validation. Frame extraction fails if an expected frame is absent, unreadable,
outside the job, improperly scaled, or has no valid timestamp. Draft rendering
fails if the index is malformed, images do not match its entries, or the
Markdown links escape `outputs/frames/`.

Cancellation terminates the active FFmpeg process through the shared process
runner, marks the active step and job `interrupted`, and preserves completed
work. It neither deletes frames nor reports success.

Resume is available for a `pending`, `interrupted`, or failed job only when the
input fingerprint and all persisted settings match. Valid completed steps are
reused. The first incomplete or invalid step and every following step rerun;
if `render-draft` alone is invalid, valid extracted frames may be reused. Any
input or output-affecting-settings mismatch requires a newly created,
uniquely-suffixed job rather than mutating the prior job.

Logs may include paths, tool versions, dimensions, timestamps, counts, and
exit diagnostics. They must not include pixel data, generated frame binaries,
or unredacted command values that the shared redaction policy would remove.

## 7. Verification Strategy

Per the current project direction, comprehensive mode tests and real-media
acceptance are deferred to the final consolidated verification phase. This
implementation still requires minimal mechanical checks for the contracts it
introduces:

- CLI create/resume parsing and rejection of create-only options on resume;
- GUI form-to-request mapping without opening external applications;
- deterministic timestamp conversion, candidate merge, ordering, and naming;
- index and Markdown rendering, including correct relative image links;
- FFmpeg command construction for scene selection, interval sampling, and
  no-upscale 1280px scaling;
- video-only preflight, zero-exit/missing-output failure, cancellation, and
  unchanged-input resume with synthetic local fixtures;
- existing project tests remaining green.

The later consolidated acceptance must use an expendable local screen-recording
and verify that a human can review the frames, edit `script.md`, and correlate
each template section with its source time. It must also verify input
immutability, duplicate-name suffixing, interrupted-job resume, and absence of
any network or Resolve operation.

## 8. Residual Risks and Acceptance Boundary

FFmpeg scene scores are content-dependent, so `0.30` is a stable initial
default rather than a claim of universal visual quality. Five-second sampling
can still create many images for long videos, and the PNG artifacts can be
large. This phase deliberately favors readable UI/code frames over aggressive
compression. User-configurable sampling policies are deferred until actual
review use shows a need.

The implementation phase is ready to begin once this document is reviewed.
It is not product acceptance: the final consolidated test and real-media
acceptance phase remains required before `script-draft` is presented as
released functionality.
