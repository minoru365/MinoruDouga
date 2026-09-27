# Repository working rules

Read `README.md` and `docs/agent-handoff.md` before changing workflows. Reuse
existing narration, media, job and validation modules before writing a separate
production script. Check installed tools and their configured local endpoints;
do not install or start VOICEVOX automatically.

## Private production files

This is a public source repository. Write generated videos, audio, extracted
frames, scripts, storyboards, reports and job packages outside the checkout by
default. Use the application's user Videos/MinoruStudio output directory, or an
explicit user-selected external location. Use OS temporary directories for
generated integration-test media.

If the user explicitly selects an in-repository output location, ensure the
whole output package is Git-ignored before writing private content. Do not rely
only on video/audio extension ignores: scripts, JSON manifests, paths, logs and
images are private too. Verify exclusions with `git check-ignore` and check the
diff before handoff. Never force-add generated production files.

Keep source code, synthetic text fixtures and documentation separate from real
user media. Existing documentation assets under `img/` are intentional tracked
examples, not a destination for new personal production outputs. Do not delete
or relocate existing user work just to clean the checkout.
