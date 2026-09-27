from __future__ import annotations

import json
from pathlib import Path

import pytest

from minoru_studio.narrate.storyboard import parse_storyboard
from minoru_studio.narrate.visual_media import music_mix_arguments, plan_music_runs


def _descriptor(tmp_path: Path, payload: dict[str, object]) -> Path:
    (tmp_path / "frame.png").write_bytes(b"image")
    (tmp_path / "normal.mp3").write_bytes(b"normal")
    (tmp_path / "battle.wav").write_bytes(b"battle")
    descriptor = tmp_path / "story.json"
    descriptor.write_text(json.dumps(payload), encoding="utf-8")
    return descriptor


def _music(**overrides: object) -> dict[str, object]:
    music: dict[str, object] = {
        "tracks": [{"id": "normal", "source": "normal.mp3"}, {"id": "battle", "source": "battle.wav", "gain_db": -12.5}],
        "default": "normal",
    }
    music.update(overrides)
    return music


def _clips(*music: str | None) -> list[dict[str, object]]:
    clips: list[dict[str, object]] = []
    for index, track in enumerate(music):
        clip: dict[str, object] = {"id": f"c{index}", "kind": "image", "source": "frame.png", "narration": "文。"}
        if track is not None:
            clip["music"] = track
        clips.append(clip)
    return clips


def test_storyboard_music_declares_tracks_defaults_and_clip_references(tmp_path: Path):
    storyboard = parse_storyboard(_descriptor(tmp_path, {"version": 1, "music": _music(), "clips": _clips(None, "battle")}))

    music = storyboard.music
    assert music is not None
    assert [(track.id, track.source.name, track.gain_db) for track in music.tracks] == [("normal", "normal.mp3", -18.0), ("battle", "battle.wav", -12.5)]
    assert (music.default, music.crossfade_ms) == ("normal", 1500)
    assert [clip.music for clip in storyboard.clips] == [None, "battle"]
    assert [storyboard.track_for(clip) for clip in storyboard.clips] == ["normal", "battle"]
    assert [path.name for path in storyboard.input_paths] == ["frame.png", "normal.mp3", "battle.wav"]


def test_storyboard_without_music_keeps_the_visual_input_contract(tmp_path: Path):
    storyboard = parse_storyboard(_descriptor(tmp_path, {"version": 1, "clips": _clips(None)}))

    assert storyboard.music is None
    assert storyboard.input_paths == storyboard.sources


def test_storyboard_music_deduplicates_a_source_shared_by_tracks(tmp_path: Path):
    music = _music(tracks=[{"id": "a", "source": "normal.mp3"}, {"id": "b", "source": "normal.mp3", "gain_db": 0}], default="a")
    storyboard = parse_storyboard(_descriptor(tmp_path, {"version": 1, "music": music, "clips": _clips(None, "b")}))

    assert [path.name for path in storyboard.input_paths] == ["frame.png", "normal.mp3"]


@pytest.mark.parametrize(
    ("music", "clips"),
    [
        (None, _clips("battle")),
        (_music(), _clips("missing")),
        (_music(default="missing"), _clips(None)),
        (_music(tracks=[]), _clips(None)),
        (_music(tracks=[{"id": f"t{index}", "source": "normal.mp3"} for index in range(9)], default="t0"), _clips(None)),
        (_music(tracks=[{"id": "normal", "source": "normal.mp3"}, {"id": "normal", "source": "battle.wav"}]), _clips(None)),
        (_music(tracks=[{"id": "normal", "source": "normal.mp3", "gain_db": 1}]), _clips(None)),
        (_music(tracks=[{"id": "normal", "source": "normal.mp3", "gain_db": -41}]), _clips(None)),
        (_music(tracks=[{"id": "normal", "source": "normal.mp3", "gain_db": True}]), _clips(None)),
        (_music(tracks=[{"id": "normal", "source": "normal.mp3", "volume": -3}]), _clips(None)),
        (_music(tracks=[{"id": "normal", "source": "frame.png"}]), _clips(None)),
        (_music(tracks=[{"id": "normal", "source": "https://example.test/a.mp3"}]), _clips(None)),
        (_music(tracks=[{"id": "normal", "source": "absent.mp3"}]), _clips(None)),
        (_music(crossfade_ms=-1), _clips(None)),
        (_music(crossfade_ms=5001), _clips(None)),
        (_music(crossfade_ms=1.5), _clips(None)),
        (_music(loop=True), _clips(None)),
    ],
)
def test_storyboard_music_rejects_undeclared_or_out_of_range_content(tmp_path: Path, music: dict[str, object] | None, clips: list[dict[str, object]]):
    payload: dict[str, object] = {"version": 1, "clips": clips}
    if music is not None:
        payload["music"] = music

    with pytest.raises(ValueError):
        parse_storyboard(_descriptor(tmp_path, payload))


def test_music_runs_merge_consecutive_clips_and_fade_at_the_end():
    assert plan_music_runs([("a", 0, 1_000), ("a", 1_000, 5_000)], crossfade_ms=1_500) == (
        {"track": "a", "start_ms": 0, "end_ms": 5_000, "fade_in_ms": 0, "fade_out_ms": 2_000},
    )


def test_music_runs_restart_and_crossfade_centered_on_each_boundary():
    runs = plan_music_runs([("a", 0, 10_000), ("b", 10_000, 20_000), ("a", 20_000, 30_000)], crossfade_ms=1_500)

    assert runs == (
        {"track": "a", "start_ms": 0, "end_ms": 10_750, "fade_in_ms": 0, "fade_out_ms": 1_500},
        {"track": "b", "start_ms": 9_250, "end_ms": 20_750, "fade_in_ms": 1_500, "fade_out_ms": 1_500},
        {"track": "a", "start_ms": 19_250, "end_ms": 30_000, "fade_in_ms": 1_500, "fade_out_ms": 2_000},
    )


def test_music_runs_clamp_crossfades_to_short_neighbouring_runs():
    runs = plan_music_runs([("a", 0, 10_000), ("b", 10_000, 11_000), ("a", 11_000, 20_000)], crossfade_ms=1_500)

    assert runs == (
        {"track": "a", "start_ms": 0, "end_ms": 10_500, "fade_in_ms": 0, "fade_out_ms": 1_000},
        {"track": "b", "start_ms": 9_500, "end_ms": 11_500, "fade_in_ms": 1_000, "fade_out_ms": 1_000},
        {"track": "a", "start_ms": 10_500, "end_ms": 20_000, "fade_in_ms": 1_000, "fade_out_ms": 2_000},
    )
    tail = plan_music_runs([("a", 0, 10_000), ("b", 10_000, 11_000)], crossfade_ms=1_500)
    assert tail[-1] == {"track": "b", "start_ms": 9_500, "end_ms": 11_000, "fade_in_ms": 1_000, "fade_out_ms": 500}


def test_music_runs_without_crossfade_cut_at_the_boundary():
    assert plan_music_runs([("a", 0, 1_000), ("b", 1_000, 4_000)], crossfade_ms=0) == (
        {"track": "a", "start_ms": 0, "end_ms": 1_000, "fade_in_ms": 0, "fade_out_ms": 0},
        {"track": "b", "start_ms": 1_000, "end_ms": 4_000, "fade_in_ms": 0, "fade_out_ms": 2_000},
    )


@pytest.mark.parametrize(
    "assignments",
    [[], [("a", 100, 1_000)], [("a", 0, 1_000), ("b", 1_200, 2_000)], [("a", 0, 1_000), ("b", 900, 2_000)], [("a", 0, 0)]],
)
def test_music_runs_require_contiguous_cover_from_zero(assignments: list[tuple[str, int, int]]):
    with pytest.raises(ValueError):
        plan_music_runs(assignments, crossfade_ms=1_500)


def test_music_mix_loops_each_run_and_mixes_without_normalizing_narration():
    runs = [
        {"track": "a", "source": "C:/m/a.mp3", "gain_db": -18.0, "start_ms": 0, "end_ms": 10_750, "fade_in_ms": 0, "fade_out_ms": 1_500},
        {"track": "b", "source": "C:/m/b.wav", "gain_db": -12.5, "start_ms": 9_250, "end_ms": 20_000, "fade_in_ms": 1_500, "fade_out_ms": 2_000},
    ]

    inputs, graph = music_mix_arguments(runs, narration_input=3, first_input=4)

    assert inputs == ["-stream_loop", "-1", "-t", "10.750", "-i", "C:/m/a.mp3", "-stream_loop", "-1", "-t", "10.750", "-i", "C:/m/b.wav"]
    assert "[3:a]aformat=sample_rates=48000:channel_layouts=stereo[narration]" in graph
    assert "[4:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=-18.0dB,afade=t=out:st=9.250:d=1.500,adelay=0:all=1[music0]" in graph
    assert "[5:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=-12.5dB,afade=t=in:st=0:d=1.500,afade=t=out:st=8.750:d=2.000,adelay=9250:all=1[music1]" in graph
    assert graph.endswith("[narration][music0][music1]amix=inputs=3:normalize=0:duration=first,alimiter=limit=0.97[outa]")
