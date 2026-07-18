from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from uuid import uuid4


class JobLockedError(RuntimeError):
    pass


def _pid_is_running(pid: int) -> bool:
    """Return whether a Windows process is still running without signalling it."""
    if pid <= 0:
        return False
    if os.name != "nt":
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    open_process.restype = ctypes.c_void_p
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    wait_for_single_object.restype = ctypes.c_uint32
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int

    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    error_access_denied = 5
    handle = open_process(synchronize, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        result = wait_for_single_object(handle, 0)
        if result == wait_timeout:
            return True
        if result == wait_object_0:
            return False
        return False
    finally:
        close_handle(handle)


def lock_is_active(job_dir: Path) -> bool:
    path = Path(job_dir) / "job.lock"
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _pid_is_running(int(payload.get("pid", 0)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


class JobLock:
    def __init__(self, job_dir: Path):
        self.path = Path(job_dir) / "job.lock"
        self.token = str(uuid4())
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                )
            except FileExistsError:
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    pid = int(payload.get("pid", 0))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pid = 0
                if _pid_is_running(pid):
                    raise JobLockedError(f"job is locked by pid {pid}")
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "token": self.token}, handle)
                handle.write("\n")
            self._owned = True
            return
        raise JobLockedError("unable to acquire job lock")

    def release(self) -> None:
        if not self._owned:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if payload.get("token") == self.token:
            self.path.unlink(missing_ok=True)
        self._owned = False

    def __enter__(self) -> JobLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()
