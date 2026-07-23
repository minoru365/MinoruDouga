import hashlib
import json
import math
import os


class ContractError(ValueError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def contained_path(root, relative):
    root = os.path.realpath(root)
    candidate = os.path.realpath(
        os.path.join(root, relative.replace("/", os.sep))
    )
    try:
        common = os.path.commonpath([root, candidate])
    except ValueError:
        raise ContractError("artifact path escapes job")
    if os.path.normcase(common) != os.path.normcase(root):
        raise ContractError("artifact path escapes job")
    return candidate


def _read_object(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ContractError("cannot read JSON: {0}".format(exc))
    if not isinstance(value, dict):
        raise ContractError("JSON root must be an object")
    return value


def _integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("{0} must be integer".format(name))
    return value


def _increasing(values, name):
    if any(left >= right for left, right in zip(values, values[1:])):
        raise ContractError("{0} must be strictly increasing".format(name))


def _validate_plan(plan, input_count):
    if _integer(plan.get("schema_version"), "schema_version") != 1:
        raise ContractError("unsupported plan schema")
    if plan.get("mode") != "beat-sync":
        raise ContractError("plan mode must be beat-sync")
    if not isinstance(plan.get("job_id"), str) or not plan["job_id"].strip():
        raise ContractError("plan job_id must be non-empty")
    if _integer(plan.get("audio_input_index"), "audio_input_index") != 0:
        raise ContractError("audio_input_index must be 0")

    analysis = plan.get("analysis")
    settings = plan.get("settings")
    materials = plan.get("materials")
    if not isinstance(analysis, dict):
        raise ContractError("analysis must be an object")
    if not isinstance(settings, dict):
        raise ContractError("settings must be an object")
    if not isinstance(materials, list) or not materials:
        raise ContractError("materials must be a non-empty list")

    duration_ms = _integer(analysis.get("duration_ms"), "duration_ms")
    minimum_cut_ms = _integer(
        analysis.get("minimum_cut_ms"),
        "minimum_cut_ms",
    )
    beats_ms = analysis.get("beats_ms")
    cut_points_ms = analysis.get("cut_points_ms")
    if not isinstance(beats_ms, list):
        raise ContractError("beats_ms must be a list")
    if not isinstance(cut_points_ms, list):
        raise ContractError("cut_points_ms must be a list")
    beats_ms = [_integer(value, "beats_ms") for value in beats_ms]
    cut_points_ms = [
        _integer(value, "cut_points_ms") for value in cut_points_ms
    ]
    if duration_ms <= 0 or minimum_cut_ms <= 0:
        raise ContractError("duration and minimum cut must be positive")
    bpm = analysis.get("bpm")
    if (
        isinstance(bpm, bool)
        or not isinstance(bpm, (int, float))
        or not math.isfinite(bpm)
        or bpm <= 0
    ):
        raise ContractError("bpm must be finite and positive")
    _increasing(beats_ms, "beats_ms")
    _increasing(cut_points_ms, "cut_points_ms")
    if len(beats_ms) < 2:
        raise ContractError("beats_ms must contain at least two beats")
    if len(cut_points_ms) < 4:
        raise ContractError("cut_points_ms must contain two internal points")
    if cut_points_ms[0] != 0 or cut_points_ms[-1] != duration_ms:
        raise ContractError("cut_points_ms must span the audio duration")
    if any(
        right - left < minimum_cut_ms
        for left, right in zip(cut_points_ms, cut_points_ms[1:])
    ):
        raise ContractError("cut_points_ms violate minimum_cut_ms")

    requested = settings.get("every_n_requested")
    if requested != "auto":
        requested = _integer(requested, "every_n_requested")
        if not 1 <= requested <= 16:
            raise ContractError("every_n_requested must be auto or 1..16")
    resolved = _integer(settings.get("every_n_resolved"), "every_n_resolved")
    if not 1 <= resolved <= 16:
        raise ContractError("every_n_resolved must be 1..16")
    if settings.get("order_mode") not in ("asc", "random"):
        raise ContractError("order_mode must be asc or random")
    timeline_name = settings.get("timeline_name")
    if not isinstance(timeline_name, str) or not timeline_name.strip():
        raise ContractError("timeline_name must be non-blank")

    input_indices = []
    order_indices = []
    for material in materials:
        if not isinstance(material, dict):
            raise ContractError("material must be an object")
        input_index = _integer(material.get("input_index"), "input_index")
        order_index = _integer(material.get("order_index"), "order_index")
        if not 1 <= input_index < input_count:
            raise ContractError("material input_index is out of range")
        if material.get("kind") not in ("photo", "video"):
            raise ContractError("material kind must be photo or video")
        input_indices.append(input_index)
        order_indices.append(order_index)
    if len(set(input_indices)) != len(input_indices):
        raise ContractError("material input_index values must be unique")
    if order_indices != list(range(len(materials))):
        raise ContractError("material order_index values must be contiguous")


_RESOLVE_VIDEO_SUFFIXES = frozenset(
    (".avi", ".mkv", ".mov", ".mp4", ".mxf", ".webm")
)


def _regular_file(path, label):
    try:
        stat = os.stat(path)
    except OSError as exc:
        raise ContractError("{0} is missing: {1}".format(label, exc))
    if not os.path.isfile(path):
        raise ContractError("{0} must be a regular file".format(label))
    return stat


def _validated_inputs(manifest):
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ContractError("job inputs are missing")
    for expected in inputs:
        if not isinstance(expected, dict):
            raise ContractError("job input must be an object")
        try:
            path = os.path.realpath(expected["path"])
            stat = _regular_file(path, "input")
            if (
                stat.st_size != expected["size"]
                or stat.st_mtime_ns != expected["mtime_ns"]
                or sha256_file(path) != expected["sha256"]
            ):
                raise ContractError("input changed: {0}".format(path))
        except KeyError as exc:
            raise ContractError("input is incomplete: {0}".format(exc))
    return inputs


def _required_artifact(root, artifacts, kind, relative):
    matches = [
        item
        for item in artifacts
        if isinstance(item, dict) and item.get("kind") == kind
    ]
    if len(matches) != 1:
        raise ContractError("job must contain one {0}".format(kind))
    record = matches[0]
    try:
        path = contained_path(root, record["path"])
        if os.path.normcase(os.path.relpath(path, root)) != os.path.normcase(
            relative.replace("/", os.sep)
        ):
            raise ContractError("{0} path is invalid".format(kind))
        stat = _regular_file(path, kind)
        if stat.st_size != record["size"] or sha256_file(path) != record["sha256"]:
            raise ContractError("{0} fingerprint mismatch".format(kind))
    except KeyError as exc:
        raise ContractError("{0} record is incomplete: {1}".format(kind, exc))
    return {"path": path, "size": stat.st_size, "sha256": record["sha256"]}


def load_validated_media_job(job_dir):
    root = os.path.realpath(job_dir)
    manifest = _read_object(os.path.join(root, "job.json"))
    if manifest.get("schema_version") != 1 or manifest.get("status") != "succeeded":
        raise ContractError("job is not successful")
    inputs = _validated_inputs(manifest)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ContractError("job artifacts must be a list")
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ContractError("job name must be non-empty")
    if manifest.get("mode") == "transcribe" and len(inputs) == 1:
        video = inputs[0]["path"]
        audio_key = "source-audio"
        audio_path = video
        required = (
            ("transcript-txt", "outputs/transcript.txt"),
            ("subtitles-srt", "outputs/subtitles.srt"),
            ("subtitles-vtt", "outputs/subtitles.vtt"),
        )
    elif manifest.get("mode") == "narrate" and len(inputs) == 2:
        video = inputs[0]["path"]
        audio_key = "narration-audio"
        audio_path = None
        required = (
            ("narration-wav", "outputs/narration.wav"),
            ("subtitles-srt", "outputs/subtitles.srt"),
            ("subtitles-vtt", "outputs/subtitles.vtt"),
        )
    else:
        raise ContractError("job mode is not supported by Resolve media placement")
    if os.path.splitext(video)[1].lower() not in _RESOLVE_VIDEO_SUFFIXES:
        raise ContractError("source input is not a Resolve-readable video")
    verified = {
        kind: _required_artifact(root, artifacts, kind, relative)
        for kind, relative in required
    }
    if audio_path is None:
        audio_path = verified["narration-wav"]["path"]
    return {
        "root": root,
        "manifest": manifest,
        "mode": manifest["mode"],
        "timeline_name": "{0} Resolve".format(name),
        "sources": [
            {"key": "source-video", "kind": "video", "path": video},
            {"key": audio_key, "kind": "audio", "path": audio_path},
        ],
        "subtitle": verified["subtitles-srt"],
    }


def load_validated_job(job_dir):
    root = os.path.realpath(job_dir)
    manifest = _read_object(os.path.join(root, "job.json"))
    if manifest.get("schema_version") != 1:
        raise ContractError("unsupported job schema")
    if (
        manifest.get("mode") != "beat-sync"
        or manifest.get("status") != "succeeded"
    ):
        raise ContractError("job is not a successful beat-sync job")
    artifact_values = manifest.get("artifacts", [])
    if not isinstance(artifact_values, list):
        raise ContractError("job artifacts must be a list")
    artifacts = [
        item
        for item in artifact_values
        if isinstance(item, dict) and item.get("kind") == "beat-sync-plan"
    ]
    if len(artifacts) != 1:
        raise ContractError("job must contain one beat-sync plan")
    artifact = artifacts[0]
    try:
        plan_path = contained_path(root, artifact["path"])
        stat = os.stat(plan_path)
        expected_size = artifact["size"]
        expected_hash = artifact["sha256"]
    except (KeyError, OSError, TypeError) as exc:
        raise ContractError("cannot read beat-sync plan: {0}".format(exc))
    if stat.st_size != expected_size or sha256_file(plan_path) != expected_hash:
        raise ContractError("beat-sync plan fingerprint mismatch")
    plan = _read_object(plan_path)
    if plan.get("job_id") != manifest.get("job_id"):
        raise ContractError("plan job_id mismatch")
    inputs = manifest.get("inputs", [])
    if not isinstance(inputs, list) or not inputs:
        raise ContractError("job inputs are missing")
    for expected in inputs:
        if not isinstance(expected, dict):
            raise ContractError("job input must be an object")
        try:
            path = os.path.realpath(expected["path"])
            current = os.stat(path)
            expected_size = expected["size"]
            expected_mtime = expected["mtime_ns"]
            expected_hash = expected["sha256"]
        except (KeyError, OSError, TypeError) as exc:
            raise ContractError("input missing: {0}".format(exc))
        if (
            current.st_size != expected_size
            or current.st_mtime_ns != expected_mtime
            or sha256_file(path) != expected_hash
        ):
            raise ContractError("input changed: {0}".format(path))
    _validate_plan(plan, len(inputs))
    return {
        "root": root,
        "manifest": manifest,
        "plan": plan,
        "inputs": inputs,
    }
