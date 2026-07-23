# Resolve Transcribe / Narrate 一括受入 Runbook

この文書は、未受入の `script-draft` 実動画、`narrate` 実音声・実preview、
`transcribe` / `narrate` の Resolve 配置を、同じ日・同じ破棄可能 workspace で
一括受入するための runbook と記録である。実行前の状態はすべて `未実行` とし、
実機で観測した事実だけを `合格` / `不合格` / `中止` に書き換える。

## 目的

- `transcribe -Preview` と `narrate -Preview` の `preview.mp4` が、Resolve なしで
  意図した音声と焼き込み字幕付きで単独再生できることを確認する。
- 成功済み `transcribe` / `narrate` ジョブを Resolve の新規タイムラインへ
  V1/A1 として配置し、手動 SRT インポートによる編集可能な字幕トラックを
  確認する。
- `script-draft` の実動画受入(代表フレームと台本テンプレート)を同じ記録に
  まとめる。

## 安全条件

すべての条件を満たさない場合は、その時点で停止して後段を実行しない。

- 新規に作成した破棄可能なローカル Resolve プロジェクトだけを使う。
  制作中プロジェクト、共有データベース、既存 MinoruDouga 環境には触れない。
- MinoruStudio が作成してよいのは `_MinoruStudio …` bin と
  `<job name> Resolve` / `-002` タイムラインだけである。既存 bin/timeline の
  選択・再利用・上書き・削除を行わない。
- 入力動画・台本・成功済み成果物を上書き・削除しない。事前・事後の SHA-256 で
  不変を確認する。
- VOICEVOX は利用者が事前に `127.0.0.1` (loopback) で起動しておく。
  MinoruStudio / Resolve アダプターは VOICEVOX を起動しない。外部送信は行わない。
- 最終レンダー自動化、Resolve オブジェクトの削除によるクリーンアップは行わない。
- 記録には台本本文・字幕本文・文字起こし本文・音声データを含めない。
  hash、size、duration、artifact kind、状態、attempt/timeline ID・名前、
  frame rate、exit code、失敗 category だけを記録する。

## 事前確認

1. 機械ゲート(全 adapter テストと全体 suite)が合格済みであることを確認する。
2. 環境を記録する。

```powershell
uv run minoru-studio doctor --json
```

- 実施日:
- MinoruStudio commit:
- Resolve product / version:
- VOICEVOX loopback 稼働確認(バージョンのみ記録):
- 破棄可能プロジェクト名 / project ID:
- timeline frame rate:

3. Git 管理外の破棄可能な日本語短尺動画と、人が記入・承認した短い日本語台本を
   用意し、事前ハッシュを記録する。

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '.\disposable-source.mp4', '.\approved-script.txt' |
    ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath '.\evidence\input-hashes-before.json' -Encoding utf8
```

## 実行手順

### 1. ローカル準備フロー(Resolve 不使用)

```powershell
uv run minoru-studio script-draft .\disposable-source.mp4 -Name final-script-draft -OutputDir .\acceptance-jobs
uv run minoru-studio narrate .\disposable-source.mp4 -Script .\approved-script.txt -Name final-narrate -OutputDir .\acceptance-jobs -Preview
uv run minoru-studio transcribe .\disposable-source.mp4 -Name final-transcribe -OutputDir .\acceptance-jobs -Preview
```

1. `script-draft` の代表フレームと `script.md` テンプレートの生成を確認する
   (内容は記録しない)。
2. 各 `preview.mp4` を Resolve を起動せずにプレイヤーで再生し、意図した音声
   (transcribe は元音声、narrate は生成ナレーション)と焼き込み字幕を確認する。
3. `job.json` の `status: succeeded` と artifact メタデータだけを確認し、
   事前ハッシュと比較して入力不変を確認する。
4. ローカル依存が不足していた場合はそこで停止し、エラー種別だけを記録する。
   利用者の指示なしにインストールや範囲拡大を行わない。

### 2. Resolve 適用(ジョブごとに別々の Utility 実行)

1. 破棄可能プロジェクトに sentinel timeline(例 `SENTINEL-DO-NOT-TOUCH`)を先に
   作り、ID・名前・クリップ数を記録する。
2. **ワークスペース → スクリプト → MinoruStudio** で `final-transcribe` を選び、
   `素材を取り込む` → 確認ダイアログ承認 → 新規タイムライン生成を実行する。
3. V1=元動画、A1=元動画音声、開始 record frame がタイムライン先頭であることを
   確認する。checkpoint 案内後に Utility が閉じることを確認する。
4. Resolve 標準UI(タイムライン → 字幕トラックを追加 → SRT を読み込み)で、
   案内された検証済み `outputs/subtitles.srt` を手動インポートし、字幕トラックが
   編集可能であることを確認する。
5. Utility を再実行し `字幕読み込みを確認` を押して `applied` を確認する。
6. `final-narrate` にも 2〜5 を繰り返す。A1 が `outputs/narration.wav` である
   ことを確認する。

### 3. 非破壊・再適用確認

1. 各ジョブで `新しい適用を開始` により新 attempt を実行し、`<job name> Resolve-002`
   が作られることを確認する。
2. sentinel、既存 bin/timeline、以前の attempt の bin/timeline が変更されて
   いないことを確認する。
3. 事後ハッシュを取得し、事前ハッシュと一致することを確認する。
4. 安全条件違反を検知したら停止する。クリーンアップ目的でも Resolve オブジェクト
   を削除しない。

## 期待結果

| # | シナリオ | 合格条件 | 結果 | Notes |
|---|---|---|---|---|
| 1 | transcribe 単独preview | `preview.mp4` が Resolve なしで元動画の映像・音声+焼き込み字幕で再生できる | 未実行 | |
| 2 | narrate 単独preview | `preview.mp4` が Resolve なしで元動画の映像+生成ナレーション+焼き込み字幕で再生できる | 未実行 | |
| 3 | transcribe V1/A1 | 新規タイムラインの V1=元動画、A1=同じ元動画の音声 | 未実行 | |
| 4 | narrate V1/A1 | 新規タイムラインの V1=元動画、A1=`outputs/narration.wav` | 未実行 | |
| 5 | 開始位置 | V1/A1 とも record frame がタイムライン先頭(開始位置 0) | 未実行 | |
| 6 | 手動字幕トラック | 手動インポートした SRT の字幕トラックが存在し Resolve 内で編集できる。確認後だけ `applied` | 未実行 | |
| 7 | 非破壊 | sentinel・既存 bin/timeline が名前・内容とも不変 | 未実行 | |
| 8 | 再適用 | 新 attempt が `<job name> Resolve-002` を新規作成し既存を再利用しない | 未実行 | |
| 9 | script-draft 実動画 | 代表フレームと `script.md` テンプレートが生成される | 未実行 | |
| 10 | 入力hash不変 | 元動画・台本の事前・事後 SHA-256 が一致 | 未実行 | |

## 実行記録

- 実行状態: 未実行
- 実施日: 未実行
- attempt / bin / timeline ID の証跡: 未実行
- 失敗・中止時の category: 未実行

証跡収集は [Resolve beat-sync 受入記録](resolve-beat-sync-acceptance.md) の
「ソース素材を含めず証跡を収集する」と同じ方式を使い、素材・台本・字幕・音声の
内容を証跡ディレクトリへ入れない。
