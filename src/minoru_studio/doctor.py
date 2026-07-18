from __future__ import annotations

import json
from importlib import import_module, metadata
import shutil
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum

from minoru_studio.processes import run_process
from minoru_studio.transcribe.models import MODEL_SPECS, default_model_cache_dir, model_is_complete


class CheckStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ToolCheck:
    name: str
    required: bool
    status: CheckStatus
    path: str | None
    version: str | None
    message: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: list[ToolCheck]

    @property
    def ok(self) -> bool:
        return all(
            not check.required or check.status is CheckStatus.OK
            for check in self.checks
        )


def _read_version(path: str, args: list[str]) -> str:
    result = run_process([path, *args], timeout_s=10)
    text = (result.stdout or result.stderr).strip().splitlines()
    if result.returncode != 0 or not text:
        raise RuntimeError(f"version command failed with {result.returncode}")
    return text[0]


def _external_check(
    name: str,
    candidates: tuple[str, ...],
    version_args: list[str],
) -> ToolCheck:
    path = next(
        (candidate_path for candidate in candidates if (candidate_path := shutil.which(candidate))),
        None,
    )
    if path is None:
        return ToolCheck(name, True, CheckStatus.MISSING, None, None, "not found on PATH")
    try:
        version = _read_version(path, version_args)
    except Exception as exc:
        return ToolCheck(name, True, CheckStatus.ERROR, path, None, str(exc))
    return ToolCheck(name, True, CheckStatus.OK, path, version, "available")


def _faster_whisper_check() -> ToolCheck:
    try:
        version = metadata.version("faster-whisper")
        import_module("faster_whisper")
    except metadata.PackageNotFoundError:
        return ToolCheck(
            "faster-whisper", True, CheckStatus.MISSING, None, None, "package is not installed"
        )
    except Exception:
        return ToolCheck(
            "faster-whisper", True, CheckStatus.ERROR, None, None, "package import failed"
        )
    cache_dir = default_model_cache_dir()
    cached = [name for name in MODEL_SPECS if model_is_complete(cache_dir, name)]
    message = "available; cached models: " + (", ".join(cached) if cached else "none")
    return ToolCheck("faster-whisper", True, CheckStatus.OK, None, version, message)


def run_doctor() -> DoctorReport:
    python_ok = sys.version_info[:2] == (3, 12)
    checks = [
        ToolCheck(
            "python",
            True,
            CheckStatus.OK if python_ok else CheckStatus.UNSUPPORTED,
            sys.executable,
            sys.version.split()[0],
            "Python 3.12" if python_ok else "Python 3.12 is required",
        ),
        _external_check("powershell", ("pwsh",), ["--version"]),
        _external_check("uv", ("uv",), ["--version"]),
        _external_check("ffmpeg", ("ffmpeg",), ["-version"]),
        _external_check("ffprobe", ("ffprobe",), ["-version"]),
        _faster_whisper_check(),
    ]
    return DoctorReport(checks)


def render_doctor(report: DoctorReport, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {"ok": report.ok, "checks": [asdict(item) for item in report.checks]},
            ensure_ascii=False,
            indent=2,
        )
    lines = ["MinoruStudio environment check"]
    lines.extend(
        f"[{item.status.value}] {item.name}: {item.message}"
        for item in report.checks
    )
    return "\n".join(lines)
