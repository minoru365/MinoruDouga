import os

from minoru_studio_resolve.placement import (
    convert_cut_points,
    parse_rate,
    source_window,
    typical_still_target,
)


class GatewayError(RuntimeError):
    pass


def _normalized_path(path):
    return os.path.normcase(os.path.realpath(str(path)))


def _required_id(value, label):
    if value is None or value is False or str(value) == "":
        raise GatewayError("Resolve {0} has no stable ID".format(label))
    return str(value)


class ResolveGateway(object):
    def __init__(self, resolve):
        self.resolve = resolve

    def _project(self):
        manager = self.resolve.GetProjectManager()
        if not manager:
            raise GatewayError("Resolve project manager is unavailable")
        project = manager.GetCurrentProject()
        if not project:
            raise GatewayError("open a Resolve project first")
        return project

    def current_project(self):
        project = self._project()
        return {
            "id": _required_id(project.GetUniqueId(), "project"),
            "name": str(project.GetName() or ""),
        }

    def product_info(self):
        return {
            "product": str(self.resolve.GetProductName() or ""),
            "version": str(self.resolve.GetVersionString() or ""),
        }

    def _media_pool(self):
        media_pool = self._project().GetMediaPool()
        if not media_pool:
            raise GatewayError("Resolve media pool is unavailable")
        return media_pool

    def _walk_folders(self, folder):
        yield folder
        children = folder.GetSubFolderList() or []
        for child in children:
            for nested in self._walk_folders(child):
                yield nested

    def _folder_by_id(self, folder_id):
        media_pool = self._media_pool()
        root = media_pool.GetRootFolder()
        for folder in self._walk_folders(root):
            if str(folder.GetUniqueId()) == str(folder_id):
                return folder
        raise GatewayError("recorded Resolve bin is missing")

    def create_application_bin(self, timeline_name, attempt_id):
        media_pool = self._media_pool()
        root = media_pool.GetRootFolder()
        if not root:
            raise GatewayError("Resolve media-pool root is unavailable")
        names = set(
            str(folder.GetName())
            for folder in (root.GetSubFolderList() or [])
        )
        base = "_MinoruStudio {0} {1}".format(timeline_name, attempt_id)
        name = base
        suffix = 2
        while name in names:
            name = "{0}-{1:03d}".format(base, suffix)
            suffix += 1
        folder = media_pool.AddSubFolder(root, name)
        if not folder or not media_pool.SetCurrentFolder(folder):
            raise GatewayError("cannot create Resolve application bin")
        return {
            "id": _required_id(folder.GetUniqueId(), "bin"),
            "name": str(folder.GetName() or name),
        }

    def application_bin(self, bin_detail):
        if not isinstance(bin_detail, dict) or not bin_detail.get("id"):
            raise GatewayError("application bin identity is missing")
        return self._folder_by_id(bin_detail["id"])

    def _import_group(self, media_pool, paths):
        if not paths:
            return []
        returned = media_pool.ImportMedia(list(paths))
        if not returned:
            raise GatewayError("Resolve did not import requested media")
        return list(returned)

    def _match_imports(self, expected, returned):
        expected_by_path = {}
        for value in expected:
            key = _normalized_path(value["path"])
            if key in expected_by_path:
                raise GatewayError("duplicate expected media path")
            expected_by_path[key] = value
        matched = {}
        for item in returned:
            key = _normalized_path(item.GetClipProperty("File Path"))
            if key not in expected_by_path:
                raise GatewayError("Resolve returned unexpected imported media")
            if key in matched:
                raise GatewayError("Resolve returned ambiguous imported media")
            matched[key] = item
        if set(matched) != set(expected_by_path):
            raise GatewayError("Resolve did not return every imported input")
        return [
            (value, matched[_normalized_path(value["path"])])
            for value in expected
        ]

    def _item_detail(self, expected, item):
        frames = item.GetClipProperty("Frames")
        try:
            frames = int(frames)
        except (TypeError, ValueError):
            frames = 0
        return {
            "input_index": expected["input_index"],
            "kind": expected["kind"],
            "id": _required_id(item.GetUniqueId(), "media item"),
            "path": os.path.realpath(expected["path"]),
            "frames": frames,
            "fps": str(item.GetClipProperty("FPS") or ""),
        }

    def import_inputs(self, validated, bin_detail):
        media_pool = self._media_pool()
        folder = self.application_bin(bin_detail)
        if not media_pool.SetCurrentFolder(folder):
            raise GatewayError("cannot select Resolve application bin")

        kinds = {0: "audio"}
        for material in validated["plan"]["materials"]:
            kinds[material["input_index"]] = material["kind"]
        expected = []
        for index, value in enumerate(validated["inputs"]):
            if index not in kinds:
                raise GatewayError("plan does not classify every input")
            expected.append(
                {
                    "input_index": index,
                    "kind": kinds[index],
                    "path": value["path"],
                }
            )

        returned = []
        videos = [value for value in expected if value["kind"] == "video"]
        if videos:
            returned.extend(self._match_imports(
                videos,
                self._import_group(media_pool, [item["path"] for item in videos]),
            ))
        photos = [value for value in expected if value["kind"] == "photo"]
        for photo in photos:
            returned.extend(self._match_imports(
                [photo],
                self._import_group(media_pool, [photo["path"]]),
            ))
        audio = [value for value in expected if value["kind"] == "audio"]
        if len(audio) != 1:
            raise GatewayError("job must contain exactly one BGM input")
        audio_returned = self._import_group(media_pool, [audio[0]["path"]])
        if len(audio_returned) != 1:
            raise GatewayError("Resolve must return exactly one BGM item")
        returned.extend(self._match_imports(audio, audio_returned))

        details = [self._item_detail(value, item) for value, item in returned]
        if len(details) != len(expected):
            raise GatewayError("Resolve import count mismatch")
        return sorted(details, key=lambda value: value["input_index"])

    def find_items(self, bin_detail, item_details):
        folder = self.application_bin(bin_detail)
        found = {}
        for nested in self._walk_folders(folder):
            for item in (nested.GetClipList() or []):
                item_id = _required_id(item.GetUniqueId(), "media item")
                if item_id in found:
                    raise GatewayError(
                        "duplicate media item ID in application bin"
                    )
                found[item_id] = item
        result = {}
        for detail in item_details:
            item_id = str(detail.get("id", ""))
            if item_id not in found:
                raise GatewayError("recorded media item is missing")
            result[detail["input_index"]] = found[item_id]
        if len(result) != len(item_details):
            raise GatewayError("recorded media item identity is ambiguous")
        return result

    def read_source_windows(self, item_details, items):
        windows = []
        for detail in item_details:
            if detail["kind"] != "video":
                continue
            item = items.get(detail["input_index"])
            if item is None:
                raise GatewayError("recorded video item is missing")
            try:
                frames = int(item.GetClipProperty("Frames"))
            except (TypeError, ValueError):
                raise GatewayError("video frame count is invalid")
            if frames <= 0:
                raise GatewayError("video frame count must be positive")
            try:
                rate = parse_rate(item.GetClipProperty("FPS"))
            except ValueError as exc:
                raise GatewayError("video FPS is invalid: {0}".format(exc))
            marks = item.GetMarkInOut() or {}
            selected = marks.get("video") or marks.get("audio") or {}
            try:
                mark_in = int(selected.get("in", 0))
                mark_out = int(selected.get("out", frames - 1))
            except (TypeError, ValueError):
                raise GatewayError("video In/Out marks are invalid")
            if not 0 <= mark_in <= mark_out < frames:
                raise GatewayError("video In/Out window is empty or out of range")
            start, end = source_window(marks, frames)
            if start >= end:
                raise GatewayError("video In/Out window is empty")
            windows.append(
                {
                    "input_index": detail["input_index"],
                    "total_frames": frames,
                    "source_rate": {
                        "numerator": rate[0],
                        "denominator": rate[1],
                    },
                    "mark_in_frame": start,
                    "mark_out_frame_exclusive": end,
                }
            )
        return windows

    def project_timeline_rate(self):
        project = self._project()
        timeline = project.GetCurrentTimeline()
        value = ""
        if timeline:
            value = timeline.GetSetting("timelineFrameRate")
        if not value:
            value = project.GetSetting("timelineFrameRate")
        try:
            rate = parse_rate(value)
        except ValueError as exc:
            raise GatewayError("timeline frame rate is invalid: {0}".format(exc))
        return {"numerator": rate[0], "denominator": rate[1]}

    def probe_still(self, photo_item, cut_points_ms, every_n, attempt_id):
        project = self._project()
        media_pool = self._media_pool()
        previous = project.GetCurrentTimeline()
        probe = media_pool.CreateEmptyTimeline(
            "_MinoruStudio Probe {0}".format(attempt_id)
        )
        if not probe:
            raise GatewayError("cannot create still probe timeline")
        if not project.SetCurrentTimeline(probe):
            raise GatewayError("cannot activate still probe timeline")
        probe_id = _required_id(probe.GetUniqueId(), "probe timeline")
        actual = None
        try:
            value = (
                probe.GetSetting("timelineFrameRate")
                or project.GetSetting("timelineFrameRate")
            )
            try:
                rate = parse_rate(value)
                points = convert_cut_points(cut_points_ms, rate)
                required = typical_still_target(points, every_n)
            except ValueError as exc:
                raise GatewayError("cannot calculate still probe: {0}".format(exc))
            base = int(probe.GetStartFrame())
            result = media_pool.AppendToTimeline(
                [
                    {
                        "mediaPoolItem": photo_item,
                        "startFrame": 0,
                        "endFrame": required * 2 - 1,
                        "mediaType": 1,
                        "trackIndex": 1,
                        "recordFrame": base,
                    }
                ]
            )
            if not isinstance(result, list) or not result:
                raise GatewayError("cannot place still probe")
            actual = int(result[0].GetDuration())
            if not probe.DeleteClips([result[0]], False):
                raise GatewayError("cannot delete still probe clip")
        finally:
            try:
                if not media_pool.DeleteTimelines([probe]):
                    raise GatewayError(
                        "cannot delete probe timeline {0} ({1})".format(
                            probe_id,
                            probe.GetName(),
                        )
                    )
            finally:
                if previous is not None and not project.SetCurrentTimeline(previous):
                    raise GatewayError(
                        "cannot restore timeline after still probe"
                    )
        return {
            "rate": {"numerator": rate[0], "denominator": rate[1]},
            "required_frames": required,
            "actual_frames": actual,
        }

    def reimport_photos(self, validated, bin_detail, item_details, attempt_id):
        media_pool = self._media_pool()
        parent = self.application_bin(bin_detail)
        existing_names = set(
            str(folder.GetName())
            for folder in (parent.GetSubFolderList() or [])
        )
        base = "_MinoruStudio Photo Retry {0}".format(attempt_id)
        name = base
        suffix = 2
        while name in existing_names:
            name = "{0}-{1:03d}".format(base, suffix)
            suffix += 1
        folder = media_pool.AddSubFolder(parent, name)
        if not folder or not media_pool.SetCurrentFolder(folder):
            raise GatewayError("cannot create photo retry bin")

        photos = []
        for detail in item_details:
            if detail["kind"] != "photo":
                continue
            photos.append(
                {
                    "input_index": detail["input_index"],
                    "kind": "photo",
                    "path": validated["inputs"][detail["input_index"]]["path"],
                }
            )
        replacements = {}
        for photo in photos:
            pairs = self._match_imports(
                [photo],
                self._import_group(media_pool, [photo["path"]]),
            )
            replacements[photo["input_index"]] = self._item_detail(
                pairs[0][0],
                pairs[0][1],
            )
        result = []
        for detail in item_details:
            result.append(replacements.get(detail["input_index"], detail))
        return result
