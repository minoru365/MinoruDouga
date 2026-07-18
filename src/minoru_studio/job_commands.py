from __future__ import annotations

import argparse
import json
from pathlib import Path

from minoru_studio.jobs.model import JobMode, manifest_to_dict
from minoru_studio.jobs.store import JobStore


def create_job_command(args: argparse.Namespace) -> int:
    job_dir = JobStore().create(
        root=Path(args.output_dir).resolve(),
        name=args.name,
        mode=JobMode(args.mode),
    )
    print(job_dir.resolve())
    return 0


def inspect_job_command(args: argparse.Namespace) -> int:
    manifest = JobStore().load(Path(args.job_dir), recover_interrupted=False)
    print(json.dumps(manifest_to_dict(manifest), ensure_ascii=False, indent=2))
    return 0
