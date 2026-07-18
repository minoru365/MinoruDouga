import ctypes
import datetime
import hashlib
import json
import os
import re
import threading
import uuid


_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_ACCESS_DENIED = 5
_HELD_MUTEX_NAMES = set()
_HELD_MUTEX_NAMES_LOCK = threading.Lock()
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class JobLockedError(RuntimeError):
    pass


class ApplicationBusy(RuntimeError):
    pass


class ApplicationRecordError(ValueError):
    pass


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _kernel32():
    if os.name != "nt":
        raise RuntimeError("job locks require Windows")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _configure_kernel32(kernel32):
    kernel32.OpenProcess.argtypes = (
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    )
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_wchar_p,
    )
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.ReleaseMutex.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int


def _pid_is_running(pid):
    if pid <= 0 or os.name != "nt":
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


def _mutex_name(job_dir):
    canonical = os.path.realpath(job_dir).casefold().encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return "Local\\minoru-studio-job-{0}".format(digest)


def _require_mutex_acquired(result):
    if result in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        return
    if result == _WAIT_TIMEOUT:
        raise JobLockedError("job is locked")
    if result == _WAIT_FAILED:
        raise ctypes.WinError(ctypes.get_last_error())
    raise RuntimeError("unexpected mutex wait result: {0:#x}".format(result))


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ApplicationRecordError(
            "cannot read application record: {0}".format(exc)
        )
    if not isinstance(payload, dict):
        raise ApplicationRecordError("application JSON root must be an object")
    return payload


def _atomic_json(path, payload):
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    temporary = os.path.join(
        parent,
        ".{0}-{1}.tmp".format(os.path.basename(path), uuid.uuid4()),
    )
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _lock_is_active(path):
    if not os.path.exists(path):
        return False
    try:
        payload = _read_json(path)
        return _pid_is_running(int(payload.get("pid", 0)))
    except (ApplicationRecordError, TypeError, ValueError):
        return False


class JobLock(object):
    def __init__(self, job_dir):
        self.job_dir = os.path.realpath(job_dir)
        self.path = os.path.join(self.job_dir, "job.lock")
        self.token = str(uuid.uuid4())
        self.mutex_name = _mutex_name(self.job_dir)
        self.mutex_handle = None
        self.owned = False

    def acquire(self):
        if not os.path.isdir(self.job_dir):
            os.makedirs(self.job_dir)
        kernel32 = _kernel32()
        _configure_kernel32(kernel32)
        handle = kernel32.CreateMutexW(None, False, self.mutex_name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        registered = False
        try:
            _require_mutex_acquired(kernel32.WaitForSingleObject(handle, 0))
            acquired = True
            with _HELD_MUTEX_NAMES_LOCK:
                if self.mutex_name in _HELD_MUTEX_NAMES:
                    raise JobLockedError("job is already locked by this process")
                _HELD_MUTEX_NAMES.add(self.mutex_name)
            registered = True
            if _lock_is_active(self.path):
                raise JobLockedError("job has live lock metadata")
            _atomic_json(
                self.path,
                {"pid": os.getpid(), "token": self.token},
            )
            self.mutex_handle = handle
            self.owned = True
        except BaseException:
            if registered:
                with _HELD_MUTEX_NAMES_LOCK:
                    _HELD_MUTEX_NAMES.discard(self.mutex_name)
            if acquired:
                kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
            raise

    def release(self):
        if not self.owned or self.mutex_handle is None:
            return
        kernel32 = _kernel32()
        _configure_kernel32(kernel32)
        handle = self.mutex_handle
        try:
            try:
                payload = _read_json(self.path)
            except ApplicationRecordError:
                payload = {}
            if payload.get("token") == self.token and os.path.exists(self.path):
                os.remove(self.path)
        finally:
            self.owned = False
            self.mutex_handle = None
            with _HELD_MUTEX_NAMES_LOCK:
                _HELD_MUTEX_NAMES.discard(self.mutex_name)
            try:
                kernel32.ReleaseMutex(handle)
            finally:
                kernel32.CloseHandle(handle)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.release()


def _attempt_path(job_dir, attempt_id):
    value = str(attempt_id)
    if not _ATTEMPT_ID.match(value) or value in (".", ".."):
        raise ApplicationRecordError("invalid application attempt_id")
    return os.path.join(
        os.path.realpath(job_dir),
        "resolve",
        "applications",
        value + ".json",
    )


def _contained_detail_path(job_dir, relative):
    root = os.path.realpath(job_dir)
    candidate = os.path.realpath(
        os.path.join(root, str(relative).replace("/", os.sep))
    )
    try:
        common = os.path.commonpath([root, candidate])
    except ValueError:
        raise ApplicationRecordError("application detail path escapes job")
    if os.path.normcase(common) != os.path.normcase(root):
        raise ApplicationRecordError("application detail path escapes job")
    return candidate


def _summary(detail):
    return {
        "attempt_id": detail["attempt_id"],
        "state": detail["state"],
        "detail_path": "resolve/applications/{0}.json".format(
            detail["attempt_id"]
        ),
        "project_id": detail["project_id"],
        "updated_at": detail["updated_at"],
    }


class ApplicationStore(object):
    def create(
        self,
        job_dir,
        job_id,
        project_id,
        project_name,
        attempt_id=None,
        now=None,
    ):
        root = os.path.realpath(job_dir)
        attempt_id = str(attempt_id or uuid.uuid4())
        timestamp = now or _utc_now()
        detail = {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "job_id": job_id,
            "state": "staging",
            "project_id": project_id,
            "project_name": project_name,
            "created_at": timestamp,
            "updated_at": timestamp,
            "operation_token": None,
            "bin": None,
            "items": [],
            "source_windows": [],
            "still": None,
            "timeline": None,
            "result": None,
            "last_error": None,
        }
        path = _attempt_path(root, attempt_id)
        with JobLock(root):
            manifest = _read_json(os.path.join(root, "job.json"))
            if os.path.exists(path):
                raise ApplicationRecordError("application attempt already exists")
            summaries = manifest.get("resolve_applications", [])
            if not isinstance(summaries, list):
                raise ApplicationRecordError(
                    "resolve_applications must be a list"
                )
            if any(item.get("attempt_id") == attempt_id for item in summaries):
                raise ApplicationRecordError("application attempt already exists")
            _atomic_json(path, detail)
            summaries.append(_summary(detail))
            manifest["resolve_applications"] = summaries
            manifest["updated_at"] = timestamp
            _atomic_json(os.path.join(root, "job.json"), manifest)
        return detail

    def load(self, job_dir, attempt_id):
        return _read_json(_attempt_path(job_dir, attempt_id))

    def _write_locked(self, root, manifest, detail):
        path = _attempt_path(root, detail["attempt_id"])
        _atomic_json(path, detail)
        summaries = manifest.get("resolve_applications", [])
        if not isinstance(summaries, list):
            raise ApplicationRecordError("resolve_applications must be a list")
        replacement = _summary(detail)
        matched = False
        for index, summary in enumerate(summaries):
            if summary.get("attempt_id") == detail["attempt_id"]:
                summaries[index] = replacement
                matched = True
                break
        if not matched:
            summaries.append(replacement)
        manifest["resolve_applications"] = summaries
        manifest["updated_at"] = detail["updated_at"]
        _atomic_json(os.path.join(root, "job.json"), manifest)

    def update(self, job_dir, detail, now=None):
        root = os.path.realpath(job_dir)
        updated = dict(detail)
        updated["updated_at"] = now or _utc_now()
        with JobLock(root):
            manifest = _read_json(os.path.join(root, "job.json"))
            current = self.load(root, updated["attempt_id"])
            if current.get("job_id") != updated.get("job_id"):
                raise ApplicationRecordError("application job_id changed")
            self._write_locked(root, manifest, updated)
        return updated

    def claim(self, job_dir, attempt_id, token, now=None):
        root = os.path.realpath(job_dir)
        with JobLock(root):
            manifest = _read_json(os.path.join(root, "job.json"))
            detail = self.load(root, attempt_id)
            if detail.get("operation_token") is not None:
                raise ApplicationBusy("Resolve application already has an owner")
            detail["operation_token"] = str(token)
            detail["updated_at"] = now or _utc_now()
            self._write_locked(root, manifest, detail)
            return detail

    def release(self, job_dir, attempt_id, token, now=None):
        root = os.path.realpath(job_dir)
        with JobLock(root):
            manifest = _read_json(os.path.join(root, "job.json"))
            detail = self.load(root, attempt_id)
            if detail.get("operation_token") != str(token):
                return detail
            detail["operation_token"] = None
            detail["updated_at"] = now or _utc_now()
            self._write_locked(root, manifest, detail)
            return detail

    def fail(self, job_dir, attempt_id, error, token=None, now=None):
        root = os.path.realpath(job_dir)
        with JobLock(root):
            manifest = _read_json(os.path.join(root, "job.json"))
            detail = self.load(root, attempt_id)
            if token is not None and detail.get("operation_token") != str(token):
                raise ApplicationBusy("Resolve application ownership changed")
            if detail.get("state") != "applied":
                detail["state"] = "failed"
            detail["operation_token"] = None
            detail["last_error"] = str(error)[:1000]
            detail["updated_at"] = now or _utc_now()
            self._write_locked(root, manifest, detail)
            return detail

    def mark_interrupted_failed(self, job_dir, attempt_id, now=None):
        detail = self.load(job_dir, attempt_id)
        if detail.get("operation_token") is None:
            return detail
        return self.fail(
            job_dir,
            attempt_id,
            "previous Resolve operation was interrupted",
            token=detail["operation_token"],
            now=now,
        )

    def reconcile(self, job_dir):
        root = os.path.realpath(job_dir)
        with JobLock(root):
            manifest = _read_json(os.path.join(root, "job.json"))
            summaries = manifest.get("resolve_applications", [])
            if not isinstance(summaries, list):
                raise ApplicationRecordError(
                    "resolve_applications must be a list"
                )
            changed = False
            for summary in summaries:
                if not isinstance(summary, dict):
                    raise ApplicationRecordError(
                        "application summary must be an object"
                    )
                path = _contained_detail_path(root, summary.get("detail_path"))
                detail = _read_json(path)
                if (
                    summary.get("state") != detail.get("state")
                    or summary.get("updated_at") != detail.get("updated_at")
                ):
                    summary["state"] = detail["state"]
                    summary["updated_at"] = detail["updated_at"]
                    changed = True
            if changed:
                manifest["updated_at"] = _utc_now()
                _atomic_json(os.path.join(root, "job.json"), manifest)
            return manifest

    def latest(self, job_dir, project_id, states=None):
        root = os.path.realpath(job_dir)
        manifest = self.reconcile(root)
        allowed = None if states is None else frozenset(states)
        matches = []
        for index, summary in enumerate(
            manifest.get("resolve_applications", [])
        ):
            if summary.get("project_id") != project_id:
                continue
            if allowed is not None and summary.get("state") not in allowed:
                continue
            matches.append((summary.get("updated_at", ""), index, summary))
        if not matches:
            return None
        summary = max(matches, key=lambda item: (item[0], item[1]))[2]
        return _read_json(
            _contained_detail_path(root, summary["detail_path"])
        )
