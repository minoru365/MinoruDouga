import os


PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".aac", ".flac")


class FakeMediaPoolItem:
    def __init__(self, item_id, path, kind, frames=120, fps="30"):
        self.item_id = item_id
        self.path = os.path.realpath(path)
        self.kind = kind
        self.frames = frames
        self.fps = fps
        self.marks = {}

    def GetUniqueId(self):
        return self.item_id

    def GetName(self):
        return os.path.basename(self.path)

    def GetClipProperty(self, name=None):
        values = {
            "File Path": self.path,
            "Frames": str(self.frames),
            "FPS": str(self.fps),
        }
        if name is None:
            return values
        return values.get(name, "")

    def GetMarkInOut(self):
        return self.marks


class FakeFolder:
    def __init__(self, folder_id, name):
        self.folder_id = folder_id
        self.name = name
        self.clips = []
        self.subfolders = []

    def GetUniqueId(self):
        return self.folder_id

    def GetName(self):
        return self.name

    def GetClipList(self):
        return list(self.clips)

    def GetSubFolderList(self):
        return list(self.subfolders)


class FakeTimelineItem:
    def __init__(
        self,
        item_id,
        media_pool_item,
        duration,
        record_frame=0,
        media_type=1,
        track_index=1,
    ):
        self.item_id = item_id
        self.media_pool_item = media_pool_item
        self.duration = duration
        self.record_frame = record_frame
        self.media_type = media_type
        self.track_index = track_index

    def GetUniqueId(self):
        return self.item_id

    def GetDuration(self):
        return self.duration


class FakeTimeline:
    def __init__(self, timeline_id, name, rate="30"):
        self.timeline_id = timeline_id
        self.name = name
        self.rate = rate
        self.items = []
        self.markers = []
        self.audio_track_items = {1: []}
        self.video_track_items = {1: []}
        self.subtitle_track_count = 0

    def GetUniqueId(self):
        return self.timeline_id

    def AddTrack(self, track_type):
        if track_type != "subtitle":
            return False
        self.subtitle_track_count += 1
        return True

    def GetTrackCount(self, track_type):
        if track_type == "subtitle":
            return self.subtitle_track_count
        if track_type == "video":
            return len(self.video_track_items)
        if track_type == "audio":
            return len(self.audio_track_items)
        return 0

    def GetName(self):
        return self.name

    def GetSetting(self, name):
        if name == "timelineFrameRate":
            return self.rate
        return ""

    def GetStartFrame(self):
        return 0

    def DeleteClips(self, items, ripple=False):
        for item in items:
            if item in self.items:
                self.items.remove(item)
            tracks = (
                self.audio_track_items
                if item.media_type == 2
                else self.video_track_items
            )
            if item in tracks.get(item.track_index, []):
                tracks[item.track_index].remove(item)
        return True

    def AddMarker(self, frame_id, color, name, note, duration, custom_data):
        self.markers.append(
            {
                "frame": frame_id,
                "color": color,
                "name": name,
                "note": note,
                "duration": duration,
                "custom_data": custom_data,
            }
        )
        return True


class FakeMediaPool:
    def __init__(
        self,
        project,
        still_duration=None,
        lag_still_duration=False,
        audio_frames=120,
        video_frames=120,
    ):
        self.project = project
        self.still_duration = still_duration
        self.lag_still_duration = lag_still_duration
        self.audio_frames = audio_frames
        self.video_frames = video_frames
        self._pending_still_duration = None
        self.root = FakeFolder("root", "Master")
        self.current_folder = self.root
        self.import_calls = []
        self.photo_import_batch_sizes = []
        self.imported_items = []
        self.video_items = []
        self.video_import_calls = 0
        self.deleted_timeline_ids = []
        self.fail_visual_append_after = None
        self._folder_number = 0
        self._item_number = 0
        self._timeline_number = 0
        self._timeline_item_number = 0
        self._visual_appends = 0

    def GetRootFolder(self):
        return self.root

    def AddSubFolder(self, parent, name):
        self._folder_number += 1
        folder = FakeFolder("folder-{0}".format(self._folder_number), name)
        parent.subfolders.append(folder)
        return folder

    def SetCurrentFolder(self, folder):
        self.current_folder = folder
        return True

    def ImportMedia(self, paths):
        paths = list(paths)
        self.import_calls.append(paths)
        kinds = []
        for path in paths:
            suffix = os.path.splitext(path)[1].lower()
            if suffix in PHOTO_SUFFIXES:
                kinds.append("photo")
            elif suffix in AUDIO_SUFFIXES:
                kinds.append("audio")
            else:
                kinds.append("video")
        if kinds and all(kind == "photo" for kind in kinds):
            self.photo_import_batch_sizes.append(len(paths))
        if kinds and all(kind == "video" for kind in kinds):
            self.video_import_calls += 1
        result = []
        for path, kind in zip(paths, kinds):
            self._item_number += 1
            if kind == "audio":
                frames = self.audio_frames
            elif kind == "video":
                frames = self.video_frames
            else:
                frames = 120
            item = FakeMediaPoolItem(
                "item-{0}".format(self._item_number),
                path,
                kind,
                frames=frames,
            )
            self.current_folder.clips.append(item)
            self.imported_items.append(item)
            if kind == "video":
                self.video_items.append(item)
            result.append(item)
        return result

    def CreateEmptyTimeline(self, name):
        self._timeline_number += 1
        if name.startswith("_MinoruStudio Probe "):
            timeline_id = "probe-" + name[len("_MinoruStudio Probe "):]
        else:
            timeline_id = "timeline-{0}".format(self._timeline_number)
        timeline = FakeTimeline(
            timeline_id,
            name,
            self.project.timeline_rate,
        )
        self.project.timelines.append(timeline)
        self.project.current_timeline = timeline
        return timeline

    def DeleteTimelines(self, timelines):
        for timeline in timelines:
            self.deleted_timeline_ids.append(timeline.GetUniqueId())
            if timeline in self.project.timelines:
                self.project.timelines.remove(timeline)
        return True

    def AppendToTimeline(self, clips):
        result = []
        for clip in clips:
            item = clip["mediaPoolItem"]
            media_type = clip.get("mediaType", 1)
            is_probe = self.project.current_timeline.GetName().startswith(
                "_MinoruStudio Probe "
            )
            if media_type == 1 and not is_probe:
                self._visual_appends += 1
                if (
                    self.fail_visual_append_after is not None
                    and self._visual_appends > self.fail_visual_append_after
                ):
                    return False
            start = int(clip.get("startFrame", 0))
            if "endFrame" in clip:
                requested = int(clip["endFrame"]) - start + 1
            else:
                try:
                    requested = int(item.frames) - start
                except (TypeError, ValueError):
                    requested = 100
            duration = requested
            if item.kind == "photo":
                if self.still_duration is not None:
                    duration = self.still_duration
                elif is_probe:
                    duration = max(1, requested // 2)
                elif self.lag_still_duration:
                    pending = self._pending_still_duration
                    duration = requested if pending is None else pending
                    self._pending_still_duration = requested
            self._timeline_item_number += 1
            timeline_item = FakeTimelineItem(
                "timeline-item-{0}".format(self._timeline_item_number),
                item,
                duration,
                int(clip.get("recordFrame", 0)),
                media_type,
                int(clip.get("trackIndex", 1)),
            )
            timeline = self.project.current_timeline
            timeline.items.append(timeline_item)
            tracks = (
                timeline.audio_track_items
                if media_type == 2
                else timeline.video_track_items
            )
            tracks.setdefault(timeline_item.track_index, []).append(
                timeline_item
            )
            result.append(timeline_item)
        return result


class FakeProject:
    def __init__(
        self,
        still_duration=None,
        timeline_rate="30",
        lag_still_duration=False,
        audio_frames=120,
        video_frames=120,
    ):
        self.project_id = "project-1"
        self.name = "Demo Project"
        self.timeline_rate = timeline_rate
        self.sentinel_timeline = FakeTimeline("sentinel", "Existing", timeline_rate)
        self.current_timeline = self.sentinel_timeline
        self.timelines = []
        self.media_pool = FakeMediaPool(
            self,
            still_duration=still_duration,
            lag_still_duration=lag_still_duration,
            audio_frames=audio_frames,
            video_frames=video_frames,
        )

    def GetUniqueId(self):
        return self.project_id

    def GetName(self):
        return self.name

    def GetMediaPool(self):
        return self.media_pool

    def GetSetting(self, name):
        if name == "timelineFrameRate":
            return self.timeline_rate
        return ""

    def GetCurrentTimeline(self):
        return self.current_timeline

    def SetCurrentTimeline(self, timeline):
        self.current_timeline = timeline
        return True

    def GetTimelineCount(self):
        return 1 + len(self.timelines)

    def GetTimelineByIndex(self, index):
        timelines = [self.sentinel_timeline] + list(self.timelines)
        if 1 <= index <= len(timelines):
            return timelines[index - 1]
        return None

    @property
    def deleted_timeline_ids(self):
        return self.media_pool.deleted_timeline_ids

    @property
    def final_timeline_ids(self):
        return [
            timeline.GetUniqueId()
            for timeline in self.timelines
            if not timeline.GetName().startswith("_MinoruStudio Probe ")
        ]

    @property
    def final_timelines(self):
        return [
            timeline
            for timeline in self.timelines
            if not timeline.GetName().startswith("_MinoruStudio Probe ")
        ]

    @property
    def current_timeline_id(self):
        if self.current_timeline is None:
            return None
        return self.current_timeline.GetUniqueId()


class FakeProjectManager:
    def __init__(self, project):
        self.project = project

    def GetCurrentProject(self):
        return self.project


class FakeResolve:
    def __init__(
        self,
        still_duration=None,
        timeline_rate="30",
        lag_still_duration=False,
        audio_frames=120,
        video_frames=120,
    ):
        self.project = FakeProject(
            still_duration=still_duration,
            timeline_rate=timeline_rate,
            lag_still_duration=lag_still_duration,
            audio_frames=audio_frames,
            video_frames=video_frames,
        )
        self.project_manager = FakeProjectManager(self.project)

    def GetProjectManager(self):
        return self.project_manager

    def GetProductName(self):
        return "DaVinci Resolve Studio"

    def GetVersionString(self):
        return "20.0"
