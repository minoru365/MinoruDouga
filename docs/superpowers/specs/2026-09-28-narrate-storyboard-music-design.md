# Narrate storyboard background music

Status: design. Extends the [storyboard phase](2026-09-27-narrate-visual-sequence-design.md),
whose statement "BGM mixing is excluded" this document supersedes for storyboard
JSON only. Legacy video/still inputs, VOICEVOX, jobs, privacy and Resolve
contracts are unchanged.

## Descriptor

An optional top-level `music` object declares tracks; a clip may name one.

```json
{
  "version": 1,
  "music": {
    "tracks": [
      {"id": "normal", "source": "normal.mp3"},
      {"id": "battle", "source": "battle.mp3", "gain_db": -16}
    ],
    "default": "normal",
    "crossfade_ms": 1500
  },
  "clips": [
    {"id": "calm", "kind": "image", "source": "01.png", "narration": "..."},
    {"id": "fight", "kind": "image", "source": "02.png", "narration": "...", "music": "battle"}
  ]
}
```

- `tracks`: 1–8 objects with unique nonblank `id`, `source` and optional
  `gain_db` (number, -40 to 0, default -18). Sources follow the clip source rules
  (descriptor-relative, no URL/UNC, must exist) and use the beat-sync audio
  extensions (WAV/FLAC/MP3/OGG/M4A/AAC). A probed source must contain audio.
- `default`: required track id used by clips without `music`.
- `crossfade_ms`: optional integer 0–5000, default 1500.
- Clip `music` must reference a declared id. It is invalid without top-level
  `music`. Unknown keys, duplicate keys/ids and invalid references fail before
  synthesis. `version` stays 1: descriptors without `music` parse exactly as before.
- Music is applied only to `preview.mp4`. A descriptor with `music` requires
  `-Preview`; otherwise the request is rejected before job creation.
  `narration.wav`, SRT and VTT remain narration-only for external editing.

## Timing and mixing

Consecutive clips resolving to the same track form a run spanning the first
clip's `start_ms` to the last clip's `end_ms` in the persisted visual plan. Each
run plays its track from 0 and loops when the run is longer than the track.
Returning to a track restarts it.

At each boundary `t` the effective fade `f = min(crossfade_ms, previous run
length, next run length)`. The outgoing run extends to `t + f/2` and fades out
over `[t - f/2, t + f/2]`; the incoming run starts at `t - f/2` and fades in over
the same window. The final run fades out over `min(2000, run length)` ms. The
first run starts at 0 without fade-in. Gain is applied per run, then music and
unit-gain narration are mixed without normalization and passed through a
limiter. Speech timing is never changed; clip audio remains omitted.

## Storage and resume

Music sources are job inputs after visual sources, in track order, deduplicated.
`visual-input.json` records each music source's probed duration. The hash-verified
`visual-plan.json` records `music.runs` (track id, source, source SHA-256,
gain, start/end, fade-in/fade-out). Plans for descriptors without `music` are
unchanged. Changed music files, descriptors or plans are refused like visual
sources. Resolve placement already rejects storyboard jobs.

## Invariants and checks

| Operation | Allowed | Refused / invariant |
| --- | --- | --- |
| Parse | Declared tracks and references only | Undeclared id, clip music without tracks, out-of-range gain/crossfade, non-audio source |
| Create | `music` with preview | `music` without preview, before synthesis |
| Plan | Runs cover `[0, narration end]` without gaps; fades within runs | Run order differs from clip order |
| Resume | Hash-valid plan and unchanged music inputs | Changed music source or plan |
| Success | Preview has audio and video of narration duration | Duration alone does not prove musical intent; human listens |

Tests: parser acceptance/refusal, pure run and fade computation, FFmpeg argument
builder, service input fingerprinting/preview requirement/changed-music refusal,
unchanged no-music plans, and synthetic FFmpeg integration with tone tracks in
OS temp directories (audible music during narration gaps, correct duration).

Excluded: ducking, per-clip volume, track offsets, resuming a track where it
stopped, music for legacy video/still input, GUI/CLI changes (the descriptor
carries music), dependencies, and Resolve placement.
