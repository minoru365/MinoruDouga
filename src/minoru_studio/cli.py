from __future__ import annotations

import argparse
from collections.abc import Sequence

from minoru_studio import __version__
from minoru_studio.beat_sync.commands import beat_sync_command, parse_every_n
from minoru_studio.doctor import render_doctor, run_doctor
from minoru_studio.job_commands import create_job_command, inspect_job_command
from minoru_studio.jobs.model import JobMode
from minoru_studio.transcribe.commands import (
    _CreateOptionAction,
    parse_language,
    transcribe_command,
)
from minoru_studio.script_draft.commands import (
    _ScriptDraftCreateOptionAction,
    script_draft_command,
)


def launch_gui() -> None:
    from minoru_studio.gui import launch_gui as run_gui

    run_gui()


def _doctor_command(args: argparse.Namespace) -> int:
    report = run_doctor()
    print(render_doctor(report, as_json=args.as_json))
    return 0 if report.ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minoru-studio",
        description="MinoruStudio local video-production tools",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")
    doctor_parser = subparsers.add_parser("doctor", help="check required local tools")
    doctor_parser.add_argument("-Json", "--json", dest="as_json", action="store_true")
    doctor_parser.set_defaults(handler=_doctor_command)

    jobs_parser = subparsers.add_parser("jobs", help="create and inspect job packages")
    jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command", required=True)

    create_parser = jobs_subparsers.add_parser("create", help="create a pending job")
    create_parser.add_argument(
        "-Mode", "--mode", dest="mode", required=True,
        choices=[mode.value for mode in JobMode],
    )
    create_parser.add_argument("-Name", "--name", dest="name", required=True)
    create_parser.add_argument(
        "-OutputDir", "--output-dir", dest="output_dir", required=True,
    )
    create_parser.set_defaults(handler=create_job_command)

    inspect_parser = jobs_subparsers.add_parser("inspect", help="print a job manifest")
    inspect_parser.add_argument("job_dir")
    inspect_parser.set_defaults(handler=inspect_job_command)

    beat_sync_parser = subparsers.add_parser(
        "beat-sync",
        help="prepare a beat-synced Resolve job",
    )
    beat_sync_parser.set_defaults(
        handler=beat_sync_command,
        command_parser=beat_sync_parser,
        beat_sync_action=None,
    )
    beat_sync_parser.add_argument("-Music", "--music", dest="music")
    beat_sync_parser.add_argument("-MediaDir", "--media-dir", dest="media_dir")
    beat_sync_parser.add_argument(
        "-EveryN",
        "--every-n",
        dest="every_n",
        type=parse_every_n,
        default="auto",
    )
    beat_sync_parser.add_argument(
        "-Order",
        "--order",
        choices=("asc", "random"),
        default="asc",
    )
    beat_sync_parser.add_argument(
        "-TimelineName",
        "--timeline-name",
        dest="timeline_name",
    )
    beat_sync_parser.add_argument("-Name", "--name", dest="name")
    beat_sync_parser.add_argument("-OutputDir", "--output-dir", dest="output_dir")
    actions = beat_sync_parser.add_subparsers(dest="beat_sync_action")
    resume_parser = actions.add_parser("resume", help="resume interrupted preparation")
    resume_parser.add_argument("job_dir")
    resume_parser.set_defaults(
        handler=beat_sync_command,
        command_parser=beat_sync_parser,
    )

    transcribe_parser = subparsers.add_parser(
        "transcribe",
        help="create or resume a local transcription job",
    )
    transcribe_parser.set_defaults(
        handler=transcribe_command,
        command_parser=transcribe_parser,
        _transcribe_create_options=frozenset(),
    )
    transcribe_parser.add_argument("input_or_action")
    transcribe_parser.add_argument("resume_job", nargs="?")
    transcribe_parser.add_argument(
        "-Name", "--name", dest="name", action=_CreateOptionAction,
    )
    transcribe_parser.add_argument(
        "-OutputDir", "--output-dir", dest="output_dir", action=_CreateOptionAction,
    )
    transcribe_parser.add_argument(
        "-Model", "--model", choices=("small", "medium"), default="small",
        action=_CreateOptionAction,
    )
    transcribe_parser.add_argument(
        "-Language", "--language", type=parse_language, default="ja",
        action=_CreateOptionAction,
    )
    transcribe_parser.add_argument(
        "-Normalize", "--normalize", action=_CreateOptionAction,
        nargs=0, const=True, default=False,
    )
    transcribe_parser.add_argument(
        "-Denoise", "--denoise", action=_CreateOptionAction,
        nargs=0, const=True, default=False,
    )
    transcribe_parser.add_argument(
        "-Preview", "--preview", action=_CreateOptionAction,
        nargs=0, const=True, default=False,
    )
    transcribe_parser.add_argument(
        "-AllowModelDownload", "--allow-model-download",
        dest="allow_model_download", action="store_true",
    )

    script_draft_parser = subparsers.add_parser(
        "script-draft",
        help="extract local video frames and create a script template",
        description=(
            "create: INPUT_PATH -Name NAME -OutputDir OUTPUT_DIR\n"
            "resume: resume JOB_DIR"
        ),
    )
    script_draft_parser.set_defaults(
        handler=script_draft_command,
        command_parser=script_draft_parser,
        _script_draft_create_options=frozenset(),
    )
    script_draft_parser.add_argument("input_or_action", metavar="INPUT_PATH | resume")
    script_draft_parser.add_argument("resume_job", nargs="?", metavar="JOB_DIR")
    script_draft_parser.add_argument(
        "-Name", "--name", dest="name", action=_ScriptDraftCreateOptionAction,
    )
    script_draft_parser.add_argument(
        "-OutputDir", "--output-dir", dest="output_dir", action=_ScriptDraftCreateOptionAction,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        launch_gui()
        return 0
    return int(handler(args))
