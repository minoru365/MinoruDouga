# MinoruStudio

MinoruStudioは、音ハメ・文字起こし・読み上げ(ナレーション)を扱う
Windows向けローカル動画制作ツールです。MinoruDouga(音ハメ単機能)の後継として、
その機能を包含しています。

- **音ハメ(beat-sync)** — BGMを解析し、静止画・動画をビートに合わせてカットした
  タイムラインを DaVinci Resolve 上に自動生成
- **文字起こし(transcribe)** — ローカルの FFmpeg + faster-whisper で字幕
  (`srt` / `vtt`)と字幕付きプレビューを生成。外部サービスへの送信なし
- **読み上げ(narrate)** — 人が承認した台本から、ローカル VOICEVOX で
  ナレーション音声と字幕を生成。静止画、動画、両者を混在させた構成にも対応

成果物は2通りに使えます。字幕・ナレーションを合成済みの `preview.mp4` を
**そのまま出力して完結**させるか、動画編集ソフト
[DaVinci Resolve](https://www.blackmagicdesign.com/jp/products/davinciresolve)
(無償版で動作)へ**通常のタイムラインとして適用**して細部を微修正するかを
選べます。各処理は入力と結果を `.media-job` として確定させ、Resolve適用時も
既存タイムラインは上書きしません。

## デモ

編集済みの動画と台本を渡すと、VOICEVOXナレーションと字幕を合成した
`preview.mp4` が生成されます(`narrate -Preview`)。

![narrateデモ: 台本の1文目が字幕として合成される](img/demo-subtitle-fish.gif)

![narrateデモ: シーンに合わせた字幕が表示される](img/demo-subtitle-anemone.gif)

ナレーション音声付きの完全版(25秒):
[img/demo-narrate.mp4](img/demo-narrate.mp4)
(デモのナレーション音声: VOICEVOX:ずんだもん)

### DaVinci Resolveで微修正する

`preview.mp4` で完結させる代わりに、ジョブを DaVinci Resolve へ適用すると
通常のタイムラインが生成されます。字幕は字幕トラックのクリップになるため、
文言・タイミング・見た目を Resolve の標準UIでそのまま編集できます。

![Resolveに適用したタイムライン。文字起こしの字幕が字幕トラックのクリップとして並び、標準UIで編集できる](img/demo-resolve-timeline.png)

## 開発環境

```powershell
uv sync --locked --dev
uv run pytest -q
```

## 最短手順

最初にadapterとResolve Utility launcherを配置します。

```powershell
.\install.ps1
```

1. 下記CLIまたは引数なしGUIで音ハメjobを準備する
2. Resolveで適用先プロジェクトを開く
3. **ワークスペース → スクリプト → MinoruStudio** を開く
4. `succeeded` の `.media-job` を選び、素材を取り込む
5. 動画がある場合はapplication binの動画へIn/Outを設定し、MinoruStudioを再実行する
6. 指示された場合だけResolveの標準スチル長を変更し、再測定する
7. 最終確認を承認し、新規timelineのA1、V1、青markerを確認する

既存timelineは上書きせず、同名なら `-002` 以降の連番を付けます。処理記録は
各jobの `resolve/applications` と `logs/resolve.log` に保存されます。

## 音ハメjobを準備する

```powershell
$outputRoot = Join-Path $env:USERPROFILE 'Videos\MinoruStudio'

uv run minoru-studio beat-sync `
  -Music .\song.mp3 `
  -MediaDir .\media `
  -EveryN auto `
  -Order asc `
  -TimelineName "Beat Sync Demo" `
  -Name demo `
  -OutputDir $outputRoot
```

`-EveryN` は `auto` または1〜16、`-Order` は `asc` または `random` を
指定します。同名jobが存在する場合は連番の新規jobを作り、既存jobを
上書きしません。

成功すると `.media-job/outputs/beat-sync-plan.json` が作成されます。BGMの
長さ、ビート、カット位置はすべて非負の整数ミリ秒で保存されるため、CLIと
Resolve内アダプターの境界で小数秒の解釈差が生じません。

中断した準備は次のコマンドで再開できます。入力ファイルが変更されている
場合は安全のため停止します。

```powershell
uv run minoru-studio beat-sync resume (Join-Path $outputRoot 'demo.media-job')
```

## GUI

```powershell
uv run minoru-studio
```

引数なしでランチャーを開きます。BGM、素材フォルダ、カット間隔、並び順、
タイムライン名を指定すると、解析はバックグラウンドで実行されます。前回の
入力値は `%APPDATA%\MinoruStudio\config.json` に保存されますが、許可された
画面項目以外は保存しません。

## 文字起こし

文字起こしはサポート対象のローカル workflow です。ローカルの FFmpeg と
faster-whisper を使い、入力メディア・ジョブ・生成物を外部サービスへ送信しません。
初回に model が cache にない場合も、`-AllowModelDownload` を明示しない限り
download は開始しません。

```powershell
$outputRoot = Join-Path $env:USERPROFILE 'Videos\MinoruStudio'

uv run minoru-studio transcribe .\sample.mp4 `
  -Name demo-transcribe `
  -OutputDir $outputRoot `
  -Preview

# 未キャッシュ model を使う場合だけ、利用者が明示して追加する
uv run minoru-studio transcribe .\sample.mp4 `
  -Name demo-transcribe `
  -OutputDir $outputRoot `
  -Preview `
  -AllowModelDownload

uv run minoru-studio transcribe resume (Join-Path $outputRoot 'demo-transcribe.media-job')
```

引数なし GUI (`uv run minoru-studio`) ではモードを `transcribe` にして、入力動画・
音声、model(`small` / `medium`)、language、音量正規化、ノイズ軽減、字幕付き
preview を選択します。失敗または中断した job は同じ画面で開いて再開できます。

成功すると `.media-job/outputs/` に `transcript.txt`、`subtitles.srt`、
`subtitles.vtt`、および video 入力で要求した場合の `preview.mp4` が作られます。
model cache は既定で `%LOCALAPPDATA%\MinoruStudio\models\faster-whisper` に置かれます。
入力は job manifest の fingerprint と照合し、変更されていれば再開を拒否します。
audio-only 入力には `-Preview` を指定できず、推論前に停止します。

環境を確認するには次を実行します。`doctor` は FFmpeg、FFprobe、faster-whisper のほか、
Python、PowerShell、uv も確認します。

```powershell
uv run minoru-studio transcribe --help
uv run minoru-studio doctor --json
```

## 読み上げ(narrate)

人が承認した台本から、ローカル VOICEVOX でナレーション音声と字幕を生成します。
VOICEVOX は利用者が事前に起動しておくローカル HTTP API(`127.0.0.1:50021`)だけを
使用し、MinoruStudio と Resolve アダプターが VOICEVOX を起動することはありません。
外部サービスへの送信も行いません。

この仕様は次の3原則に基づいています。

- **内容は人が決める** — 台本の自動リライト・短縮・話速調整はしない。
  ナレーションが動画より長くても警告するだけで、自動では縮めない
- **データは外に出さない** — 私的な素材を扱う前提のため、処理はローカルで完結する
- **ツールは勝手なことをしない** — VOICEVOX の自動起動・自動インストールはせず、
  未起動なら起動方法を案内して停止する

```powershell
uv run minoru-studio narrate .\sample.mp4 `
  -Script .\approved-script.txt `
  -Name demo-narrate `
  -Preview

uv run minoru-studio narrate resume (Join-Path $env:USERPROFILE 'Videos\MinoruStudio\demo-narrate.media-job')
```

成功すると `.media-job/outputs/` に `narration.wav`、`subtitles.srt`、
`subtitles.vtt`、および video 入力で `-Preview` を指定した場合の `preview.mp4` が
作られます。

### 静止画と、静止画・動画の組み合わせ

1枚の静止画は動画と同じ指定方法です。ナレーションが終わるまで表示します。

```powershell
uv run minoru-studio narrate .\picture.png -Script .\approved-script.txt -Name picture -Preview
```

複数素材は、素材ごとに台詞を明記した構成JSONを渡します。ファイル名と台本の
章番号から対応を推測する処理はありません。`clips` の記載順に再生するので、
ファイル名の昇順で使いたい場合は、その順に記載してください。

```json
{
  "version": 1,
  "clips": [
    {"id": "doorbell", "kind": "image", "source": "33.png", "narration": "チャイムが鳴りました。"},
    {"id": "delivery", "kind": "image", "source": "34.png", "narration": "荷物が届きました。二人は喜んでいます。"},
    {"id": "opening", "kind": "video", "source": "opening.mp4", "narration": "箱を開けてみましょう。", "trim_start_ms": 1000, "trim_end_ms": 4000}
  ]
}
```

```powershell
uv run minoru-studio narrate .\storyboard.json -Name story -Preview
```

- 構成JSONでは台詞が内蔵されているので `-Script` は指定しません。GUIでも入力に
  JSONを選び、台本欄を空にして実行できます。
- `source` の相対パスは構成JSONがあるフォルダ基準です。画像は PNG/JPEG/BMP/
  WebP/TIFF、動画は MP4/MOV/MKV/AVI/WebM/M4V を指定できます。
- 各 `id` は一意にします。同じ画像を複数の項目で再利用したり、1枚に複数の文を
  割り当てたりできます。章の途中で絵を変える場合は、台詞を項目に分けます。
- 切替時刻は実際に生成した音声と発話間の300msから計算します。完成プレビューで
  絵と台詞の内容も確認してください。総尺や枚数の一致だけでは対応を保証できません。
- 新しい画像／混在プレビューは1280×720・30fpsです。縦横比を保持して余白を付けます。
  動画は指定区間を先頭から使い、音声より短ければ最終フレームを保持し、長ければ
  ナレーション区間の長さでカットします。元動画の音声は使用しません。
- この新しい入力形式のResolveへの直接適用は対象外です。MP4・WAV・字幕を単独で
  利用できます。従来の動画入力のResolve適用は変わりません。

#### 構成JSONのBGM

構成JSONに `music` を書くと、`preview.mp4` にBGMを重ねます。場面ごとの曲は
クリップの `music` で明示し、省略したクリップは `default` の曲になります。

```json
{
  "version": 1,
  "music": {
    "tracks": [
      {"id": "normal", "source": "normal.mp3", "gain_db": -24},
      {"id": "battle", "source": "battle.mp3", "gain_db": -29}
    ],
    "default": "normal",
    "crossfade_ms": 1500
  },
  "clips": [
    {"id": "calm", "kind": "image", "source": "01.png", "narration": "穏やかな場面です。"},
    {"id": "fight", "kind": "image", "source": "02.png", "narration": "戦いが始まりました。", "music": "battle"}
  ]
}
```

- BGMは `-Preview` 指定時だけ使えます。`narration.wav` と字幕はナレーションのみの
  ままなので、別の編集ソフトでも使えます。
- 曲はWAV/FLAC/MP3/OGG/M4A/AACで、最大8曲です。`gain_db` は -40〜0(省略時 -18)。
  曲ごとに元の音量が違うので、ナレーションを聞きながら調整してください。
- 同じ曲が続くクリップは1区間になり、区間の最初から再生します。区間より曲が短ければ
  ループし、別の曲から戻った場合も最初から再生します。
- 曲の切り替えは境界を中心に `crossfade_ms`(0〜5000、省略時1500)で重ね、最後は
  2秒でフェードアウトします。ナレーションの長さやタイミングは変わりません。

### 成果物と公開リポジトリ

`narrate` の `-OutputDir` 省略時とGUIの初期保存先は、ユーザーの
`Videos\MinoruStudio` です。成果物・台本・構成JSON・確認画像はリポジトリ外へ
保存してください。`-OutputDir` で別の保存先も明示できます。

新しく作る `.media-job` は、個人パスを含むJSON、台本、ログ、中間画像も含めて
ジョブ全体をGit除外する `.gitignore` を持ちます。このリポジトリ側でもジョブ・
出力フォルダを除外します。除外は既に追跡済みのファイルには効かないため、公開前に
`git status` と差分を確認してください。既存の成果物を自動で移動・削除はしません。

## Resolveで文字起こし・読み上げを適用する

単独で視聴・配布できる成果物が目的なら、`-Preview`(または GUI の preview 選択)で
`preview.mp4` を作れば完結します。この経路に Resolve は不要です。字幕の文言・
タイミング・見た目まで編集したい場合だけ、次の任意編集経路を使います。

1. **ワークスペース → スクリプト → MinoruStudio** で、成功済みの video
   `transcribe` / `narrate` ジョブを選択します。
2. 配置内容はモードごとに固定です。`transcribe` は V1=元動画、A1=同じ元動画の
   音声。`narrate` は V1=元動画、A1=`outputs/narration.wav`。
3. Utility は `<job name> Resolve`(既存名と衝突する場合は `-002`)の新規
   タイムラインを作ります。既存タイムラインのレンダー・置換・削除は行いません。
4. 案内に表示される検証済み `outputs/subtitles.srt` を Resolve 標準UIで字幕
   トラックへ手動インポートし、編集可能なことを確認したら、Utility を再実行して
   `字幕読み込みを確認` を押します。
5. アダプターは字幕の自動生成(`CreateSubtitlesFromAudio` 等)を行わず、
   `preview.mp4` を配置素材として使いません。

## 基盤コマンド

```powershell
uv run minoru-studio --version
uv run minoru-studio doctor --json
$outputRoot = Join-Path $env:USERPROFILE 'Videos\MinoruStudio'
uv run minoru-studio jobs create -Mode beat-sync -Name demo -OutputDir $outputRoot
uv run minoru-studio jobs inspect (Join-Path $outputRoot 'demo.media-job')
uv run minoru-studio
```

`jobs create` は各固定モードの空の `pending` jobを作る基盤コマンドです。
音ハメ準備には上記の専用 `beat-sync` コマンドまたはGUIを使用してください。

Resolveへ適用するときは、準備済みjobを選んで必ず最終確認を承認します。
途中のIn/Out設定やスチル長変更が必要な場合は状態をjobへ保存し、次のUtility
呼び出しから再開します。

## ライセンス

[MIT License](LICENSE)。直接依存の faster-whisper(MIT)と librosa(ISC)は
いずれも寛容型ライセンスです。

FFmpeg・VOICEVOX・DaVinci Resolve は利用者が各自インストールする外部
ソフトウェアで、本リポジトリには同梱していません。VOICEVOX で生成した
音声を公開する場合は、[VOICEVOX 利用規約](https://voicevox.hiroshiba.jp/term/)
に従い「VOICEVOX:キャラクター名」のクレジット表記と、各音声ライブラリの
規約の確認が必要です。
