# ローカル音声・字幕付き動画の自動化ツール設計

## 1. ステータス

- 本書は設計のみを対象とする。
- ツール実装、依存関係の追加、VOICEVOX・Voicemeeter等のインストール、クラウドAPI呼び出しは本書の作成範囲に含めない。
- 本書は動画関連資料としてMinoruDougaの `docs/` に保存するが、MinoruDouga本体への統合設計ではない。
- 実装する場合も moneyplanner や MinoruDouga の本体には組み込まず、複数プロジェクトから利用できる個人用ローカルツールとして配置する。
- MinoruDougaの `src/`、`scripts/`、Resolve連携、依存関係、インストーラーは変更対象外とする。

## 2. 目的

動画に対する次の作業を、PowerShellコマンドから再現可能な形で自動化する。

1. 既存音声の文字起こしと字幕生成
2. 台本からの読み上げ音声と字幕生成
3. 画面録画からの台本案生成
4. 音声の調整、動画への結合、字幕焼き込み
5. 必要に応じたVoicemodによる声質変換

元動画は常に保持し、編集可能な字幕ファイルと、そのまま共有できる字幕付きMP4の両方を出力する。

## 3. 採用アーキテクチャ

PowerShellを利用者向けCLIとし、FFmpegと隔離されたPython処理を呼び出すハイブリッド構成を採用する。

- エントリポイント: `~/.local/bin/media-auto.ps1`
- ツール本体: `~/.local/share/media-auto/`
- Python環境: `uv` 管理のPython 3.12
- 動画・音声処理: FFmpeg / FFprobe
- ローカル音声認識: faster-whisper、CPU、INT8
- ローカル読み上げ: VOICEVOX Engine
- 任意クラウド処理: プロバイダーアダプター経由。初期候補はOpenAI
- 任意声質変換: Voicemod Desktop + Control API + 仮想オーディオ経路

Pythonをシステム環境へ直接インストールせず、ツール専用環境へ固定する。モデル、音声エンジン、クラウドサービスは共通インターフェースの背後に置き、CLIや動画処理から分離する。

## 4. CLI

### 4.1 文字起こし

```powershell
media-auto transcribe input.mp4
```

処理内容:

1. FFmpegで音声トラックを抽出する。
2. 指定時のみ保守的な音量正規化とノイズ低減を行う。
3. faster-whisperで文字起こし、無音検出、タイムスタンプ生成を行う。
4. TXT、SRT、VTTを生成する。
5. SRTを焼き込んだMP4を生成する。

既定モデルはCPU負荷と日本語精度の均衡から `small` とし、`-Model medium` を選択可能にする。

### 4.2 台本からの読み上げ

```powershell
media-auto narrate input.mp4 -Script script.txt
```

処理内容:

1. 台本を句読点と最大文字数で発話単位に分割する。
2. 読み上げプロバイダーで発話単位ごとのWAVを生成する。
3. FFprobeで各WAVの実時間を取得してSRTを組み立てる。
4. 発話を連結し、動画の音声トラックへ結合する。
5. SRTと字幕焼き込みMP4を出力する。

既定の読み上げプロバイダーはローカルのVOICEVOXとする。クラウド読み上げとVoicemodは明示指定時のみ使用する。

### 4.3 画面録画からの台本案生成

```powershell
media-auto auto input.mp4 -Cloud -ScriptProvider openai
```

処理内容:

1. FFmpegでシーン変化と時間間隔を基に代表フレームを抽出する。
2. 送信対象フレーム、時刻、解像度を `manifest.json` に記録する。
3. クラウド利用が明示された場合のみ、代表フレームを台本生成プロバイダーへ送る。
4. 生成した `script.txt` を保存し、読み上げ処理へ渡す。

初期版ではローカル画像モデルを同梱しない。`-Cloud` がない場合は代表フレームと台本テンプレートを生成して終了し、ユーザーまたはCodexが台本を補完できる状態にする。

## 5. 出力

既定では入力動画と同じ場所に `<元ファイル名>.media-auto` ディレクトリを作る。`-OutputDir` で変更できる。

```text
<name>.media-auto/
  manifest.json
  script.txt
  transcript.txt
  subtitles.srt
  subtitles.vtt
  narration.wav
  captioned.mp4
  work/
  logs/
```

- 元動画は上書きしない。
- 既存の出力がある場合は停止し、`-Force` 指定時だけ置き換える。
- `work/` は成功時に削除可能とし、失敗時は診断と再開のため保持する。
- `manifest.json` には入力ハッシュ、処理日時、使用プロバイダー、モデル、FFmpeg引数、出力ファイルを記録する。APIキーや個人情報は記録しない。

## 6. 音声・字幕プロバイダー

### 6.1 faster-whisper

- 既定はローカルCPUのINT8。
- VADで長い無音を除外する。
- 単語またはセグメントのタイムスタンプからSRT/VTTを生成する。
- 初回モデル取得にはネットワーク通信が発生するため、実行前にサイズと保存先を表示する。

### 6.2 VOICEVOX

- 日本語の既定読み上げエンジンとする。
- ローカルHTTP APIの `/audio_query` と `/synthesis` を利用する。
- 話者、スタイル、話速をCLIオプションで指定可能にする。
- エンジンが起動していない場合は、自動で別プロバイダーへ切り替えず、起動方法を示して停止する。

### 6.3 クラウドプロバイダー

- `-Cloud` とプロバイダー指定の両方がある場合だけ利用する。
- APIキーは環境変数から読み、設定ファイルやログへ保存しない。
- 台本生成では動画全体ではなく、`manifest.json` に列挙された代表フレームだけを送る。
- 音声認識、台本生成、読み上げを個別に選択できるようにし、一つのクラウドサービスへ固定しない。

## 7. Voicemodの位置づけ

### 7.1 採用判断

Voicemodは既定の読み上げエンジンではなく、生成済み音声へキャラクター性や声色を加える任意の後処理として採用する。

Voicemodの公式Control APIは、デスクトップアプリの声一覧取得、声の選択、パラメーター変更、Voice Changerの有効化などをWebSocket経由で制御できる。一方、音声ファイルを直接送信して変換済みファイルを受け取るバッチAPIは公式ドキュメントで確認できない。

### 7.2 処理経路

```text
VOICEVOXまたはクラウドTTS
  -> narration-source.wav
  -> 仮想オーディオ入力
  -> Voicemod Desktop
  -> Voicemod Virtual Microphone
  -> FFmpegで録音
  -> narration-voicemod.wav
```

Control APIは次の制御だけを担当する。

- Voicemodアプリへのクライアント登録
- 利用可能な声の取得
- 指定した声の選択
- Voice Changerの状態確認と有効化
- 選択中の声と処理結果のメタデータ記録

音声ファイルの再生・ルーティングにはVoicemeeter等の仮想オーディオ経路が必要になる。変換後音声はFFmpegでVoicemod Virtual Microphoneから録音する。

### 7.3 制約

- Voicemod Desktopが起動し、ログイン済みである必要がある。
- Control APIキーと、接続後最初の `registerClient` メッセージが必要である。
- 仮想デバイス名は環境依存であり、初回セットアップが必要になる。
- ファイル処理は実時間再生になるため、VOICEVOXや通常のFFmpeg処理より遅い。
- 音声デバイス競合、先頭・末尾の無音、録音開始の同期ずれが起き得る。
- Voicemod公式のオンラインTTSには公開された自動化APIが確認できないため、初期版の直接連携対象にはしない。

このため `-VoiceProvider voicemod` は実験的機能として明示的に選ぶ。失敗時にVOICEVOXへ黙って切り替えず、元のTTS音声を保持してエラーを返す。

## 8. プライバシーと安全性

- 既定は完全ローカル処理とする。
- クラウド送信前に対象ファイルと代表フレーム数を表示する。
- `-DryRun` で送信予定とFFmpeg処理だけを確認できるようにする。
- 連絡先、メールアドレス、認証情報などが映る可能性を警告する。
- 元動画、台本、字幕を自動削除しない。
- APIキー、Voicemod認証情報、個人情報をログへ出さない。

## 9. エラー処理

実行開始時にFFmpeg、FFprobe、Python環境、選択プロバイダーを検査する。必要なプロバイダーが利用できない場合は、代替サービスを自動選択せずに終了する。

各段階は `manifest.json` に状態を記録し、再実行時に入力ハッシュと設定が一致する完了済み段階を再利用できるようにする。外部プロセスの終了コードが0でない場合、最終MP4を成功扱いにしない。

## 10. 検証方針

### 自動検証

- CLI引数とプロバイダー選択
- 台本分割とSRT時刻の単調増加
- 既存出力の上書き防止
- `manifest.json` への秘密情報非出力
- クラウドプロバイダーのモック
- Voicemod Control API WebSocketのモック
- FFprobeによる映像・音声ストリーム、長さ、出力容量の検査

### ローカル統合検証

- 短い合成動画で文字起こし、SRT、字幕焼き込みを確認する。
- VOICEVOXで日本語読み上げ、発話単位の字幕同期を確認する。
- App Review用の18秒動画を使い、元動画を変更せず成果物を生成できることを確認する。
- Voicemodは別ゲートとし、アプリ起動、声選択、仮想経路、録音同期を手動確認する。

## 11. 受入条件

- PowerShellから3モードを明示的に実行できる。
- 既定処理では動画、音声、画像を外部へ送らない。
- SRTと字幕焼き込みMP4の両方が生成される。
- 元動画を変更または削除しない。
- VOICEVOX、クラウド、Voicemodを明示的に切り替えられる。
- プロバイダー障害時に黙ったフォールバックを行わない。
- Voicemodを使用しない基本経路は仮想オーディオデバイスに依存しない。
- すべての外部プロセス終了コードとFFprobe検査が成功した場合だけ完了扱いにする。

## 12. 残余リスクと実装順序

推奨実装順序は次のとおり。

1. FFmpegによる字幕生成・焼き込み
2. faster-whisperによるローカル文字起こし
3. VOICEVOXによる台本読み上げ
4. 明示的クラウド台本生成
5. Voicemod実験アダプター

Voicemod経路は仮想オーディオと実時間録音に依存するため、基本経路の安定後に分離して追加する。最大の残余リスクはWindows音声デバイス名と録音同期の環境差である。

## 13. 公式資料

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [FFmpeg documentation](https://www.ffmpeg.org/ffmpeg.html)
- [VOICEVOX Engine](https://github.com/VOICEVOX/voicevox_engine)
- [VOICEVOX Engine API](https://voicevox.github.io/voicevox_engine/api/)
- [Voicemod Control API](https://control-api.voicemod.net/)
- [Voicemod Control API reference](https://control-api.voicemod.net/api-reference/)
- [Voicemodで音声ファイルを加工する公式手順](https://support.voicemod.net/hc/en-us/articles/360013375380-How-to-modify-an-audio-file-Voicemeeter-Banana)
- [Voicemod録音の公式手順](https://support.voicemod.net/hc/en-us/articles/360013375460-How-to-record-your-own-voice-effects)
- [OpenAI API models](https://developers.openai.com/api/docs/models)
- [OpenAI Audio API](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create)
