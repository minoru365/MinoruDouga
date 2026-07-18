from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from minoru_studio.redaction import redact_text


_SECRET_COMMAND_ASSIGNMENT = re.compile(
    r"(?i)^(-{0,2}(?:api[_-]?key|access[_-]?token|auth|password|secret)"
    r"\s*[:=]\s*).*$"
)


class ProcessTimeoutError(TimeoutError):
    def __init__(self, timeout_s: float, display_command: str) -> None:
        self.timeout_s = timeout_s
        self.display_command = display_command
        super().__init__(f"process timed out after {timeout_s}s: {display_command}")


class ProcessCancelledError(Exception):
    def __init__(self, display_command: str) -> None:
        self.display_command = display_command
        super().__init__(f"process cancelled: {display_command}")


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    display_command: str


def _redact_display_argument(argument: str, secrets: Iterable[str]) -> str:
    redacted = redact_text(argument, secrets)
    return _SECRET_COMMAND_ASSIGNMENT.sub(r"\1***", redacted)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_process(
    args: Sequence[str],
    cwd: Path | None = None,
    timeout_s: float = 30,
    secrets: Iterable[str] = (),
) -> ProcessResult:
    if not args:
        raise ValueError("args must not be empty")
    command_args = list(args)
    secret_values = tuple(secrets)
    display = subprocess.list2cmdline(
        [_redact_display_argument(argument, secret_values) for argument in command_args]
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    timed_out = False
    try:
        completed = subprocess.run(
            command_args,
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
    except subprocess.TimeoutExpired:
        timed_out = True
    if timed_out:
        raise ProcessTimeoutError(timeout_s, display) from None
    return ProcessResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        display_command=display,
    )


def run_cancellable_process(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_s: float | None = None,
    secrets: Iterable[str] = (),
    cancel_event: object | None = None,
    poll_interval_s: float = 0.1,
) -> ProcessResult:
    if not args:
        raise ValueError("args must not be empty")
    command_args = list(args)
    secret_values = tuple(secrets)
    display = subprocess.list2cmdline(
        [_redact_display_argument(argument, secret_values) for argument in command_args]
    )
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCancelledError(display)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command_args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        creationflags=creationflags,
    )
    deadline = time.monotonic() + timeout_s if timeout_s is not None else None
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _terminate_process(process)
            process.communicate()
            raise ProcessCancelledError(display) from None
        if deadline is not None and time.monotonic() >= deadline:
            _terminate_process(process)
            process.communicate()
            raise ProcessTimeoutError(timeout_s, display) from None
        try:
            stdout, stderr = process.communicate(timeout=poll_interval_s)
            break
        except subprocess.TimeoutExpired:
            continue
        except KeyboardInterrupt:
            _terminate_process(process)
            process.communicate()
            raise ProcessCancelledError(display) from None
    return ProcessResult(
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        display_command=display,
    )
