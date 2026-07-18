from __future__ import annotations

import argparse
from pathlib import Path
import sys

from minoru_studio.transcribe.service import (
    TranscribeRequest,
    TranscribeService,
    TranscriptionFailed,
    TranscriptionInterrupted,
)


class _CreateOptionAction(argparse.Action):
    """Record mode-specific flags so resume can reject them explicitly."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        provided = set(getattr(namespace, "_transcribe_create_options", ()))
        provided.add(self.dest)
        setattr(namespace, "_transcribe_create_options", frozenset(provided))
        setattr(namespace, self.dest, self.const if self.nargs == 0 else values)


def parse_language(value: str) -> str:
    language = value.casefold()
    if language == "auto":
        return language
    if len(language) in (2, 3) and language.isascii() and language.isalpha():
        return language
    raise argparse.ArgumentTypeError(
        "Language must be auto or a two- or three-letter ASCII code"
    )


def transcribe_command(args: argparse.Namespace) -> int:
    if args.input_or_action == "resume":
        _validate_resume_arguments(args)
        return _resume(args)
    _validate_create_arguments(args)
    return _create(args)


def _validate_resume_arguments(args: argparse.Namespace) -> None:
    if args.resume_job is None:
        args.command_parser.error("missing transcription job directory")
    if args._transcribe_create_options:
        args.command_parser.error("create-only transcribe options are not valid for resume")


def _validate_create_arguments(args: argparse.Namespace) -> None:
    if args.resume_job is not None:
        args.command_parser.error("transcribe create accepts exactly one input path")
    required = {"Name": args.name, "OutputDir": args.output_dir}
    missing = [name for name, value in required.items() if not value]
    if missing:
        args.command_parser.error("missing transcribe options: " + ", ".join(missing))


def _create(args: argparse.Namespace) -> int:
    service = TranscribeService()
    try:
        job_dir = service.create_and_run(
            TranscribeRequest(
                input_path=Path(args.input_or_action),
                name=args.name,
                output_dir=Path(args.output_dir).resolve(),
                model=args.model,
                language=args.language,
                normalize=args.normalize,
                denoise=args.denoise,
                preview=args.preview,
            ),
            allow_model_download=args.allow_model_download,
        )
    except TranscriptionInterrupted as exc:
        return _interrupted(exc)
    except TranscriptionFailed as exc:
        return _failed(exc)
    print(Path(job_dir).resolve())
    return 0


def _resume(args: argparse.Namespace) -> int:
    service = TranscribeService()
    try:
        job_dir = service.resume(
            Path(args.resume_job).resolve(),
            allow_model_download=args.allow_model_download,
        )
    except TranscriptionInterrupted as exc:
        return _interrupted(exc)
    except TranscriptionFailed as exc:
        return _failed(exc)
    print(Path(job_dir).resolve())
    return 0


def _failed(error: TranscriptionFailed) -> int:
    print(str(error), file=sys.stderr)
    return 1


def _interrupted(error: TranscriptionInterrupted) -> int:
    print(Path(error.job_dir).resolve(), file=sys.stderr)
    return 130
