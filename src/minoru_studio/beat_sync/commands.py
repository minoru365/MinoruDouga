from __future__ import annotations

import argparse
import sys
from pathlib import Path

from minoru_studio.beat_sync.analyzer import AnalysisError, LibrosaBeatAnalyzer
from minoru_studio.beat_sync.plan import estimate_material_count
from minoru_studio.beat_sync.service import (
    BeatSyncRequest,
    BeatSyncService,
    PreparationFailed,
)


def parse_every_n(value):
    if value == "auto":
        return value
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("EveryN must be auto or 1..16") from exc
    if not 1 <= parsed <= 16:
        raise argparse.ArgumentTypeError("EveryN must be auto or 1..16")
    return parsed


def beat_sync_command(args):
    service = BeatSyncService()
    try:
        if args.beat_sync_action == "resume":
            job_dir = service.resume(Path(args.job_dir).resolve())
        else:
            required = {
                "Music": args.music,
                "MediaDir": args.media_dir,
                "TimelineName": args.timeline_name,
                "Name": args.name,
                "OutputDir": args.output_dir,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                args.command_parser.error(
                    "missing beat-sync options: " + ", ".join(missing)
                )
            job_dir = service.create_and_prepare(
                BeatSyncRequest(
                    Path(args.music),
                    Path(args.media_dir),
                    args.every_n,
                    args.order,
                    args.timeline_name,
                    args.name,
                    Path(args.output_dir).resolve(),
                )
            )
    except PreparationFailed as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(Path(job_dir).resolve())
    return 0


def beat_sync_estimate_command(args, analyzer=None):
    if not args.music:
        args.command_parser.error("missing beat-sync options: Music")
    if args.every_n == "auto":
        args.command_parser.error(
            "estimate requires a numeric -EveryN (auto depends on material count)"
        )
    analyzer = analyzer or LibrosaBeatAnalyzer()
    try:
        analysis = analyzer.analyze(Path(args.music))
    except AnalysisError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    count = estimate_material_count(analysis.cut_points_ms, args.every_n)
    print("bpm={0:.2f}".format(analysis.bpm))
    print("duration_ms={0}".format(analysis.duration_ms))
    print("every_n={0}".format(args.every_n))
    print("estimated_material_count={0}".format(count))
    return 0
