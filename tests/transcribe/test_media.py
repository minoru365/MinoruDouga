from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import pytest

from minoru_studio.processes import ProcessResult
from minoru_studio.transcribe.media import (
    DENOISE_FILTER,
    FontChoice,
    LOUDNESS_TARGET,
    MediaInfo,
    MediaToolVersions,
    extract_audio,
    probe_media,
    read_media_tool_versions,
    render_preview,
    resolve_japanese_font,
    validate_preview,
)


def result(*, stdout: str = "", stderr: str = "", returncode: int = 0) -> ProcessResult:
    return ProcessResult(returncode, stdout, stderr, "tool")


def probe_payload(duration: str = "12.345", streams: list[str] | None = None) -> str:
    return json.dumps(
        {
            "format": {"duration": duration},
            "streams": [{"codec_type": stream} for stream in streams or ["audio", "video"]],
        }
    )


def write_pcm(path: Path, *, clipped: bool = False) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        samples = (-32768, 0, 32767) if clipped else (-1200, 0, 1200)
        output.writeframes(struct.pack("<" + "h" * len(samples), *samples))


def test_probe_uses_stream_types_and_rounds_duration(tmp_path):
    source = tmp_path / "source.not-a-video-extension"
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        return result(stdout=probe_payload())

    assert probe_media(source, runner=runner) == MediaInfo(12_345, True, True)
    assert calls == [[
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
        "-of", "json", str(source),
    ]]


@pytest.mark.parametrize("duration", ["0", "-1", "NaN", "not-a-number"])
def test_probe_rejects_nonpositive_or_invalid_duration(tmp_path, duration):
    with pytest.raises(ValueError, match="duration"):
        probe_media(tmp_path / "source", runner=lambda *args, **kwargs: result(stdout=probe_payload(duration)))


def test_extract_audio_rejects_sources_without_audio(tmp_path):
    with pytest.raises(RuntimeError, match="audio"):
        extract_audio(
            tmp_path / "source.mp4", tmp_path / "inference.wav", normalize=False, denoise=False,
            runner=lambda *args, **kwargs: result(returncode=1), cancel_event=None,
        )


def test_extract_audio_without_filters_uses_required_pcm_command_and_replaces_destination(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    destination = tmp_path / "work" / "inference.wav"
    destination.parent.mkdir()
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        write_pcm(Path(args[-1]))
        return result()

    monkeypatch.setattr("minoru_studio.transcribe.media.uuid4", lambda: type("U", (), {"hex": "fixed"})())
    assert extract_audio(source, destination, normalize=False, denoise=False, runner=runner, cancel_event=None) == destination
    temporary = destination.parent / ".inference.fixed.wav"
    assert calls[0] == [
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-i", str(source), "-map", "0:a:0", "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(temporary),
    ]
    assert destination.exists() and not temporary.exists()


def test_extract_audio_normalization_orders_denoise_before_loudnorm_and_uses_measurements(tmp_path):
    source = tmp_path / "source.mp4"
    destination = tmp_path / "work" / "inference.wav"
    destination.parent.mkdir()
    calls: list[list[str]] = []
    measurements = json.dumps({
        "input_i": "-20.1", "input_lra": "3.2", "input_tp": "-1.0",
        "input_thresh": "-30.0", "target_offset": "0.4",
    })

    def runner(args, **kwargs):
        calls.append(list(args))
        if args[-1] != "-":
            write_pcm(Path(args[-1]))
        return result(stderr="analysis\n" + measurements)

    extract_audio(source, destination, normalize=True, denoise=True, runner=runner, cancel_event=None)
    assert calls[0][calls[0].index("-v") + 1] == "info"
    assert DENOISE_FILTER in calls[0][calls[0].index("-af") + 1]
    assert LOUDNESS_TARGET in calls[0][calls[0].index("-af") + 1]
    assert calls[0][calls[0].index("-af") + 1].index(DENOISE_FILTER) < calls[0][calls[0].index("-af") + 1].index(LOUDNESS_TARGET)
    second_filter = calls[1][calls[1].index("-af") + 1]
    assert "measured_I=-20.1" in second_filter and "linear=true" in second_filter


def test_extract_audio_rejects_invalid_final_loudness_statistics_object(tmp_path):
    destination = tmp_path / "work" / "inference.wav"
    valid = json.dumps({
        "input_i": "-20.1", "input_lra": "3.2", "input_tp": "-1.0",
        "input_thresh": "-30.0", "target_offset": "0.4",
    })

    def runner(args, **kwargs):
        if args[-1] != "-":
            write_pcm(Path(args[-1]))
        return result(stderr=valid + "\n" + json.dumps({"input_i": "nan"}))

    with pytest.raises(ValueError, match="loudness"):
        extract_audio(
            tmp_path / "source.mp4", destination, normalize=True, denoise=False,
            runner=runner, cancel_event=None,
        )


def test_extract_audio_rejects_malformed_loudness_statistics(tmp_path):
    with pytest.raises(ValueError, match="loudness"):
        extract_audio(
            tmp_path / "source.mp4", tmp_path / "work" / "inference.wav", normalize=True, denoise=False,
            runner=lambda *args, **kwargs: result(stderr="{not json}"), cancel_event=None,
        )


def test_extract_audio_rejects_clipped_pcm_without_replacing_destination(tmp_path):
    destination = tmp_path / "work" / "inference.wav"
    destination.parent.mkdir()
    destination.write_bytes(b"preserve")

    def runner(args, **kwargs):
        write_pcm(Path(args[-1]), clipped=True)
        return result()

    with pytest.raises(ValueError, match="clipped"):
        extract_audio(tmp_path / "source.mp4", destination, normalize=False, denoise=False, runner=runner, cancel_event=None)
    assert destination.read_bytes() == b"preserve"


def test_tool_versions_capture_first_output_line():
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        return result(stdout=f"{args[0]} version 8.1.2\nconfiguration")

    assert read_media_tool_versions(runner=runner) == MediaToolVersions("ffmpeg version 8.1.2", "ffprobe version 8.1.2")
    assert calls == [["ffmpeg", "-version"], ["ffprobe", "-version"]]


def test_resolve_japanese_font_uses_priority_order(tmp_path):
    fonts = tmp_path / "Fonts"
    fonts.mkdir()
    (fonts / "meiryo.ttc").touch()
    (fonts / "msgothic.ttc").touch()
    assert resolve_japanese_font(tmp_path).family == "Meiryo"
    (fonts / "YuGothR.ttc").touch()
    assert resolve_japanese_font(tmp_path).family == "Yu Gothic"


def test_render_preview_rejects_audio_only_sources(tmp_path):
    with pytest.raises(ValueError, match="video"):
        render_preview(
            tmp_path / "source.wav", tmp_path / "subtitles.srt", tmp_path / "preview.mp4",
            MediaInfo(1_000, True, False), runner=lambda *args, **kwargs: result(), cancel_event=None,
        )


def test_render_preview_escapes_windows_filter_path_and_validates_before_replace(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    subtitles = tmp_path / "sub dir" / "captions.srt"
    subtitles.parent.mkdir()
    subtitles.touch()
    destination = tmp_path / "preview.mp4"
    font_dir = tmp_path / "Fonts"
    font_dir.mkdir()
    (font_dir / "YuGothR.ttc").touch()
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        if args[0] == "ffmpeg":
            Path(args[-1]).write_bytes(b"preview")
            return result()
        return result(stdout=probe_payload("1.25"))

    monkeypatch.setattr("minoru_studio.transcribe.media.resolve_japanese_font", lambda: resolve_japanese_font(tmp_path))
    render_preview(source, subtitles, destination, MediaInfo(1_000, True, True), runner=runner, cancel_event=None)
    filter_value = calls[0][calls[0].index("-vf") + 1]
    assert "subtitles=" in filter_value and "fontsdir=" in filter_value
    assert "\\\\" not in filter_value
    assert "force_style=FontName=Yu Gothic" in filter_value
    assert destination.read_bytes() == b"preview"


def test_render_preview_uses_explicit_preflight_font_without_resolving_another(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    subtitles = tmp_path / "captions.srt"
    subtitles.touch()
    destination = tmp_path / "preview.mp4"
    font_file = tmp_path / "Fonts" / "meiryo.ttc"
    font_file.parent.mkdir()
    font_file.touch()
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        calls.append(list(args))
        if args[0] == "ffmpeg":
            Path(args[-1]).write_bytes(b"preview")
            return result()
        return result(stdout=probe_payload("1"))

    monkeypatch.setattr(
        "minoru_studio.transcribe.media.resolve_japanese_font",
        lambda: pytest.fail("renderer must use the supplied font"),
    )
    render_preview(
        source, subtitles, destination, MediaInfo(1_000, True, True),
        font=FontChoice("Meiryo", font_file), runner=runner, cancel_event=None,
    )

    filter_value = calls[0][calls[0].index("-vf") + 1]
    font_directory = str(font_file.parent).replace("\\", "/").replace(":", "\\:")
    assert f"fontsdir='{font_directory}'" in filter_value
    assert "force_style=FontName=Meiryo" in filter_value


def test_validate_preview_accepts_inclusive_duration_boundary_and_requires_both_streams(tmp_path):
    preview = tmp_path / "preview.mp4"
    assert validate_preview(
        preview, 1_000,
        runner=lambda *args, **kwargs: result(stdout=probe_payload("1.25")),
    ) is None
    with pytest.raises(ValueError, match="audio"):
        validate_preview(
            preview, 1_000,
            runner=lambda *args, **kwargs: result(stdout=probe_payload("1", ["video"])),
        )
