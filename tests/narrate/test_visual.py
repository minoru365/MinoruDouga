from pathlib import Path

from minoru_studio.narrate.models import StoryboardRequest
from minoru_studio.narrate.visual_media import cumulative_frame_boundaries


def test_visual_boundaries_round_cumulative_cues_once_at_30fps():
    # Individual-duration rounding would place the third cut one frame later.
    assert cumulative_frame_boundaries((0, 333, 667, 1000)) == (0, 10, 20, 30)


def test_storyboard_request_has_a_descriptor_only_public_contract(tmp_path: Path):
    request = StoryboardRequest(tmp_path / "story.json", "story", tmp_path / "jobs")
    assert request.input_path.name == "story.json" and request.preview is False
