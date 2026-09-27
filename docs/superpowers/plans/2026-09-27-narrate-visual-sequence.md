# Narrate visual sequence implementation plan

Use the [phase specification](../specs/2026-09-27-narrate-visual-sequence-design.md)
as the state/operation model and acceptance checklist.

1. Capture the existing narration/job/GUI test baseline. Add output-root and Git
   exclusion tests, then implement shared external defaults and job exclusion.
2. One bounded core batch: explicit storyboard models/parser and text segmentation
   reuse; still probing; cue-derived visual plan; local FFmpeg renderer; integrate
   into the existing narration service and durable validation; regression tests.
3. Integrate CLI/GUI inputs and optional external output default. Document JSON
   associations, video trim/hold behavior, unsupported Resolve placement, and
   privacy defaults. Keep private examples/generated media outside the checkout.
4. Run focused tests, complete suite and synthetic FFmpeg acceptance outside the
   repository. Inspect late cuts and source immutability. Fix failures within the
   bounded scope; replan any material contract change.
5. After the final diff and gates, perform one independent acceptance audit using
   the predeclared checklist. Hand off commands, verification outcomes, residual
   limitations and the public diff for human review; no publish/commit operation.
