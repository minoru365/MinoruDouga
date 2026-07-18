# ローカル文字起こし受入実行記録（未実施）

## 未確認シナリオと証跡欄

- [ ] 実施日:
- [ ] 実施者:
- [ ] MinoruStudio commit:
- [ ] Python version:
- [ ] faster-whisper version:
- [ ] FFmpeg version:
- [ ] FFprobe version:
- [ ] 入力パス:
- [ ] 入力 SHA-256 / size / mtime（実行前）:
- [ ] 入力 SHA-256 / size / mtime（実行後）:
- [ ] job directory（初回、再開、offline、cancel）:
- [ ] model cache path:
- [ ] CLI command output / exit code:
- [ ] artifact SHA-256（TXT / SRT / VTT / preview）:
- [ ] subtitle timing validation:
- [ ] preview stream validation:
- [ ] cancellation / resume evidence:
- [ ] offline-cache run evidence:
- [ ] log scan evidence:
- [ ] GUI smoke evidence:
- [ ] Final Pass / Fail:

これは **0.3.0 の実モデル受入前** の記録用 runbook です。ここに実行結果や
文字起こし本文を事前に記入しません。すべての項目が確認されるまでリリース合格を
宣言しません。

## 安全上の前提

- [ ] 10〜30秒程度の、破棄可能で非公開の日本語サンプルを使う。私的な映像や音声を
  使用しない。
- [ ] マシン全体のネットワークを切断・変更しない。offline 確認には下記の
  `HF_HUB_OFFLINE` だけを使う。
- [ ] モデル重み、入力メディア、job directory、または transcript text を Git に
  commit しない。
- [ ] 証跡を共有する場合はローカル絶対パスを必要に応じて伏せ、本文ではなく
  SHA-256、サイズ、時刻、終了コードを記録する。

## 1. 実行前の環境と入力を記録する

```powershell
uv sync --locked --dev
uv run minoru-studio doctor --json
Get-FileHash -Algorithm SHA256 -LiteralPath '<sample.mp4>'
Get-Item -LiteralPath '<sample.mp4>' | Select-Object FullName,Length,LastWriteTimeUtc
```

- [ ] `doctor --json` の Python、PowerShell、uv、FFmpeg、FFprobe、faster-whisper が
  `ok` である。
- [ ] モデル cache path と `small` のキャッシュ有無を記録する。標準の cache は
  `%LOCALAPPDATA%\MinoruStudio\models\faster-whisper` である。
- [ ] 入力ファイルの実行前 hash、size、mtime を記録する。

## 2. 初回の未許可モデル確認

最初は `-AllowModelDownload` を付けずに実行し、未キャッシュモデルの場合にダウン
ロードしないことを確認する。すでにキャッシュ済みの場合は、その事実を記録して次へ
進む。

```powershell
uv run minoru-studio transcribe '<sample.mp4>' -Name acceptance-no-download -OutputDir '<jobs>' -Preview
```

- [ ] 未キャッシュ時は説明付きで停止し、モデルファイルや network state を変更しない。
- [ ] cached 時は job directory と終了コードを記録する。

## 3. 明示許可した実モデル実行と artifact 確認

まず次の command を実行する。未キャッシュ model なら安全に停止するため、実施者が
download を許可するまで model 取得は行われない。

```powershell
uv run minoru-studio transcribe <sample.mp4> -Name acceptance-transcribe -OutputDir <jobs> -Preview
```

未キャッシュで、実施者が明示的に download を許可した場合だけ次を再実行する。

```powershell
uv run minoru-studio transcribe <sample.mp4> -Name acceptance-transcribe -OutputDir <jobs> -Preview -AllowModelDownload
```

- [ ] job `status` は `succeeded`、TXT/SRT/VTT/preview.mp4 が存在する。
- [ ] 人手で主要な話し言葉を確認する。ただし transcript text はこの文書に転記しない。
- [ ] 各 artifact の SHA-256 を記録する。
- [ ] 入力の実行後 hash、size、mtime が実行前と一致する。

## 4. 字幕とプレビューを検証する

- [ ] SRT/VTT の時刻は media duration 内で、正、昇順、非重複である。
- [ ] 各 cue は最大2行、各行21文字以内で、日本語の可読性を目視確認する。
- [ ] preview は video stream と original audio stream を持ち、字幕が表示される。
- [ ] preview の duration が入力 duration と整合することを FFprobe で確認する。

```powershell
ffprobe -v error -show_entries format=duration:stream=codec_type -of json '<job>\outputs\preview.mp4'
```

## 5. キャンセルと再開

- [ ] 実行中の別 job を GUI のキャンセルで止め、`interrupted` を確認する。
- [ ] 入力を変更せず、次の再開が成功することを job directory と終了コードで記録する。

```powershell
uv run minoru-studio transcribe resume <failed-job> -AllowModelDownload
```

## 6. offline cache 実行

キャッシュ済みモデルを使い、マシン全体の接続を変更せずに実行する。

```powershell
$env:HF_HUB_OFFLINE = "1"
uv run minoru-studio transcribe <sample.mp4> -Name acceptance-offline -OutputDir <jobs>
Remove-Item Env:HF_HUB_OFFLINE
```

- [ ] cached model で成功し、モデル download を試みない。
- [ ] 環境変数を必ず削除したことを記録する。

## 7. 同名、audio-only、ログ、GUI の確認

- [ ] 同じ `-Name` を2回作成し、2つ目が `-002` の新規 job directory になる。
- [ ] audio-only 入力で `-Preview` を指定すると、推論前に拒否される。
- [ ] `logs/run.log` と job error fields に transcript text が含まれない。
- [ ] 引数なし GUI を開き、入力、model、language、normalize、denoise、preview、開始、
  キャンセル、再開の導線を smoke 確認する。

```powershell
uv run minoru-studio
```

## 最終判定

- [ ] 上記すべてが Pass。`0.3.0` への昇格を許可する。
- [ ] Fail または未確認。入力・ログ・artifact 本文を commit せず、該当 job directory
  と内容を含まないエラー区分だけを添えて実装担当へ戻す。
