from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

_PATTERN = re.compile(r"^(\d{6})_(\d+)(\.[^.]+)$")


@dataclass(frozen=True, slots=True)
class RenamePlanItem:
    original: Path
    renamed: Path


def plan_renumbering(directory: str | Path) -> list[RenamePlanItem]:
    root = Path(directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")

    groups: dict[str, list[tuple[int, Path]]] = {}
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        match = _PATTERN.match(path.name)
        if not match:
            continue
        date, number = match.group(1), int(match.group(2))
        groups.setdefault(date, []).append((number, path))

    plan = []
    for date in sorted(groups):
        items = sorted(groups[date], key=lambda pair: pair[0])
        count = len(items)
        width = max(2, len(str(count - 1)))
        for new_index, (_old_number, path) in enumerate(reversed(items)):
            renamed = path.with_name(f"{date}_{new_index:0{width}d}{path.suffix}")
            if renamed != path:
                plan.append(RenamePlanItem(path, renamed))
    return plan


def apply_renumbering(plan: list[RenamePlanItem]) -> None:
    staged = []
    try:
        for item in plan:
            temporary = item.original.with_name(f".renumber-{uuid4().hex}{item.original.suffix}")
            item.original.rename(temporary)
            staged.append((temporary, item))
    except OSError:
        for temporary, item in staged:
            temporary.rename(item.original)
        raise
    for temporary, item in staged:
        temporary.rename(item.renamed)


def renumber_sequence_command(args) -> int:
    try:
        plan = plan_renumbering(args.folder)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not plan:
        print("no yyMMdd_# files need renaming.")
        return 0
    for item in plan:
        print(f"{item.original.name} -> {item.renamed.name}")
    if not args.apply:
        print(f"\n{len(plan)} file(s) would be renamed. Pass -Apply to actually rename.")
        return 0
    apply_renumbering(plan)
    print(f"\nrenamed {len(plan)} file(s).")
    return 0
