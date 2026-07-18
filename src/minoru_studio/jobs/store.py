from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from minoru_studio.jobs.lock import JobLock, JobLockedError
from minoru_studio.jobs.model import (
    InputRef,
    JobManifest,
    JobMode,
    JobStatus,
    StepStatus,
    manifest_from_dict,
    manifest_to_dict,
    new_manifest,
)


_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class JobConflictError(RuntimeError):
    pass


def safe_job_name(value: str) -> str:
    cleaned = _INVALID_WINDOWS_CHARS.sub("_", value.strip()).rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise ValueError("job name must not be empty")
    if cleaned.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def fingerprint_file(path: Path) -> InputRef:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"input is not a file: {resolved}")
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    stat = resolved.stat()
    return InputRef(
        path=str(resolved),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=digest.hexdigest(),
    )


class JobStore:
    def create(
        self,
        root: Path,
        name: str,
        mode: JobMode,
        input_paths: Iterable[Path] = (),
        settings: Mapping[str, Any] | None = None,
    ) -> Path:
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        base = safe_job_name(name)
        input_refs = [fingerprint_file(path) for path in input_paths]
        sequence = 1
        while True:
            suffix = "" if sequence == 1 else f"-{sequence:03d}"
            candidate = root / f"{base}{suffix}.media-job"
            try:
                candidate.mkdir()
            except FileExistsError:
                sequence += 1
                continue
            break
        try:
            for child in ("inputs", "outputs", "work", "logs", "resolve"):
                (candidate / child).mkdir()
            manifest = new_manifest(base, mode)
            manifest.inputs = input_refs
            manifest.settings = dict(settings or {})
            self.save(candidate, manifest)
            return candidate
        except BaseException:
            shutil.rmtree(candidate)
            raise

    def load(
        self,
        job_dir: Path,
        recover_interrupted: bool = True,
    ) -> JobManifest:
        job_dir = Path(job_dir).resolve(strict=True)
        manifest = self._read_manifest(job_dir)
        if not recover_interrupted or manifest.status is not JobStatus.RUNNING:
            return manifest
        try:
            with self.locked(job_dir):
                latest = self._read_manifest(job_dir)
                if latest.status is JobStatus.RUNNING:
                    latest.status = JobStatus.INTERRUPTED
                    for step in latest.steps.values():
                        if step.status is StepStatus.RUNNING:
                            step.status = StepStatus.INTERRUPTED
                    self._write_manifest(job_dir, latest)
                return latest
        except JobLockedError:
            return self._read_manifest(job_dir)

    def save(self, job_dir: Path, manifest: JobManifest) -> None:
        job_dir = Path(job_dir).resolve()
        with JobLock(job_dir):
            target = job_dir / "job.json"
            if target.exists():
                latest = self._read_manifest(job_dir)
                if latest.updated_at != manifest.updated_at:
                    raise JobConflictError("job manifest has changed on disk")
            self._write_manifest(job_dir, manifest)

    def update(
        self,
        job_dir: Path,
        mutator: Callable[[JobManifest], object],
    ) -> JobManifest:
        job_dir = Path(job_dir).resolve()
        with JobLock(job_dir):
            manifest = self._read_manifest(job_dir)
            mutator(manifest)
            self._write_manifest(job_dir, manifest)
            return manifest

    def _read_manifest(self, job_dir: Path) -> JobManifest:
        data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        return manifest_from_dict(data)

    def _write_manifest(self, job_dir: Path, manifest: JobManifest) -> None:
        updated_at = datetime.now(UTC)
        token = updated_at.isoformat()
        if token == manifest.updated_at:
            token = (updated_at + timedelta(microseconds=1)).isoformat()
        manifest.updated_at = token
        target = job_dir / "job.json"
        temporary = job_dir / f".job-{uuid4()}.tmp"
        payload = json.dumps(
            manifest_to_dict(manifest),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def locked(self, job_dir: Path) -> JobLock:
        return JobLock(job_dir)
