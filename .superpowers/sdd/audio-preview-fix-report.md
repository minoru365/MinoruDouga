# Audio-only preview preflight fix report

## RED

- Added service regressions for a fresh audio-only `preview=True` request and
  a resumed job with cached audio-only probe metadata.
- `uv run pytest tests/transcribe/test_service.py -q` failed as expected:
  2 failures, 11 passes. Both paths advanced past `probe-input`; the fresh
  request completed preview rendering and the cached-resume path rendered the
  missing preview step.

## GREEN

- `TranscribeService` now validates the shared media invariant at both fresh
  probe handling and cached probe reconciliation: every input needs audio, and
  preview requests additionally need video.
- Fresh and cached audio-only preview requests fail with the stable `input
  validation` category before extraction, worker invocation, artifact rendering,
  or preview rendering. The cached path invalidates prior downstream successes
  before it reprobes and rejects the input.
- Added a preservation regression confirming audio-only transcription remains
  successful when `preview=False`.

## Verification counts

- Focused service suite: 14 passed.
- Related transcription, CLI, and GUI-controller regression suite: 107 passed.
- Full suite: 244 passed.
- `git diff --check`: passed.

## Commit

- `fix: reject audio-only previews before inference`

## Residual risk

- No real FFmpeg/model inference was run, by design. Re-run the previously
  failed real acceptance audio-only preview scenario and the outstanding manual
  GUI smoke before release promotion.
