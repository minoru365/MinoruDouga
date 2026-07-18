from __future__ import annotations

import argparse
from collections.abc import Sequence

from minoru_studio import __version__
from minoru_studio.doctor import render_doctor, run_doctor
from minoru_studio.job_commands import create_job_command, inspect_job_command
from minoru_studio.jobs.model import JobMode


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))
