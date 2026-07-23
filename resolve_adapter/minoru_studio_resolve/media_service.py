import datetime
import os
import traceback
import uuid

from minoru_studio_resolve.contract import load_validated_media_job
from minoru_studio_resolve.gateway import GatewayError
from minoru_studio_resolve.resolve_log import append_resolve_log
from minoru_studio_resolve.service import AdapterError
from minoru_studio_resolve.state import TERMINAL_STATES, transition


_AUDIO_LABELS = {
    "transcribe": "元動画の音声",
    "narrate": "生成ナレーション",
}


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _ids():
    while True:
        yield str(uuid.uuid4())


def _same_path(left, right):
    return os.path.normcase(os.path.realpath(str(left))) == os.path.normcase(
        os.path.realpath(str(right))
    )


class MediaPlacementService(object):
    def __init__(
        self,
        gateway,
        applications,
        attempt_ids=None,
        operation_tokens=None,
        utc_now=None,
    ):
        self.gateway = gateway
        self.applications = applications
        self.attempt_ids = attempt_ids or _ids()
        self.operation_tokens = operation_tokens or _ids()
        self.utc_now = utc_now or _utc_now

    def _record_failure(self, root, event, detail, error, trace_text):
        if root is None:
            return
        try:
            append_resolve_log(
                root,
                event,
                detail=detail,
                error=error,
                traceback_text=trace_text,
            )
        except Exception:
            pass

    def _reject_existing_attempt(self, root, project_id, new_attempt):
        latest = self.applications.latest(root, project_id)
        if latest is None:
            return
        if latest["state"] in TERMINAL_STATES:
            if not new_attempt:
                raise AdapterError(
                    "latest Resolve attempt is terminal; use new_attempt=True"
                )
            return
        raise AdapterError(
            "Resolve attempt is unfinished; continue the current attempt"
        )

    def start(self, job_dir, new_attempt=False):
        detail = None
        token = None
        root = None
        try:
            validated = load_validated_media_job(job_dir)
            root = validated["root"]
            project = self.gateway.current_project()
            self._reject_existing_attempt(root, project["id"], new_attempt)
            detail = self.applications.create(
                root,
                validated["manifest"]["job_id"],
                project["id"],
                project["name"],
                attempt_id=next(self.attempt_ids),
                now=self.utc_now(),
                mode=validated["mode"],
                subtitle={
                    "path": validated["subtitle"]["path"],
                    "size": validated["subtitle"]["size"],
                    "sha256": validated["subtitle"]["sha256"],
                    "user_confirmed": False,
                },
            )
            token = next(self.operation_tokens)
            detail = self.applications.claim(
                root,
                detail["attempt_id"],
                token,
            )
            detail["resolve"] = self.gateway.product_info()
            detail["bin"] = self.gateway.create_application_bin(
                validated["timeline_name"],
                detail["attempt_id"],
            )
            detail["items"] = self.gateway.import_media_sources(
                validated["sources"],
                detail["bin"],
            )
            detail["timeline_rate"] = self.gateway.project_timeline_rate()
            transition(detail, "ready")
            detail = self.applications.update(root, detail)
            append_resolve_log(root, "media_start.completed", detail=detail)
        except AdapterError as exc:
            trace_text = traceback.format_exc()
            if detail is not None and root is not None and token is not None:
                try:
                    detail = self.applications.fail(
                        root,
                        detail["attempt_id"],
                        exc,
                        token=token,
                    )
                except Exception:
                    pass
            self._record_failure(
                root,
                "media_start.failed",
                detail,
                exc,
                trace_text,
            )
            raise
        except Exception as exc:
            trace_text = traceback.format_exc()
            if detail is not None and root is not None:
                try:
                    detail = self.applications.fail(
                        root,
                        detail["attempt_id"],
                        exc,
                        token=token,
                    )
                except Exception:
                    pass
            self._record_failure(
                root,
                "media_start.failed",
                detail,
                exc,
                trace_text,
            )
            raise AdapterError("Resolve staging failed: {0}".format(exc))
        finally:
            if detail is not None and root is not None and token is not None:
                try:
                    self.applications.release(
                        root,
                        detail["attempt_id"],
                        token,
                    )
                except Exception:
                    pass
        return self.applications.load(root, detail["attempt_id"])

    def latest_detail(self, job_dir):
        validated = load_validated_media_job(job_dir)
        project = self.gateway.current_project()
        detail = self.applications.latest(
            validated["root"],
            project["id"],
        )
        if detail is None:
            raise AdapterError(
                "no Resolve application exists for this project"
            )
        return detail

    def _current_attempt(self, root, required_state):
        project = self.gateway.current_project()
        detail = self.applications.latest(root, project["id"])
        if detail is None:
            raise AdapterError(
                "no Resolve application exists for this project"
            )
        if detail.get("operation_token") is not None:
            self.applications.mark_interrupted_failed(
                root,
                detail["attempt_id"],
            )
            raise AdapterError("previous Resolve operation was interrupted")
        if detail["state"] != required_state:
            raise AdapterError(
                "Resolve attempt is not at the {0} step".format(
                    required_state
                )
            )
        return detail

    def apply_ready(self, job_dir, confirm):
        validated = load_validated_media_job(job_dir)
        root = validated["root"]
        detail = self._current_attempt(root, "ready")
        proposed = self.gateway.proposed_timeline_name(
            validated["timeline_name"]
        )
        summary = {
            "mode": validated["mode"],
            "timeline_name": proposed,
            "audio_label": _AUDIO_LABELS[validated["mode"]],
            "subtitle_path": validated["subtitle"]["path"],
        }
        try:
            confirmed = bool(confirm(summary))
        except Exception as exc:
            self._record_failure(
                root,
                "media_apply.confirmation_failed",
                detail,
                exc,
                traceback.format_exc(),
            )
            raise AdapterError("confirmation failed: {0}".format(exc))
        if not confirmed:
            append_resolve_log(root, "media_apply.cancelled", detail=detail)
            return detail

        token = next(self.operation_tokens)
        claimed = False
        try:
            detail = self.applications.claim(
                root,
                detail["attempt_id"],
                token,
            )
            claimed = True
            transition(detail, "applying")
            detail = self.applications.update(root, detail)
            timeline, timeline_detail = self.gateway.create_final_timeline(
                validated["timeline_name"]
            )
            detail["timeline"] = timeline_detail
            detail = self.applications.update(root, detail)
            detail["timeline_rate"] = self.gateway.verify_timeline_rate(
                timeline,
                detail["timeline_rate"],
            )
            detail = self.applications.update(root, detail)
            items = self.gateway.find_media_items(
                detail["bin"],
                detail["items"],
            )
            detail["result"] = self.gateway.place_media_timeline(
                timeline,
                items,
                validated,
            )
            transition(detail, "awaiting_subtitle_import")
            detail = self.applications.update(root, detail)
            append_resolve_log(root, "media_apply.completed", detail=detail)
        except Exception as exc:
            trace_text = traceback.format_exc()
            if claimed:
                try:
                    detail = self.applications.fail(
                        root,
                        detail["attempt_id"],
                        exc,
                        token=token,
                    )
                except Exception:
                    pass
            self._record_failure(
                root,
                "media_apply.failed",
                detail,
                exc,
                trace_text,
            )
            if isinstance(exc, AdapterError):
                raise
            raise AdapterError("Resolve apply failed: {0}".format(exc))
        finally:
            if claimed:
                try:
                    self.applications.release(
                        root,
                        detail["attempt_id"],
                        token,
                    )
                except Exception:
                    pass
        return self.applications.load(root, detail["attempt_id"])

    def confirm_subtitle_import(self, job_dir, confirm):
        validated = load_validated_media_job(job_dir)
        root = validated["root"]
        detail = self._current_attempt(root, "awaiting_subtitle_import")
        stored = detail.get("subtitle") or {}
        current = validated["subtitle"]
        if (
            not _same_path(stored.get("path") or "", current["path"])
            or stored.get("size") != current["size"]
            or stored.get("sha256") != current["sha256"]
        ):
            raise AdapterError(
                "verified subtitle file changed since Resolve placement"
            )
        summary = {
            "mode": detail.get("mode"),
            "timeline_name": (detail.get("timeline") or {}).get("name"),
            "subtitle_path": current["path"],
        }
        try:
            confirmed = bool(confirm(summary))
        except Exception as exc:
            self._record_failure(
                root,
                "media_confirm.confirmation_failed",
                detail,
                exc,
                traceback.format_exc(),
            )
            raise AdapterError("confirmation failed: {0}".format(exc))
        if not confirmed:
            append_resolve_log(root, "media_confirm.cancelled", detail=detail)
            return detail

        token = next(self.operation_tokens)
        claimed = False
        checkpoint = False
        try:
            detail = self.applications.claim(
                root,
                detail["attempt_id"],
                token,
            )
            claimed = True
            transition(detail, "applying")
            detail = self.applications.update(root, detail)
            try:
                timeline = self.gateway.timeline_by_id(
                    (detail.get("timeline") or {}).get("id")
                )
                count = self.gateway.subtitle_track_count(timeline)
                if count < 1:
                    raise AdapterError(
                        "timeline has no subtitle track; import the "
                        "verified SRT through the Resolve UI first"
                    )
            except (AdapterError, GatewayError) as exc:
                checkpoint = True
                transition(detail, "awaiting_subtitle_import")
                detail = self.applications.update(root, detail)
                self._record_failure(
                    root,
                    "media_confirm.checkpoint",
                    detail,
                    exc,
                    traceback.format_exc(),
                )
                if isinstance(exc, AdapterError):
                    raise
                raise AdapterError(
                    "subtitle confirmation is incomplete: {0}".format(exc)
                )
            subtitle = dict(detail.get("subtitle") or {})
            subtitle["user_confirmed"] = True
            subtitle["track_count"] = count
            detail["subtitle"] = subtitle
            transition(detail, "applied")
            detail = self.applications.update(root, detail)
            append_resolve_log(root, "media_confirm.completed", detail=detail)
        except Exception as exc:
            if not checkpoint:
                trace_text = traceback.format_exc()
                if claimed:
                    try:
                        detail = self.applications.fail(
                            root,
                            detail["attempt_id"],
                            exc,
                            token=token,
                        )
                    except Exception:
                        pass
                self._record_failure(
                    root,
                    "media_confirm.failed",
                    detail,
                    exc,
                    trace_text,
                )
            if isinstance(exc, AdapterError):
                raise
            raise AdapterError(
                "subtitle confirmation failed: {0}".format(exc)
            )
        finally:
            if claimed:
                try:
                    self.applications.release(
                        root,
                        detail["attempt_id"],
                        token,
                    )
                except Exception:
                    pass
        return self.applications.load(root, detail["attempt_id"])
