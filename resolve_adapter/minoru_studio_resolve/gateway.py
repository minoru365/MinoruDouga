import os


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
        for item in (folder.GetClipList() or []):
            item_id = _required_id(item.GetUniqueId(), "media item")
            if item_id in found:
                raise GatewayError("duplicate media item ID in application bin")
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
