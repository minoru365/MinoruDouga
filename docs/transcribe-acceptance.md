# ローカル文字起こし受入実行記録（合格）

実施日: 2026-07-19（Asia/Tokyo）
MinoruStudio commit: `6d34602`
最終判定: **Pass — 0.3.0 への昇格を許可**

## 安全な実行条件

- 破棄可能な20秒の日本語 Windows TTS / color-video fixture を使った。私的な映像・音声は
  使用していない。
- マシン全体のネットワークは変更していない。offline 確認には
  `HF_HUB_OFFLINE=1` だけを使い、実行後に削除した。
- モデル重み、入力メディア、job directory、または transcript text は Git に commit
  していない。以下は内容を含まない hash、サイズ、時刻、終了コードだけの記録である。

## 環境と入力

| 項目 | 記録 |
|---|---|
| Python | 3.12.10 |
| uv | 0.11.29 |
| faster-whisper provider | 1.2.1 |
| FFmpeg / FFprobe | 8.1.2-full_build-www.gyan.dev |
| model cache | `%LOCALAPPDATA%\\MinoruStudio\\models\\faster-whisper\\small` |
| 入力 | 破棄可能なローカル fixture（パス非記録） |
| 入力 SHA-256 | `41544d8d73a66c8bd022b05844ea4513a246b58b93337505f89e3ed35c04ceba` |
| 入力 size / mtime-ns | 100692 bytes / `1784399235794242700` |
| 入力保全 | 実行前後で SHA-256、size、mtime が一致 |

## 受入シナリオ

| # | シナリオ | 結果 | 内容を含まない証跡 |
|---|---|---|---|
| 1 | 未許可の初回 model | Pass | cache 未存在で `-AllowModelDownload` なしの初回実行は exit 1、`model unavailable` の failed/resumable job を作成。cache は不変。 |
| 2 | 明示許可した `small` 実行 | Pass | 同じ job を `-AllowModelDownload` で再開し、model を取得して exit 0 / `succeeded`。 |
| 3 | TXT/SRT/VTT/preview | Pass | 全4 artifact を作成し、合成された話し言葉を内容非記録の確認で認識できた。 |
| 4 | 字幕・preview | Pass | SRT 2 cues は正、昇順、非重複、20秒内、最大2行かつ各行21文字以内。VTT header は有効。preview は audio/video stream を持ち duration 20.016009秒。 |
| 5 | 実行中 cancel / resume | Pass | 実 worker 中の cancellation event で `interrupted` を記録し、変更していない同一 job の resume が exit 0 / `succeeded`。 |
| 6 | cached offline | Pass | `HF_HUB_OFFLINE=1` で cached model 実行が `succeeded`。環境変数を削除済み。 |
| 7 | 同名 create | Pass | 2回の成功 job が base と `-002` の別 directory を作成。 |
| 8 | audio-only `-Preview` | Pass | real CLI は exit 1 / `input validation`。`probe-input` のみ failed、extract / transcribe / artifact / preview は未開始、work / outputs は空、model cache metadata も不変。 |
| 9 | transcript redaction | Pass | transcript output を持つ11 job を含む13 job の `logs/run.log` と manifest/error fields を内容非記録で走査し、leak 0。 |
| 10 | GUI smoke | Pass | 実ユーザーが引数なし GUI の文字起こし panel で input、model、language、normalize、denoise、preview、開始、キャンセル、既存 job を開く導線を視認。job は開始せず model dialog も表示されなかった。resume は実 cancel/resume 証跡でも確認。 |

## Artifact hashes

| Artifact | SHA-256 |
|---|---|
| TXT | `2d0c6d2ba94eb330358a2e5a854d633e97a7151bce02c870f8bc92e4a226400c` |
| SRT | `4369341781c2bdc2f1af101b77e27666449a5d081e3440a700518daeb76d09e5` |
| VTT | `1f7bc392e8ddc8c029fc1a06ce6e16fc0da9c7f94578c5c228c90bde7e2220c9` |
| preview | `57cd2e8cd0e0895358de9ba0ed1bb85bc03871d1e618532d8cb46e26ebcba1b7` |

## 残存リスク

- faster-whisper の精度と CPU 実行時間は入力品質とハードウェアに依存する。
- subtitle filter と日本語 font の利用可否は FFmpeg / Windows 環境に依存する。
- forceful termination は一時ファイルを残す可能性があるため、完了していない model cache を
  完全な cache として扱わない。
