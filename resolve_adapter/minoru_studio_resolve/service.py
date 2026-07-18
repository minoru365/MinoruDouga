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
