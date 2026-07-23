import os

from minoru_studio_resolve.placement import (
    corrected_source_length,
    convert_cut_points,
    fit_steps,
    parse_rate,
    source_frames_available,
    source_window,
    timeline_to_source_frames,
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

    def import_media_sources(self, sources, bin_detail):
        media_pool = self._media_pool()
        folder = self.application_bin(bin_detail)
        if not media_pool.SetCurrentFolder(folder):
            raise GatewayError("cannot select Resolve application bin")
        expected = []
        keys = set()
        for source in sources:
            if not isinstance(source, dict):
                raise GatewayError("media source must be an object")
            key = str(source.get("key") or "").strip()
            path = str(source.get("path") or "").strip()
            kind = source.get("kind")
            if not key or not path:
                raise GatewayError("media source role is incomplete")
            if kind not in ("video", "audio"):
                raise GatewayError("media source kind must be video or audio")
            if key in keys:
                raise GatewayError("duplicate media source role")
            keys.add(key)
            expected.append({"key": key, "kind": kind, "path": path})
        if not expected:
            raise GatewayError("media placement requires sources")
        unique = []
        seen = set()
        for source in expected:
            normalized = _normalized_path(source["path"])
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append({"path": source["path"]})
        matched = self._match_imports(
            unique,
            self._import_group(
                media_pool,
                [value["path"] for value in unique],
            ),
        )
        by_path = {
            _normalized_path(value["path"]): item
            for value, item in matched
        }
        details = []
        for source in expected:
            item = by_path[_normalized_path(source["path"])]
            frames = item.GetClipProperty("Frames")
            try:
                frames = int(frames)
            except (TypeError, ValueError):
                frames = 0
            if frames <= 0:
                raise GatewayError(
                    "imported media frame count must be positive"
                )
            details.append(
                {
                    "key": source["key"],
                    "kind": source["kind"],
                    "id": _required_id(item.GetUniqueId(), "media item"),
                    "path": os.path.realpath(source["path"]),
                    "frames": frames,
                    "fps": str(item.GetClipProperty("FPS") or ""),
                }
            )
        return details

    def find_media_items(self, bin_detail, item_details):
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
            key = str(detail.get("key") or "")
            if not key or key in result:
                raise GatewayError("recorded media role is invalid")
            item_id = str(detail.get("id") or "")
            if item_id not in found:
                raise GatewayError("recorded media item is missing")
            result[key] = found[item_id]
        return result

    def place_media_timeline(self, timeline, items, validated):
        media_pool = self._media_pool()
        mode = validated.get("mode")
        if mode == "transcribe":
            audio_key = "source-audio"
        elif mode == "narrate":
            audio_key = "narration-audio"
        else:
            raise GatewayError("unsupported media placement mode")
        video_key = "source-video"
        record_frame = int(timeline.GetStartFrame())
        for key, media_type, label in (
            (video_key, 1, "source video"),
            (audio_key, 2, "placement audio"),
        ):
            item = items.get(key)
            if item is None:
                raise GatewayError(
                    "recorded {0} item is missing".format(label)
                )
            try:
                frames = int(item.GetClipProperty("Frames"))
            except (TypeError, ValueError):
                raise GatewayError(
                    "{0} frame count is invalid".format(label)
                )
            if frames <= 0:
                raise GatewayError(
                    "{0} frame count must be positive".format(label)
                )
            self._append(
                media_pool,
                timeline,
                {
                    "mediaPoolItem": item,
                    "startFrame": 0,
                    "endFrame": frames - 1,
                    "mediaType": media_type,
                    "trackIndex": 1,
                    "recordFrame": record_frame,
                },
                label,
            )
        return {
            "video_key": video_key,
            "audio_key": audio_key,
            "record_frame": record_frame,
        }

    def timeline_by_id(self, timeline_id):
        project = self._project()
        for index in range(1, int(project.GetTimelineCount()) + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline and str(timeline.GetUniqueId()) == str(timeline_id):
                return timeline
        raise GatewayError("recorded final timeline is missing")

    def subtitle_track_count(self, timeline):
        value = timeline.GetTrackCount("subtitle")
        try:
            count = int(value)
        except (TypeError, ValueError):
            raise GatewayError("subtitle track count is invalid")
        if count < 0:
            raise GatewayError("subtitle track count is invalid")
        return count

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

    def _timeline_names(self):
        project = self._project()
        names = set()
        for index in range(1, int(project.GetTimelineCount()) + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline:
                names.add(str(timeline.GetName() or ""))
        return names

    def proposed_timeline_name(self, requested):
        names = self._timeline_names()
        if requested not in names:
            return requested
        suffix = 2
        while True:
            candidate = "{0}-{1:03d}".format(requested, suffix)
            if candidate not in names:
                return candidate
            suffix += 1

    def create_final_timeline(self, requested):
        project = self._project()
        media_pool = self._media_pool()
        name = self.proposed_timeline_name(requested)
        timeline = media_pool.CreateEmptyTimeline(name)
        if not timeline:
            raise GatewayError("cannot create final timeline")
        if not project.SetCurrentTimeline(timeline):
            raise GatewayError("cannot activate final timeline")
        return timeline, {
            "id": _required_id(timeline.GetUniqueId(), "final timeline"),
            "name": str(timeline.GetName() or name),
        }

    def verify_timeline_rate(self, timeline, expected):
        try:
            actual = parse_rate(timeline.GetSetting("timelineFrameRate"))
        except ValueError as exc:
            raise GatewayError("final timeline rate is invalid: {0}".format(exc))
        wanted = (int(expected["numerator"]), int(expected["denominator"]))
        if actual != wanted:
            raise GatewayError("final timeline frame rate changed")
        return {"numerator": actual[0], "denominator": actual[1]}

    def _append(self, media_pool, timeline, descriptor, label):
        result = media_pool.AppendToTimeline([descriptor])
        if not isinstance(result, list) or not result:
            raise GatewayError("cannot place {0}".format(label))
        item = result[0]
        try:
            duration = int(item.GetDuration())
        except (TypeError, ValueError):
            raise GatewayError("{0} duration is invalid".format(label))
        if duration <= 0:
            raise GatewayError("{0} duration must be positive".format(label))
        return item, duration

    def _window_values(self, detail):
        return (
            detail["mark_in_frame"],
            detail["mark_out_frame_exclusive"],
        ), (
            detail["source_rate"]["numerator"],
            detail["source_rate"]["denominator"],
        )

    def populate_timeline(self, timeline, items, validated, detail):
        media_pool = self._media_pool()
        rate = (
            detail["timeline_rate"]["numerator"],
            detail["timeline_rate"]["denominator"],
        )
        try:
            points = convert_cut_points(
                validated["plan"]["analysis"]["cut_points_ms"],
                rate,
            )
        except ValueError as exc:
            raise GatewayError("cannot convert final cut points: {0}".format(exc))
        base = int(timeline.GetStartFrame())
        audio = items.get(validated["plan"]["audio_input_index"])
        if audio is None:
            raise GatewayError("recorded BGM item is missing")
        self._append(
            media_pool,
            timeline,
            {
                "mediaPoolItem": audio,
                "startFrame": 0,
                "endFrame": points[-1] - 1,
                "mediaType": 2,
                "trackIndex": 1,
                "recordFrame": base,
            },
            "BGM",
        )

        materials = sorted(
            validated["plan"]["materials"],
            key=lambda value: value["order_index"],
        )
        if not materials:
            raise GatewayError("placement requires visual materials")
        windows = {
            value["input_index"]: value
            for value in detail.get("source_windows", [])
        }
        video_cursors = {
            input_index: window["mark_in_frame"]
            for input_index, window in windows.items()
        }
        usage = {str(value["input_index"]): 0 for value in materials}
        gaps = []
        mismatches = []
        corrections = 0
        placed = 0
        material_cursor = 0
        cut_index = 0
        every_n = validated["plan"]["settings"]["every_n_resolved"]
        custom_data = "minoru-studio:{0}:{1}".format(
            validated["manifest"]["job_id"],
            detail["attempt_id"],
        )

        while cut_index < len(points) - 1:
            requested = min(every_n, len(points) - 1 - cut_index)
            selected = None
            for offset in range(len(materials)):
                position = (material_cursor + offset) % len(materials)
                material = materials[position]
                input_index = material["input_index"]
                if material["kind"] == "photo":
                    available = detail["still"]["actual_frames"]
                    steps = fit_steps(
                        points,
                        cut_index,
                        requested,
                        available,
                    )
                    if steps:
                        target = points[cut_index + steps] - points[cut_index]
                        selected = {
                            "material": material,
                            "position": position,
                            "steps": steps,
                            "source_start": 0,
                            "source_frames": target,
                            "source_rate": rate,
                        }
                        break
                else:
                    window_detail = windows.get(input_index)
                    if window_detail is None:
                        raise GatewayError("video source window is missing")
                    window, source_rate = self._window_values(window_detail)
                    cursor = video_cursors[input_index]
                    requested_target = (
                        points[cut_index + requested] - points[cut_index]
                    )
                    requested_source = timeline_to_source_frames(
                        requested_target,
                        rate,
                        source_rate,
                    )
                    if cursor + requested_source > window[1]:
                        cursor = window[0]
                    available = source_frames_available(
                        (cursor, window[1]),
                        source_rate,
                        rate,
                    )
                    steps = fit_steps(
                        points,
                        cut_index,
                        requested,
                        available,
                    )
                    if steps:
                        target = points[cut_index + steps] - points[cut_index]
                        source_frames = timeline_to_source_frames(
                            target,
                            rate,
                            source_rate,
                        )
                        if cursor + source_frames <= window[1]:
                            selected = {
                                "material": material,
                                "position": position,
                                "steps": steps,
                                "source_start": cursor,
                                "source_frames": source_frames,
                                "source_rate": source_rate,
                            }
                            break
            if selected is None:
                gaps.append(
                    {
                        "cut_index": cut_index,
                        "start_frame": points[cut_index],
                    }
                )
                cut_index += 1
                continue

            material = selected["material"]
            input_index = material["input_index"]
            media_item = items.get(input_index)
            if media_item is None:
                raise GatewayError("recorded visual item is missing")
            target = (
                points[cut_index + selected["steps"]] - points[cut_index]
            )
            source_frames = selected["source_frames"]
            descriptor = {
                "mediaPoolItem": media_item,
                "startFrame": selected["source_start"],
                "endFrame": selected["source_start"] + source_frames - 1,
                "mediaType": 1,
                "trackIndex": 1,
                "recordFrame": base + points[cut_index],
            }
            timeline_item, actual = self._append(
                media_pool,
                timeline,
                descriptor,
                "visual",
            )
            if abs(actual - target) > 1:
                if material["kind"] == "video":
                    if not timeline.DeleteClips([timeline_item], False):
                        raise GatewayError("cannot delete mismatched video clip")
                    corrected = corrected_source_length(
                        source_frames,
                        target,
                        actual,
                        rate,
                        selected["source_rate"],
                    )
                    window, unused_rate = self._window_values(windows[input_index])
                    if selected["source_start"] + corrected > window[1]:
                        corrected = window[1] - selected["source_start"]
                    if corrected <= 0:
                        raise GatewayError("corrected video window is empty")
                    descriptor["endFrame"] = (
                        selected["source_start"] + corrected - 1
                    )
                    timeline_item, actual = self._append(
                        media_pool,
                        timeline,
                        descriptor,
                        "corrected visual",
                    )
                    source_frames = corrected
                else:
                    if not timeline.DeleteClips([timeline_item], False):
                        raise GatewayError("cannot delete mismatched still clip")
                    timeline_item, actual = self._append(
                        media_pool,
                        timeline,
                        descriptor,
                        "corrected visual",
                    )
                corrections += 1
            if abs(actual - target) > 1:
                mismatches.append(
                    {
                        "input_index": input_index,
                        "cut_index": cut_index,
                        "target_frames": target,
                        "actual_frames": actual,
                    }
                )
            if not timeline.AddMarker(
                points[cut_index],
                "Blue",
                "beat",
                "",
                1,
                custom_data,
            ):
                raise GatewayError("cannot add beat marker")
            if material["kind"] == "video":
                window, unused_rate = self._window_values(windows[input_index])
                next_cursor = selected["source_start"] + source_frames
                video_cursors[input_index] = (
                    window[0] if next_cursor >= window[1] else next_cursor
                )
            usage[str(input_index)] += 1
            placed += 1
            material_cursor = (selected["position"] + 1) % len(materials)
            cut_index += selected["steps"]

        return {
            "bgm_placed": True,
            "placed": placed,
            "failed": 0,
            "gaps": len(gaps),
            "gap_details": gaps,
            "corrections": corrections,
            "per_input_usage": usage,
            "unused_inputs": [
                int(input_index)
                for input_index, count in usage.items()
                if count == 0
            ],
            "mismatches": mismatches,
        }
