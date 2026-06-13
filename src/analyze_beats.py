# -*- coding: utf-8 -*-
"""音楽ファイルのビート解析。結果を JSON で書き出す。

使い方:
    python analyze_beats.py --audio song.mp3 --out beats.json
"""
import argparse
import json
import sys


def main():
    p = argparse.ArgumentParser(description="Beat analysis for MinoruDouga")
    p.add_argument("--audio", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    import numpy as np
    import librosa

    try:
        y, sr = librosa.load(args.audio, sr=None, mono=True)
    except Exception as e:
        sys.stderr.write(
            f"音楽ファイルを読み込めません: {e}\n"
            "(m4a/aac の場合は ffmpeg が必要です: winget install Gyan.FFmpeg)\n"
        )
        sys.exit(2)

    duration = float(librosa.get_duration(y=y, sr=sr))
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo = float(np.atleast_1d(tempo)[0])

    data = {
        "audio": args.audio,
        "bpm": tempo,
        "duration": duration,
        "beats": [round(float(b), 4) for b in beats],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"BPM={tempo:.1f} beats={len(beats)} duration={duration:.1f}s")


if __name__ == "__main__":
    main()
