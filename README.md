# MinoruStudio

MinoruStudioは、MinoruDougaの音ハメを包含し、字幕、読み上げ、リポジトリの
デモ動画制作などを段階的に扱うWindows向けローカル制作ツールです。

現在は音ハメの準備工程まで実装しています。BGMを解析し、静止画・動画の
順序とカット位置を確定した `.media-job` を作成できます。DaVinci Resolveへ
実際にタイムラインを作る処理は次段階のResolve内アダプターで追加します。

## 開発環境

```powershell
uv sync --locked --dev
uv run pytest -q
```

## 音ハメjobを準備する

```powershell
uv run minoru-studio beat-sync `
  -Music .\song.mp3 `
  -MediaDir .\media `
  -EveryN auto `
  -Order asc `
  -TimelineName "Beat Sync Demo" `
  -Name demo `
  -OutputDir .\jobs
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
uv run minoru-studio beat-sync resume .\jobs\demo.media-job
```

## GUI

```powershell
uv run minoru-studio
```

引数なしでランチャーを開きます。BGM、素材フォルダ、カット間隔、並び順、
タイムライン名を指定すると、解析はバックグラウンドで実行されます。前回の
入力値は `%APPDATA%\MinoruStudio\config.json` に保存されますが、許可された
画面項目以外は保存しません。

## 基盤コマンド

```powershell
uv run minoru-studio --version
uv run minoru-studio doctor --json
uv run minoru-studio jobs create -Mode beat-sync -Name demo -OutputDir .\jobs
uv run minoru-studio jobs inspect .\jobs\demo.media-job
uv run minoru-studio
```

`jobs create` は各固定モードの空の `pending` jobを作る基盤コマンドです。
音ハメ準備には上記の専用 `beat-sync` コマンドまたはGUIを使用してください。

現段階のMinoruStudioはResolveプロジェクトを変更しません。実際の
タイムライン生成が必要な場合は、下記の既存MinoruDougaをフォールバックとして
使用してください。

---

# MinoruDouga（Resolve実適用のフォールバック） 🎵🎬

写真・動画・音楽を渡すと、曲のビートに合わせてテンポよくカットした
タイムラインを DaVinci Resolve 上に自動生成するツール。
生成後は普通のタイムラインなので、そのまま Resolve で微調整できる。

![MinoruDouga の実行画面](img/editsample.png)

> 設定ダイアログで音楽・素材フォルダ・カット間隔を指定すると、ビートに
> 合わせてカットされたタイムラインが生成される(各カット位置に青マーカー)。

## 仕組み

```
音楽ファイル ──→ analyze_beats.py (librosa でビート解析・システム Python)
                        │ beats.json
                        ▼
Resolve スクリプトメニュー ──→ minoru_douga.py
   ├ 素材フォルダの写真・動画をメディアプールにインポート
   ├ 拍位置でカット境界を計算(N拍ごと)
   ├ V1 に素材を順番/ランダムに配置(動画は使用箇所を自動でずらす)
   ├ A1 に音楽を配置
   └ 各カット位置にマーカーを追加
```

無償版 Resolve でも動く(外部からの API 操作ではなく、Resolve 内の
スクリプトメニューから実行する方式のため)。

## セットアップ

```powershell
.\install.ps1
```

これで依存ライブラリのインストールと、ランチャー
(`scripts\MinoruDouga.py`)の Resolve スクリプトフォルダへの配置が行われる。

## 使い方

1. 素材(写真・動画)を 1 つのフォルダにまとめる
2. Resolve でプロジェクトを開く
3. メニュー **ワークスペース → スクリプト → MinoruDouga**
4. ダイアログで音楽ファイル・素材フォルダ・カット間隔(N拍ごと)を指定して「タイムライン生成」

ログは **ワークスペース → コンソール** に出る。

## オプション

| 項目 | 説明 |
|---|---|
| カット間隔 | 何拍ごとに切り替えるか。「自動」は全素材がほぼ一巡する間隔を計算 |
| 並び順 | ファイル名 昇順 / ランダム |

## 動画のハイライト指定

使ってほしい場面がある動画は、実行前に **メディアプールでダブルクリック →
ソースビューアでその場面の頭に In 点(`I`)を打つだけ**でよい。
長さはスクリプトが拍数に合わせて自動で決めるので Out 点は不要。
Out 点(`O`)も打った場合は「この範囲の中だけを使う」という制限になる。
未指定の動画は全体から順繰りに使われる。

設定は `%APPDATA%\MinoruDouga\settings.json` に記憶される。

## 注意・既知の制限

- **対応音楽形式**: wav / flac / mp3 / ogg。m4a・aac は ffmpeg が必要
  (`winget install Gyan.FFmpeg`)
- **写真の表示時間**: Resolve の環境設定「標準スチルの長さ」(環境設定 →
  ユーザー → 編集 → 一般設定)がスロット長より短いと写真を引き伸ばせない。
  実行時に自動チェックし、足りない場合は変更手順をダイアログで案内する
- **Resolve の Python**: Resolve はシステムの Python 3 を使う。メニューに
  スクリプトが出ない・動かない場合は Resolve が Python を認識しているか確認
  (Preferences → System → General の Script 設定)
- クリップが毎回 1 フレームずれる場合は `src/minoru_douga.py` の
  `END_FRAME_INCLUSIVE` を反転させる

## 開発メモ

- 本体: [src/minoru_douga.py](src/minoru_douga.py) — ランチャー経由で毎回 reload されるので、編集が即反映される
- ビート解析: [src/analyze_beats.py](src/analyze_beats.py) — 単体でも実行可能
- 設計の全体像と判断の背景は [ARCHITECTURE.md](ARCHITECTURE.md) を参照
- 今後の拡張候補: 曲の盛り上がり(RMS/オンセット強度)に応じた緩急カット、
  ハイライト区間の自動検出、トランジション自動挿入

## ライセンス

[MIT License](LICENSE)。依存ライブラリ(numpy / soundfile = BSD 3-Clause、
librosa = ISC)はいずれも寛容型ライセンス。
