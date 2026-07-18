from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
from pathlib import Path
from uuid import uuid4


_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_ACCESS_DENIED = 5
_held_mutex_names: set[str] = set()
_held_mutex_names_lock = threading.Lock()


class JobLockedError(RuntimeError):
    pass


def _kernel32() -> ctypes.CDLL:
    if os.name != "nt":
        raise RuntimeError("job locks require Windows")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _configure_kernel32(kernel32: ctypes.CDLL) -> None:
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.ReleaseMutex.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int


def _pid_is_running(pid: int) -> bool:
    """Return whether a Windows process is running without signalling it."""
    if pid <= 0:
        return False
    if os.name != "nt":
        return False
    kernel32 = _kernel32()
    _configure_kernel32(kernel32)
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == _WAIT_TIMEOUT:
            return True
        if result == _WAIT_OBJECT_0:
            return False
        return True
    finally:
        kernel32.CloseHandle(handle)


def _mutex_name(job_dir: Path) -> str:
    canonical = str(Path(job_dir).resolve()).casefold().encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return f"Local\\minoru-studio-job-{digest}"


def _require_mutex_acquired(result: int) -> None:
    if result in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        return
    if result == _WAIT_TIMEOUT:
        raise JobLockedError("job is locked")
    if result == _WAIT_FAILED:
        raise ctypes.WinError(ctypes.get_last_error())
    raise RuntimeError(f"unexpected mutex wait result: {result:#x}")


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
        self._mutex_name = _mutex_name(self.path.parent)
        self._mutex_handle: int | None = None
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        kernel32 = _kernel32()
        _configure_kernel32(kernel32)
        handle = kernel32.CreateMutexW(None, False, self._mutex_name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        registered = False
        try:
            _require_mutex_acquired(kernel32.WaitForSingleObject(handle, 0))
            acquired = True
            with _held_mutex_names_lock:
                if self._mutex_name in _held_mutex_names:
                    raise JobLockedError("job is already locked by this process")
                _held_mutex_names.add(self._mutex_name)
            registered = True
            if self.path.exists() and lock_is_active(self.path.parent):
                raise JobLockedError("job has live lock metadata")
            self._write_metadata()
            self._mutex_handle = handle
            self._owned = True
        except BaseException:
            if registered:
                with _held_mutex_names_lock:
                    _held_mutex_names.discard(self._mutex_name)
            if acquired:
                kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
            raise

    def _write_metadata(self) -> None:
        temporary = self.path.parent / f".job-lock-{uuid4()}.tmp"
        try:
            temporary.write_text(
                json.dumps({"pid": os.getpid(), "token": self.token}) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def release(self) -> None:
        if not self._owned or self._mutex_handle is None:
            return
        kernel32 = _kernel32()
        _configure_kernel32(kernel32)
        handle = self._mutex_handle
        try:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if payload.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        finally:
            self._owned = False
            self._mutex_handle = None
            with _held_mutex_names_lock:
                _held_mutex_names.discard(self._mutex_name)
            try:
                kernel32.ReleaseMutex(handle)
            finally:
                kernel32.CloseHandle(handle)

    def __enter__(self) -> JobLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()
