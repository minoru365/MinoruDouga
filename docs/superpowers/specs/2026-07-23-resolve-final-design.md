# Resolve 最終連携・まとめ受入設計

## 1. 目的

`transcribe` と `narrate` は Resolve なしでも確認用 MP4 を完成成果物として使え、こだわった微修正が必要なときだけ DaVinci Resolve 無償版の新規かつ編集可能なタイムラインへ安全に配置できるようにする。併せて、未受入の `script-draft` 実動画、`narrate` 実音声／preview、`transcribe`／`narrate` の Resolve 配置を、破棄可能な素材とプロジェクトで一括受入する。

## 2. スコープと非対象

### スコープ

- 既存 Resolve Utility の同一 UI から、成功済み `transcribe`／`narrate` ジョブを選択・検証・配置する。
- 動画入力の `transcribe -Preview` と `narrate -Preview` は、Resolve を起動せずに視聴・配布できる確認用 `preview.mp4` を作る。`transcribe` は元動画の映像・音声、`narrate` は元動画の映像と生成済みナレーションを使い、生成済み SRT を映像へ焼き込む。
- `transcribe` は元動画を V1 と A1 に、`narrate` は元動画を V1、生成済み `narration.wav` を A1 に、タイムライン開始位置へ配置する。
- 生成済み `subtitles.srt` を Resolve 標準 UI で手動インポートする中間状態を管理し、字幕トラックの存在と明示確認後に適用完了を記録する。
- 既存 `beat-sync` のアダプター契約・状態遷移・配置結果を保持する。
- 実装の機械検証、実機一括受入 runbook、内容非記録の受入記録、README／引き継ぎ更新を行う。

### 非対象

- SRT の字幕クリップを Resolve API だけで自動作成・自動配置すること。インストール済み API は字幕トラック操作を持つが、既存 SRT の字幕項目を作成する API を提供しない。
- `CreateSubtitlesFromAudio` による再認識。既存 SRT の内容・時刻と一致しない可能性があるため使わない。
- preview MP4 を最終タイムラインの代替として配置すること。焼き込み字幕は編集可能でないため使わない。
- MP4 内にオン・オフ可能な字幕ストリームを追加すること。単独視聴用 preview の字幕は互換性を優先して焼き込みとし、編集可能な字幕は Resolve 側の手動 SRT インポートで扱う。
- 自動レンダー、既存 timeline/bin の削除、Resolve プロジェクトのロールバック、外部送信、VOICEVOX の自動起動。
- `script-draft` の Resolve 配置または AI 台本化。

## 3. アダプター構成

Resolve Utility はジョブの `mode` を検証して、既存の beat-sync 経路か新しい media-placement 経路へ明示的に分岐する。共通で `ApplicationStore`、Windows job lock、Resolve gateway、`resolve/applications/<attempt-id>.json`、内容非記録ログを使う。

`beat-sync` の `load_validated_job`、still 測定、In/Out 再開、青 marker、BGM 配置を変更しない。新しい経路は別の正規化済み配置入力を作り、beat-sync 固有の `plan`、BPM、still、素材ローテーションを参照しない。

### 3.1 Resolve を使わない完成経路

`preview.mp4` は Resolve 用の中間ファイルではなく、Resolve を使わずに確認・配布するための独立した完成成果物とする。利用者は `-Preview` または GUI の preview 選択で生成し、必要な場合だけ Resolve 適用へ進む。preview は既存の入力・WAV・SRT/VTT を読み取り専用で使用し、既存成果物を上書きしない。

字幕は preview の映像に焼き込まれるため、その MP4 単体でナレーション／元音声と同期して視聴できる。一方、字幕を文言・タイミング・見た目まで調整したい利用者は、同じ job の元動画、`narration.wav`、SRT を Resolve 経路で使う。Resolve アダプターは preview.mp4 を素材として import しない。

### 3.2 配置入力

新経路が受け入れるのは `status: succeeded` の次のジョブだけである。

| モード | V1 | A1 | 必須成果物 |
|---|---|---|---|
| `transcribe` | manifest の元動画 | 同じ元動画の音声 | `subtitles.srt`、`subtitles.vtt`、`transcript.txt` |
| `narrate` | manifest の元動画 | `outputs/narration.wav` | `outputs/narration.wav`、`subtitles.srt`、`subtitles.vtt` |

アダプターは manifest の全 input fingerprint を再計算する。成果物は manifest の記録から、相対パス containment、size、SHA-256、通常ファイルであることを検証する。`transcribe` は元入力に Resolve が読み込める映像・音声が必要である。`narrate` は映像入力と `narration.wav` が必要で、元動画の音声は配置しない。検証失敗は bin/timeline を作る前に安定した adapter error で停止する。

### 3.3 新規 timeline と bin

各適用 attempt は `_MinoruStudio <job-name> Resolve <attempt-id>` の専用 bin を作る。新規 timeline の基底名は `<job-name> Resolve` とし、既存名と衝突すると `-002`、`-003` を付ける。既存 timeline と bin を選択、再利用、上書き、削除しない。

timeline を作った後、開始位置 0 に次を配置する。

- V1: 元動画を `mediaType=1` で配置する。
- A1: `transcribe` は同じ元動画を `mediaType=2` で、`narrate` は `narration.wav` を `mediaType=2` で配置する。

gateway は import 結果の path と item ID を確認し、append の成功、track index、record frame、timeline ID/name/frame rate を attempt detail に記録する。必要な V1/A1 配置がいずれか失敗すれば attempt を `failed` にし、既に作成した timeline/bin を自動削除しない。

## 4. 字幕手動インポート checkpoint

音声・映像の配置が成功した attempt は `awaiting_subtitle_import` となる。Resolve Utility は、新しい timeline 名と検証済み `subtitles.srt` の絶対パスを表示し、Resolve 標準 UI で当該 SRT を字幕トラックへインポートするよう案内する。

ユーザーが字幕を読み込んだ後、同じ Utility で「字幕読み込みを確認」を実行する。gateway は記録済み timeline ID が現在のプロジェクトに存在すること、字幕 track 数が1以上であることを確認する。API では cue 本文を再読できないため、SRT の事前 fingerprint とユーザー確認を併せて記録する。確認後だけ attempt を `applied` にする。

字幕トラックがない、timeline が見つからない、またはユーザーが確認を取り消した場合は `awaiting_subtitle_import` を維持する。SRT を再認識・再生成・変更しない。

## 5. 状態、再開、非破壊性

media-placement attempt の状態は `staging`、`ready`、`awaiting_subtitle_import`、`applied`、`failed` とする。`ready` までは安全に再開できる。`awaiting_subtitle_import` では同じ timeline ID と SRT fingerprint を再検証し、字幕確認だけを再開する。terminal attempt を再適用するには明示的な「新しい適用を開始」を要求し、別の bin/timeline を作る。

アダプターの実行中に残った operation token は次回に `failed` として扱う。元動画、台本、音声、字幕、成功済み成果物、既存 Resolve objects は削除しない。job lock、attempt ownership、application detail の原子的更新は既存方式を使う。

## 6. UI と記録

UI は mode ごとに固定の安全な文言だけを表示する。`transcribe`／`narrate` の確認ダイアログにはジョブ名、モード、timeline 提案名、映像/音声/字幕の配置数、SRT path を示し、既存 timeline/bin を上書きしないことを明示する。台本、字幕、transcript の本文を UI の状態、application JSON、ログ、例外へ複製しない。

attempt detail には mode、job ID、project ID/name、Resolve product/version、bin ID/name、timeline ID/name/frame rate、V1/A1 item IDs、SRT artifact fingerprint、字幕 track 数、状態、時刻、安定したエラーだけを記録する。

## 7. 機械検証

Fake Resolve を拡張し、次を検証する。

1. `beat-sync` の既存 adapter suite が変わらず通る。
2. `transcribe`／`narrate` の manifest・input・artifact hash 改ざん、外部パス、未成功 job、必要成果物不足を Resolve call 前に拒否する。
3. 各モードが専用 bin、新規 V1/A1 timeline、固有名、正しい source/audio を作る。
4. 字幕 checkpoint が subtitle track 不在で進まず、track 検出と明示確認でだけ `applied` になる。
5. キャンセル、途中失敗、operation token 残留、terminal attempt、新しい attempt、`-002` 命名、sentinel timeline 非変更を確認する。
6. Windows の空白・日本語・長い path、24/30/60/23.976/29.97 fps、既存 `transcribe`／`narrate` の CLI/GUI を回帰する。

実機 VOICEVOX、FFmpeg メディア処理、Resolve プロジェクトへの操作は、この機械検証を通過し、別途明示承認された受入実行時だけ行う。

## 8. 一括実機受入

実機受入は同じ日・同じ破棄可能 workspace で、次の順に行う。各前提または安全条件が失敗したらそこで停止し、後段を実行しない。

1. `doctor --json`、Adapter install、VOICEVOX の loopback availability、Resolve version を記録する。
2. Git ignored の破棄可能な日本語短尺動画を使い、`script-draft` で代表フレームと `script.md` テンプレートを確認する。
3. 人が記入した短い日本語ナレーションを用い、`narrate -Preview` で ずんだもん／ノーマルの実音声、WAV、SRT/VTT、preview を確認する。元動画 hash は前後で一致させる。
4. 同じか別の破棄可能な短尺動画で、既存 `transcribe` 実機受入に準じた成功済みジョブを用意する。
5. 新規ローカル Resolve プロジェクトに sentinel timeline を作り、transcribe と narrate の各ジョブを別 timeline へ適用する。SRT を Resolve 標準 UI で読み込み、字幕 track、V1/A1、timeline 名、入力不変を確認する。
6. 各適用を1回ずつ新しい attempt として再適用し、`-002` の新 timeline、sentinel 非変更、既存 timeline/bin 非上書きを確認する。

受入記録は job path、台本本文、字幕本文、音声データを含めない。内容非記録の hash、size、duration、artifact kind、状態、attempt/timeline ID/name、frame rate、exit code、失敗 category だけを記録する。実機受入結果は `docs/resolve-transcribe-narrate-acceptance.md` に、`script-draft`／`narrate` の結果も同じ consolidated record に追記する。

## 9. 受入条件

- 既存 beat-sync の実機受入と全 adapter テストが維持される。
- 2つの成功済みジョブが別々の新規 Resolve timeline へ V1/A1 として配置される。
- `transcribe` の A1 は元動画音声、`narrate` の A1 は生成済み narration.wav だけである。
- 各 timeline は user-imported SRT を含む字幕 track を持ち、字幕を Resolve 内で編集できる。
- input/manifest/artifact 改ざん、subtitle track 不在、既存 timeline 名衝突、途中失敗は非破壊に停止または再開する。
- `script-draft` の代表フレームとテンプレート、`narrate` の ずんだもん実音声・SRT/VTT・preview、全 adapter テスト、最後の全自動 suite、doctor が確認済みである。
- `transcribe -Preview` と `narrate -Preview` の preview.mp4 は、それぞれ Resolve を起動せずに映像・選択された音声・焼き込み字幕を再生でき、入力 hash を変えない。
