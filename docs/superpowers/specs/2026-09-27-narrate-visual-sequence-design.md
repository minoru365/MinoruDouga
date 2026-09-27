# Narrate still images and mixed visual sequences

Status: implementation requested. This extension supersedes only the video-only
input restriction of the existing narration phase. It retains the same local
VOICEVOX, job, publication, cancellation and privacy contracts.

## Scope and inputs

- Existing video + TXT/Markdown script: preserve existing behavior and manifests.
- Single still image + TXT/Markdown script: hold the image for the full narration.
- Ordered storyboard JSON: explicit media/text associations, including still-only,
  video-only and mixed sequences. No inferred pairing by chapter/file number.
- Use the existing `NarrateService`, speaker resolution, synthesis, actual WAV
  measurements, 300 ms utterance gaps, subtitle builder and resume machinery.
- CLI and GUI both accept the new inputs. A storyboard owns its narration and
  cannot be combined with a separate `-Script` argument.

```json
{
  "version": 1,
  "clips": [
    {"id": "opening", "kind": "image", "source": "01.png", "narration": "最初の場面です。"},
    {"id": "movement", "kind": "video", "source": "02.mp4", "narration": "次の場面です。", "trim_start_ms": 1000, "trim_end_ms": 4000}
  ]
}
```

The JSON array determines playback order. Clip IDs are unique; sources may be
reused. Narration is nonblank for every clip and is split with the existing text
rules, retaining each clip's utterance range. To change visuals during a chapter,
split its narration across explicit clips. Several utterances may share an image.
Unknown fields, duplicate object keys/IDs, URLs, unsupported media, missing files,
empty or out-of-bounds video trims fail before synthesis. Relative sources resolve
against the original descriptor directory, not the snapshot directory or CWD.

`NarrateRequest` remains the video/still-plus-script contract. A distinct
`StoryboardRequest(input_path, name, output_dir, preview=False)` handles JSON.
Both use the same service. New jobs carry an explicit input-kind setting; its
absence means the legacy video contract and exact original settings.

## Timing and rendering

Still/storyboard previews use a 1280x720, 30 fps canvas, aspect-preserving scaling
and padding. Video trims play from their selected start, cut to the narration
interval if longer, and freeze their last frame if shorter. Original clip audio
is omitted, consistent with existing narrate. Speech is never accelerated or cut.

Visual intervals derive from the measured utterance timeline. The previous
visual covers the 300 ms inter-utterance gap; the next starts with its first cue.
Round cumulative boundaries to frames rather than rounding individual lengths.
Render silent segments in contained temporary work, then combine narration and
subtitles once. Persist a hash-verified visual plan for inspection and resume.
Content meaning is reviewed through the explicit storyboard and preview; equal
counts/durations are not proof of correct semantic association.

BGM mixing, automatic scene recognition, engine installation/startup, cloud
services, new dependencies and changes to Resolve placement are excluded. New
variants produce standalone narration/subtitles/optional MP4; existing Resolve
contracts must reject them clearly instead of treating JSON/images as video.

## Storage and privacy

Use a shared default output root under the user's Videos/MinoruStudio directory,
outside the checkout. Preserve explicit output choices and old resume paths.
Every newly created job gets a self-excluding `.gitignore` before private content
is written. The repository additionally ignores complete `*.media-job` folders,
known local work/output directories and existing ad-hoc production files.
Documentation uses external output examples. No existing asset is deleted,
moved, staged, committed or published by this change.

Storyboard manifests fingerprint the descriptor first, then distinct source
files in first-use order. Snapshot JSON byte-for-byte using create-only
publication. Typed source references, clip order, utterance coverage and source
fingerprints must agree. Private scripts, descriptors, source paths, reports and
thumbnails belong inside ignored jobs, never in tracked examples or logs.

## State and operation invariants

| Operation | Evaluation / allowed transition | Refuse / invariant |
| --- | --- | --- |
| Create | Validate input schema, references and trims before synthesis; allocate a unique job | No implicit image/text pairing or source writes |
| Claim/resume | Locked check of settings and all inputs; pending/failed/interrupted to running | Running/succeeded cannot restart; changed input/configuration refused |
| Reuse | Hash-valid snapshot, mapping, work, WAV provenance and exact artifact set | No stale, reordered or incomplete association is accepted |
| Source consumption | Revalidate source identities before reading and before success | Detected source change fails without adopting a different plan |
| Publication | Contained work followed by create-only publication | No overwrite/deletion of published artifacts or escaping symlinks |
| Partial failure/cancel | Retain completed outputs; record failed/interrupted state | No success after cancellation; only owned temporary work may be cleaned |
| Success | Expected utterance count, artifacts, visual plan and streams verified | Total duration alone cannot establish correct image mapping |
| Git exclusion | Ignore job before snapshots/logs/audio are written | Default output must not resolve to CWD; existing files preserved |

## Gates, audit and human review

Allowed edits: narration modules, CLI/GUI integration, shared output helper and
job creation exclusion, focused tests, docs, repository `.gitignore`/AGENTS.
Excluded: unrelated mode processing, dependencies, cloud, source media,
credentials, remote Git operations, real Resolve mutations and job migration.

Before implementation, add tests for output-root and complete-job exclusion.
The narration batch adds meaningful regression fixtures for multiple utterances
per image, repeated images, a split chapter followed by later scenes, invalid
IDs/references/trims, changed source/snapshot/mapping refusal, cancellation,
resumption and final-output conflicts. Retain legacy narration tests unchanged
except the intentional optional output-directory CLI requirement.

Run focused tests, the full existing suite and synthetic FFmpeg integration:
single still, mixed color still/motion clips, trim start, final-frame hold,
continuous audio/subtitles, source hashes and cumulative cut positions. All
generated test media use OS temporary directories outside the checkout. No real
VOICEVOX request, download or Resolve mutation is necessary for these gates.
Use `git check-ignore` for manifests, scripts, intermediate images, VTT and MP4,
and verify the public diff contains no user material or absolute personal paths.

One Sol design pass defines this checklist. One final independent lightweight
Sol audit uses only the final diff, this plan, summarized gates and residual risks.
Human review after implementation covers the visible synthetic preview and the
public diff/output exclusions. Input hashes detect concurrent edits but do not
lock other applications; no semantic correspondence detector is claimed.
