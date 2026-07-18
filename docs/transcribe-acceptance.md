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
| 入力（video） | `test/local-acceptance/japanese-disposable-20s.mp4`（Git ignored の破棄可能 fixture） |
| 入力（audio-only preview 検証） | `test/local-acceptance/japanese-disposable-audio.m4a`（Git ignored の破棄可能 fixture） |
| 入力 SHA-256 | `41544d8d73a66c8bd022b05844ea4513a246b58b93337505f89e3ed35c04ceba` |
| 入力 size / mtime-ns | 100692 bytes / `1784399235794242700` |
| 入力保全 | 実行前後で SHA-256、size、mtime が一致 |

## Job directory 証跡

すべて `test/local-acceptance/jobs/` 配下の Git ignored directory であり、内容は
commit していない。job directory、入力、model cache、transcript text の内容はこの文書にも
記録していない。

| シナリオ | job directory | 観測した最終 status |
|---|---|---|
| 未許可 create → 許可 resume | `test/local-acceptance/jobs/acceptance-no-download.media-job` | `succeeded`（初回は `failed` / `model unavailable`） |
| main（artifact / preview） | `test/local-acceptance/jobs/acceptance-current-main.media-job` | `succeeded` |
| cancel → resume | `test/local-acceptance/jobs/acceptance-current-cancel.media-job` | `succeeded`（cancel 時は `interrupted`） |
| cached offline | `test/local-acceptance/jobs/acceptance-current-offline.media-job` | `succeeded` |
| 同名 create（1回目） | `test/local-acceptance/jobs/acceptance-current-same.media-job` | `succeeded` |
| 同名 create（2回目） | `test/local-acceptance/jobs/acceptance-current-same-002.media-job` | `succeeded` |
| audio-only `-Preview` | `test/local-acceptance/jobs/acceptance-audio-preview-fixed-002.media-job` | `failed` / `input validation` |

## 内容を含まない CLI 実行証跡

以下は実行した command shape、終了コード、安定した category と job status だけである。標準
出力、標準エラー、transcript、job 内ファイルの内容は記録していない。

| シナリオ | command shape | exit / category | job status |
|---|---|---|---|
| 未許可の初回 model | `uv run minoru-studio transcribe test/local-acceptance/japanese-disposable-20s.mp4 -Name acceptance-no-download -OutputDir test/local-acceptance/jobs -Preview` | `1` / `model unavailable` | `failed`（resumable） |
| 明示許可 resume | `uv run minoru-studio transcribe resume test/local-acceptance/jobs/acceptance-no-download.media-job -AllowModelDownload` | `0` | `succeeded` |
| main と preview | `uv run minoru-studio transcribe test/local-acceptance/japanese-disposable-20s.mp4 -Name acceptance-current-main -OutputDir test/local-acceptance/jobs -Preview` | `0` | `succeeded` |
| cancel（worker 実行中） | `uv run minoru-studio transcribe test/local-acceptance/japanese-disposable-20s.mp4 -Name acceptance-current-cancel -OutputDir test/local-acceptance/jobs` | `130` / cancellation | `interrupted` |
| cancel 後の resume | `uv run minoru-studio transcribe resume test/local-acceptance/jobs/acceptance-current-cancel.media-job` | `0` | `succeeded` |
| offline 設定 | `$env:HF_HUB_OFFLINE = "1"` | 設定 | — |
| cached offline | `uv run minoru-studio transcribe test/local-acceptance/japanese-disposable-20s.mp4 -Name acceptance-current-offline -OutputDir test/local-acceptance/jobs` | `0` | `succeeded` |
| offline 設定を削除 | `Remove-Item Env:HF_HUB_OFFLINE` | 削除 | — |
| 同名 create（1回目） | `uv run minoru-studio transcribe test/local-acceptance/japanese-disposable-20s.mp4 -Name acceptance-current-same -OutputDir test/local-acceptance/jobs` | `0` | `succeeded` |
| 同名 create（2回目） | `uv run minoru-studio transcribe test/local-acceptance/japanese-disposable-20s.mp4 -Name acceptance-current-same -OutputDir test/local-acceptance/jobs` | `0` | `succeeded`（`-002`） |
| audio-only `-Preview` | `uv run minoru-studio transcribe test/local-acceptance/japanese-disposable-audio.m4a -Name acceptance-audio-preview-fixed -OutputDir test/local-acceptance/jobs -Preview` | `1` / `input validation` | `failed` |

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
