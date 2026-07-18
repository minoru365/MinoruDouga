from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from minoru_studio.redaction import redact_text


_SECRET_COMMAND_ASSIGNMENT = re.compile(
    r"(?i)^(-{0,2}(?:api[_-]?key|access[_-]?token|auth|password|secret)"
    r"\s*[:=]\s*).*$"
)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    display_command: str


def _redact_display_argument(argument: str, secrets: Iterable[str]) -> str:
    redacted = redact_text(argument, secrets)
    return _SECRET_COMMAND_ASSIGNMENT.sub(r"\1***", redacted)


def run_process(
    args: Sequence[str],
    cwd: Path | None = None,
    timeout_s: float = 30,
    secrets: Iterable[str] = (),
) -> ProcessResult:
    if not args:
        raise ValueError("args must not be empty")
    secret_values = tuple(secrets)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        shell=False,
        check=False,
        creationflags=creationflags,
    )
    display = subprocess.list2cmdline(
        [_redact_display_argument(argument, secret_values) for argument in args]
    )
    return ProcessResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        display_command=display,
    )
