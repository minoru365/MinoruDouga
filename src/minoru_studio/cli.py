from __future__ import annotations

import argparse
from collections.abc import Sequence

from minoru_studio import __version__
from minoru_studio.doctor import render_doctor, run_doctor


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))
