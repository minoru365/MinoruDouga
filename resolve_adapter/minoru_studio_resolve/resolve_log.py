import datetime
import json
import os

from minoru_studio_resolve.job_io import JobLock


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _identifier(value, key):
    if not isinstance(value, dict):
        return None
    return value.get(key)


def append_resolve_log(
    job_root,
    event,
    detail=None,
    error=None,
    traceback_text=None,
    now=None,
):
    root = os.path.realpath(job_root)
    detail = detail or {}
    result = detail.get("result") or {}
    payload = {
        "timestamp": now or _utc_now(),
        "event": str(event),
        "attempt_id": detail.get("attempt_id"),
        "job_id": detail.get("job_id"),
        "project_id": detail.get("project_id"),
        "state": detail.get("state"),
        "bin_id": _identifier(detail.get("bin"), "id"),
        "timeline_id": _identifier(detail.get("timeline"), "id"),
        "timeline_name": _identifier(detail.get("timeline"), "name"),
        "placed": result.get("placed"),
        "gaps": result.get("gaps"),
        "corrections": result.get("corrections"),
        "message": None if error is None else str(error),
        "traceback": traceback_text,
    }
    logs = os.path.join(root, "logs")
    if not os.path.isdir(logs):
        os.makedirs(logs)
    path = os.path.join(logs, "resolve.log")
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    with JobLock(root):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
    return path
