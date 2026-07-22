from __future__ import annotations

import argparse
from pathlib import Path
import sys

from minoru_studio.script_draft.models import ScriptDraftRequest
from minoru_studio.script_draft.service import (
    ScriptDraftFailed,
    ScriptDraftInterrupted,
    ScriptDraftService,
)


class _ScriptDraftCreateOptionAction(argparse.Action):
    """Record create-only flags so resume can reject them explicitly."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        provided = set(getattr(namespace, "_script_draft_create_options", ()))
        provided.add(self.dest)
        setattr(namespace, "_script_draft_create_options", frozenset(provided))
        setattr(namespace, self.dest, values)


def script_draft_command(args: argparse.Namespace) -> int:
    if args.input_or_action == "resume":
        _validate_resume_arguments(args)
        return _resume(args)
    _validate_create_arguments(args)
    return _create(args)


def _validate_resume_arguments(args: argparse.Namespace) -> None:
    if args.resume_job is None:
        args.command_parser.error("missing script draft job directory")
    if args._script_draft_create_options:
        args.command_parser.error("create-only script draft options are not valid for resume")


def _validate_create_arguments(args: argparse.Namespace) -> None:
    if args.resume_job is not None:
        args.command_parser.error("script-draft create accepts exactly one input path")
    required = {"Name": args.name, "OutputDir": args.output_dir}
    missing = [name for name, value in required.items() if not value]
    if missing:
        args.command_parser.error("missing script-draft options: " + ", ".join(missing))


def _create(args: argparse.Namespace) -> int:
    service = ScriptDraftService()
    try:
        job_dir = service.create_and_run(
            ScriptDraftRequest(
                input_path=Path(args.input_or_action),
                name=args.name,
                output_dir=Path(args.output_dir).resolve(),
            )
        )
    except ScriptDraftInterrupted as exc:
        return _interrupted(exc)
    except ScriptDraftFailed as exc:
        return _failed(exc)
    print(Path(job_dir).resolve())
    return 0


def _resume(args: argparse.Namespace) -> int:
    service = ScriptDraftService()
    try:
        job_dir = service.resume(Path(args.resume_job).resolve())
    except ScriptDraftInterrupted as exc:
        return _interrupted(exc)
    except ScriptDraftFailed as exc:
        return _failed(exc)
    print(Path(job_dir).resolve())
    return 0


def _failed(error: ScriptDraftFailed) -> int:
    print(error.category, file=sys.stderr)
    return 1


def _interrupted(error: ScriptDraftInterrupted) -> int:
    print(Path(error.job_dir).resolve(), file=sys.stderr)
    return 130


__all__ = ["_ScriptDraftCreateOptionAction", "script_draft_command"]
