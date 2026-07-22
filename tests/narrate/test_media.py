from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
import wave

import pytest

from minoru_studio.processes import ProcessResult
from minoru_studio.narrate.models import VideoInfo, WavInfo
from minoru_studio.transcribe.media import FontChoice
from minoru_studio.narrate.media import (
    concat_wavs, inspect_wav, probe_video, publish_wav, render_preview,
    validate_preview,
)


def result(*, stdout: str = "", returncode: int = 0) -> ProcessResult:
    return ProcessResult(returncode, stdout, "", "tool")


def pcm(path: Path, *, rate: int = 10_000, channels: int = 1, width: int = 2, frames: int = 3_335) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(b"\0" * frames * channels * width)


def probe(duration: str, streams: list[dict[str, object]]) -> str:
    return json.dumps({"format": {"duration": duration}, "streams": streams})


def test_probe_video_requires_decodable_video_and_positive_finite_dimensions_but_not_audio(tmp_path: Path):
    source = tmp_path / "source.mp4"
    payload = probe("12.345", [{"codec_type": "video", "width": 1920, "height": 1080}])
    assert probe_video(source, runner=lambda *args, **kwargs: result(stdout=payload)) == VideoInfo(12_345, 1920, 1080)
    for bad in (
        probe("12", [{"codec_type": "audio"}]),
        probe("NaN", [{"codec_type": "video", "width": 1, "height": 1}]),
        probe("0", [{"codec_type": "video", "width": 1, "height": 1}]),
        probe("1", [{"codec_type": "video", "width": 0, "height": 1}]),
    ):
        with pytest.raises(ValueError, match="video"):
            probe_video(source, runner=lambda *args, data=bad, **kwargs: result(stdout=data))


def test_inspect_wav_keeps_ffprobe_decimal_duration_and_validates_riff_pcm_shape(tmp_path: Path):
    source = tmp_path / "utterance.wav"
    pcm(source)
    assert inspect_wav(source, runner=lambda *args, **kwargs: result(stdout=probe("0.3335", [{"codec_type": "audio"}]))) == WavInfo(Decimal("0.3335"), 10_000, 1, 2)
    source.write_bytes(b"not wav")
    with pytest.raises(ValueError, match="RIFF PCM"):
        inspect_wav(source, runner=lambda *args, **kwargs: result(stdout=probe("1", [])))


def test_publish_wav_is_validated_create_only(tmp_path: Path):
    staged = tmp_path / "staged.wav"
    pcm(staged)
    destination = tmp_path / "outputs" / "utterance-0001.wav"
    info = publish_wav(staged.read_bytes(), destination, runner=lambda *args, **kwargs: result(stdout=probe("0.3335", [])))
    assert info == WavInfo(Decimal("0.3335"), 10_000, 1, 2)
    with pytest.raises(ValueError, match="already exists"):
        publish_wav(staged.read_bytes(), destination, runner=lambda *args, **kwargs: result(stdout=probe("0.3335", [])))


def test_concat_wavs_alternates_exact_matching_silence_and_validates_output(tmp_path: Path):
    first, second = tmp_path / "one.wav", tmp_path / "two.wav"
    pcm(first); pcm(second)
    destination = tmp_path / "outputs" / "narration.wav"
    calls: list[list[str]] = []
    concat_lines: list[str] = []
    silence_shape: list[tuple[int, int, int, int]] = []
    def runner(args, **kwargs):
        calls.append(list(args))
        if args[0] == "ffmpeg":
            concat_lines.extend(Path(args[args.index("-i") + 1]).read_text(encoding="utf-8").splitlines())
            silence_path = Path(concat_lines[1][6:-1])
            with wave.open(str(silence_path), "rb") as wav:
                silence_shape.append((wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()))
            pcm(Path(args[-1]), frames=9_670)
            return result()
        return result(stdout=probe("0.967", []))
    info = concat_wavs([first, second], destination, silence_ms=300, runner=runner, cancel_event=None)
    assert info == WavInfo(Decimal("0.967"), 10_000, 1, 2)
    assert len(concat_lines) == 3 and concat_lines[0].startswith("file '") and concat_lines[2].startswith("file '")
    assert silence_shape == [(10_000, 1, 2, 3_000)]
    command = next(command for command in calls if command[0] == "ffmpeg")
    assert ["-f", "concat", "-safe", "0"] == command[command.index("-f"):command.index("-f") + 4]
    assert "-vn" in command and command[command.index("-c:a") + 1] == "pcm_s16le"


def test_concat_rejects_mismatched_shapes_and_nonzero_or_missing_output(tmp_path: Path):
    first, second = tmp_path / "one.wav", tmp_path / "two.wav"
    pcm(first); pcm(second, channels=2)
    with pytest.raises(ValueError, match="shape"):
        concat_wavs([first, second], tmp_path / "out.wav", silence_ms=300, cancel_event=None)
    pcm(second)
    def failed_ffmpeg(args, **kwargs):
        return result(stdout=probe("0.3335", []) if args[0] == "ffprobe" else "", returncode=0 if args[0] == "ffprobe" else 1)
    with pytest.raises(RuntimeError, match="concat"):
        concat_wavs([first, second], tmp_path / "out.wav", silence_ms=300, runner=failed_ffmpeg, cancel_event=None)


def test_preview_maps_generated_audio_holds_video_and_validates_longer_duration(tmp_path: Path):
    source, narration, subtitles = tmp_path / "source.mp4", tmp_path / "narration.wav", tmp_path / "subs.srt"
    subtitles.write_text("", encoding="utf-8")
    font = tmp_path / "Fonts" / "meiryo.ttc"; font.parent.mkdir(); font.touch()
    calls: list[list[str]] = []
    def runner(args, **kwargs):
        calls.append(list(args))
        if args[0] == "ffmpeg":
            Path(args[-1]).write_bytes(b"preview")
            return result()
        return result(stdout=probe("1.5", [{"codec_type": "video"}, {"codec_type": "audio"}]))
    destination = render_preview(source, narration, subtitles, tmp_path / "preview.mp4", VideoInfo(1_000, 16, 9), 1_500, font=FontChoice("Meiryo", font), runner=runner, cancel_event=None)
    command = calls[0]
    assert ["-map", "0:v:0", "-map", "1:a:0"] == command[command.index("-map"):command.index("-map") + 4]
    assert "0:a" not in command and "-shortest" not in command and command[command.index("-t") + 1] == "1.500"
    assert "tpad=stop_mode=clone" in command[command.index("-filter:v") + 1]
    assert destination.exists()
    with pytest.raises(ValueError, match="duration"):
        validate_preview(destination, 1_500, runner=lambda *args, **kwargs: result(stdout=probe("1.751", [{"codec_type": "video"}, {"codec_type": "audio"}])))
