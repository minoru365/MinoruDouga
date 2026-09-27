# Narrate storyboard music implementation plan

Use the [specification](../specs/2026-09-28-narrate-storyboard-music-design.md)
as the model and checklist.

1. Tests first: descriptor parsing/refusals, run and fade computation, filter
   argument builder, service preview requirement and music input fingerprinting.
2. One bounded batch: parser model (`storyboard.py`), run planner and mixed
   renderer (`visual_media.py`), service integration of inputs, probe, plan and
   preview (`service.py`). No CLI/GUI changes.
3. Synthetic FFmpeg integration with tone tracks outside the repository.
4. README section update; focused tests, full suite, `git check-ignore` of
   generated outputs, public diff review.
5. Human listening check with a real storyboard; no commit or publish.
