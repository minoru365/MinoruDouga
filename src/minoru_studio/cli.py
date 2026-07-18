from __future__ import annotations

import argparse
from collections.abc import Sequence

from minoru_studio import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minoru-studio",
        description="MinoruStudio local video-production tools",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_subparsers(dest="command")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))
