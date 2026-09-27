from __future__ import annotations

import argparse
from pathlib import Path
import sys

from minoru_studio.narrate.artifacts import read_duration_warning
from minoru_studio.narrate.models import NarrateRequest, StoryboardRequest
from minoru_studio.narrate.service import NarrateFailed, NarrateInterrupted, NarrateService, StoryboardMusicRequiresPreview
from minoru_studio.output_paths import default_output_dir


class _NarrateCreateOptionAction(argparse.Action):
    """Record create-only flags so resume can reject them explicitly."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        provided = set(getattr(namespace, "_narrate_create_options", ()))
        provided.add(self.dest)
        setattr(namespace, "_narrate_create_options", frozenset(provided))
        setattr(namespace, self.dest, self.const if self.nargs == 0 else values)


def narrate_command(args: argparse.Namespace) -> int:
    if args.input_or_action == "resume":
        _validate_resume_arguments(args)
        return _resume(args)
    _validate_create_arguments(args)
    return _create(args)


def _validate_resume_arguments(args: argparse.Namespace) -> None:
    if args.resume_job is None:
        args.command_parser.error("missing narration job directory")
    if args._narrate_create_options:
        args.command_parser.error("create-only narration options are not valid for resume")


def _validate_create_arguments(args: argparse.Namespace) -> None:
    if args.resume_job is not None:
        args.command_parser.error("narrate create accepts exactly one input path")
    storyboard = Path(args.input_or_action).suffix.casefold() == ".json"
    if storyboard and args.script is not None:
        args.command_parser.error("storyboard JSON contains narration; do not specify -Script")
    required = {"Name": args.name}
    if not storyboard:
        required["Script"] = args.script
    if args.output_dir is not None:
        required["OutputDir"] = args.output_dir
    missing = [name for name, value in required.items() if not isinstance(value, str) or not value.strip()]
    if missing:
        args.command_parser.error("missing narrate options: " + ", ".join(missing))
    if args.script is not None:
        args.script = args.script.strip()
    args.name = args.name.strip()
    args.output_dir = args.output_dir.strip() if args.output_dir is not None else str(default_output_dir())


def _create(args: argparse.Namespace) -> int:
    try:
        if Path(args.input_or_action).suffix.casefold() == ".json":
            request = StoryboardRequest(
                input_path=Path(args.input_or_action), name=args.name,
                output_dir=Path(args.output_dir).resolve(), preview=args.preview,
            )
        else:
            request = NarrateRequest(
                input_path=Path(args.input_or_action),
                script_path=Path(args.script),
                name=args.name,
                output_dir=Path(args.output_dir).resolve(),
                preview=args.preview,
            )
        job_dir = NarrateService().create_and_run(request)
    except NarrateInterrupted as exc:
        return _interrupted(exc)
    except NarrateFailed as exc:
        return _failed(exc)
    except StoryboardMusicRequiresPreview as exc:
        print(exc, file=sys.stderr)
        return 1
    except (ValueError, OSError):
        print("input validation", file=sys.stderr)
        return 1
    return _succeeded(job_dir)


def _resume(args: argparse.Namespace) -> int:
    try:
        job_dir = NarrateService().resume(Path(args.resume_job).resolve())
    except NarrateInterrupted as exc:
        return _interrupted(exc)
    except NarrateFailed as exc:
        return _failed(exc)
    return _succeeded(job_dir)


def _succeeded(job_dir: Path) -> int:
    resolved = Path(job_dir).resolve()
    print(resolved)
    warning = read_duration_warning(resolved)
    if warning is not None:
        print(
            "warning: narration duration "
            f"{warning.narration_duration_ms} ms exceeds source duration "
            f"{warning.source_duration_ms} ms",
            file=sys.stderr,
        )
    return 0


def _failed(error: NarrateFailed) -> int:
    print(error.category, file=sys.stderr)
    return 1


def _interrupted(error: NarrateInterrupted) -> int:
    print(Path(error.job_dir).resolve(), file=sys.stderr)
    return 130


__all__ = ["_NarrateCreateOptionAction", "narrate_command"]
