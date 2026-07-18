from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from minoru_studio.transcribe.media import MediaInfo, extract_audio, probe_media, render_preview


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="FFmpeg tools not on PATH",
)


def test_ffmpeg_extracts_audio_and_renders_japanese_preview(tmp_path):
    source = tmp_path / "source.mp4"
    subtitles = tmp_path / "captions.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n", encoding="utf-8")
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac", str(source),
        ], check=True,
    )
    info = probe_media(source)
    assert info.has_audio and info.has_video
    wav = extract_audio(source, tmp_path / "work" / "inference.wav", normalize=False, denoise=False, cancel_event=None)
    assert wav.exists()
    preview = render_preview(source, subtitles, tmp_path / "preview.mp4", MediaInfo(info.duration_ms, True, True), cancel_event=None)
    assert preview.exists()
