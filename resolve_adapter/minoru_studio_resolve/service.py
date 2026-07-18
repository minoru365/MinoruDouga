import datetime
import uuid

from minoru_studio_resolve.contract import load_validated_job
from minoru_studio_resolve.state import TERMINAL_STATES, transition


class AdapterError(RuntimeError):
    pass


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _ids():
    while True:
        yield str(uuid.uuid4())


class AdapterService(object):
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
        raise AdapterError("Resolve attempt can be resumed; call resume")

    def start(self, job_dir, new_attempt=False, stop_after_stage=False):
        detail = None
        token = None
        root = None
        try:
            validated = load_validated_job(job_dir)
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
            )
            token = next(self.operation_tokens)
            detail = self.applications.claim(
                root,
                detail["attempt_id"],
                token,
            )
            detail["resolve"] = self.gateway.product_info()
            detail["bin"] = self.gateway.create_application_bin(
                validated["plan"]["settings"]["timeline_name"],
                detail["attempt_id"],
            )
            detail["items"] = self.gateway.import_inputs(
                validated,
                detail["bin"],
            )
            has_video = any(
                item["kind"] == "video" for item in detail["items"]
            )
            transition(
                detail,
                "awaiting_in_out" if has_video else "checking_still",
            )
            detail = self.applications.update(root, detail)
            if detail["state"] == "checking_still" and not stop_after_stage:
                items = self.gateway.find_items(detail["bin"], detail["items"])
                detail = self._check_still(validated, detail, items)
        except AdapterError:
            raise
        except Exception as exc:
            if detail is not None and root is not None:
                try:
                    self.applications.fail(
                        root,
                        detail["attempt_id"],
                        exc,
                        token=token,
                    )
                except Exception:
                    pass
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

    def _check_still(self, validated, detail, items):
        photos = [
            item for item in detail["items"] if item["kind"] == "photo"
        ]
        if not photos:
            raise AdapterError("still check requires a photo")
        photo = items.get(photos[0]["input_index"])
        if photo is None:
            raise AdapterError("recorded photo item is missing")
        measurement = self.gateway.probe_still(
            photo,
            validated["plan"]["analysis"]["cut_points_ms"],
            validated["plan"]["settings"]["every_n_resolved"],
            detail["attempt_id"],
        )
        detail["still"] = measurement
        detail["timeline_rate"] = measurement["rate"]
        if (
            abs(
                measurement["actual_frames"]
                - measurement["required_frames"]
            )
            > 1
        ):
            transition(detail, "awaiting_still_setting")
        else:
            transition(detail, "ready")
        return self.applications.update(validated["root"], detail)

    def _resume_detail(self, validated, project):
        detail = self.applications.latest(
            validated["root"],
            project["id"],
        )
        if detail is None:
            raise AdapterError("no Resolve attempt exists for this project")
        if detail.get("operation_token") is not None:
            self.applications.mark_interrupted_failed(
                validated["root"],
                detail["attempt_id"],
            )
            raise AdapterError("previous Resolve operation was interrupted")
        if detail["state"] in TERMINAL_STATES:
            raise AdapterError("terminal Resolve attempt cannot be resumed")
        if detail["state"] not in (
            "awaiting_in_out",
            "awaiting_still_setting",
            "ready",
        ):
            raise AdapterError(
                "Resolve attempt is not at a resumable checkpoint"
            )
        return detail

    def resume(self, job_dir):
        detail = None
        token = None
        root = None
        try:
            validated = load_validated_job(job_dir)
            root = validated["root"]
            project = self.gateway.current_project()
            detail = self._resume_detail(validated, project)
            token = next(self.operation_tokens)
            detail = self.applications.claim(
                root,
                detail["attempt_id"],
                token,
            )
            items = self.gateway.find_items(detail["bin"], detail["items"])
            if detail["state"] == "awaiting_in_out":
                detail["source_windows"] = self.gateway.read_source_windows(
                    detail["items"],
                    items,
                )
                has_photo = any(
                    item["kind"] == "photo" for item in detail["items"]
                )
                if has_photo:
                    transition(detail, "checking_still")
                else:
                    detail["timeline_rate"] = (
                        self.gateway.project_timeline_rate()
                    )
                    transition(detail, "ready")
                detail = self.applications.update(root, detail)
            elif detail["state"] == "awaiting_still_setting":
                detail["items"] = self.gateway.reimport_photos(
                    validated,
                    detail["bin"],
                    detail["items"],
                    detail["attempt_id"],
                )
                transition(detail, "checking_still")
                detail = self.applications.update(root, detail)
                items = self.gateway.find_items(
                    detail["bin"],
                    detail["items"],
                )
            if detail["state"] == "checking_still":
                detail = self._check_still(validated, detail, items)
        except AdapterError:
            if detail is not None and root is not None and token is not None:
                try:
                    self.applications.fail(
                        root,
                        detail["attempt_id"],
                        "Resolve resume failed",
                        token=token,
                    )
                except Exception:
                    pass
            raise
        except Exception as exc:
            if detail is not None and root is not None and token is not None:
                try:
                    self.applications.fail(
                        root,
                        detail["attempt_id"],
                        exc,
                        token=token,
                    )
                except Exception:
                    pass
            raise AdapterError("Resolve resume failed: {0}".format(exc))
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
        validated = load_validated_job(job_dir)
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

    def _confirmation_summary(self, validated, detail, proposed_name):
        plan = validated["plan"]
        materials = plan["materials"]
        counts = {"photo": 0, "video": 0}
        for material in materials:
            counts[material["kind"]] += 1
        points = plan["analysis"]["cut_points_ms"]
        intervals = [
            right - left for left, right in zip(points, points[1:])
        ]
        approximate = 0
        if intervals:
            approximate = int(
                round(sum(intervals) / float(len(intervals)))
            ) * plan["settings"]["every_n_resolved"]
        return {
            "bpm": plan["analysis"]["bpm"],
            "interval": plan["settings"]["every_n_resolved"],
            "approximate_cut_ms": approximate,
            "duration_ms": plan["analysis"]["duration_ms"],
            "material_counts": counts,
            "still": detail.get("still"),
            "timeline_name": proposed_name,
        }

    def apply_ready(self, job_dir, confirm):
        validated = load_validated_job(job_dir)
        root = validated["root"]
        project = self.gateway.current_project()
        detail = self.applications.latest(root, project["id"])
        if detail is None:
            raise AdapterError("no Resolve application exists for this project")
        if detail.get("operation_token") is not None:
            self.applications.mark_interrupted_failed(
                root,
                detail["attempt_id"],
            )
            raise AdapterError("previous Resolve operation was interrupted")
        if detail["state"] != "ready":
            raise AdapterError("Resolve attempt is not ready to apply")
        proposed = self.gateway.proposed_timeline_name(
            validated["plan"]["settings"]["timeline_name"]
        )
        summary = self._confirmation_summary(validated, detail, proposed)
        try:
            confirmed = bool(confirm(summary))
        except Exception as exc:
            raise AdapterError("confirmation failed: {0}".format(exc))
        if not confirmed:
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
            items = self.gateway.find_items(detail["bin"], detail["items"])
            timeline, timeline_detail = self.gateway.create_final_timeline(
                validated["plan"]["settings"]["timeline_name"]
            )
            detail["timeline"] = timeline_detail
            detail = self.applications.update(root, detail)
            detail["timeline_rate"] = self.gateway.verify_timeline_rate(
                timeline,
                detail["timeline_rate"],
            )
            detail = self.applications.update(root, detail)
            result = self.gateway.populate_timeline(
                timeline,
                items,
                validated,
                detail,
            )
            if (
                not result.get("bgm_placed")
                or not detail.get("timeline")
                or result.get("placed", 0) < 1
            ):
                raise AdapterError("final timeline is incomplete")
            detail["result"] = result
            transition(detail, "applied")
            detail = self.applications.update(root, detail)
        except Exception as exc:
            if claimed:
                try:
                    self.applications.fail(
                        root,
                        detail["attempt_id"],
                        exc,
                        token=token,
                    )
                except Exception:
                    pass
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
