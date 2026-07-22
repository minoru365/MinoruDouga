# MinoruStudio コーディングエージェント引き継ぎ

次の実装を担当するコーディングエージェントの入口である。製品要件や既存モードの
詳細はここへ複製せず、下記の正本を再利用する。

## まず読むもの

| 確認したいこと | 正本 |
|---|---|
| 製品全体、固定モード、プライバシー、検証方針、将来順序 | [`media-automation-design.md`](media-automation-design.md) の 7、10、11、13、15、17章 |
| 現在の導入方法と既存モードの使い方 | [`../README.md`](../README.md) |
| `beat-sync` の実Resolve受入記録 | [`resolve-beat-sync-acceptance.md`](resolve-beat-sync-acceptance.md) |
| `transcribe` の実機受入記録 | [`transcribe-acceptance.md`](transcribe-acceptance.md) |
| 完了済みフェーズの詳細な判断・実装経緯 | [`superpowers/specs/`](superpowers/specs/) と [`superpowers/plans/`](superpowers/plans/) |

この文書と上表の資料が矛盾する場合は、上表の正本を優先する。要件を変える必要が
ある場合は、実装を始めずに新しいフェーズ専用specを作って承認を受ける。

## 現在地

`main` には `0.3.0` が統合済みである。

| 領域 | 状態 | 参照先 |
|---|---|---|
| 共通基盤、CLI、GUI、ジョブ、ログ、再開、doctor | 完了 | README、foundation plan |
| `beat-sync` とResolveアダプター | 完了 | Resolve受入記録 |
| `transcribe` のローカル文字起こし、TXT/SRT/VTT、任意preview | 完了 | 文字起こし受入記録 |
| `script-draft` | 実装・機械確認済み。実動画のまとめて受入待ち | 総合設計 7.4 |
| `narrate` | 実装・機械確認済み。VOICEVOX実音声・実previewのまとめて受入待ち | 総合設計 7.3、11.2 |
| `repo-demo` のローカル工程 | 未実装 | 総合設計 7.5 |
| `repo-demo -AI` | 未実装 | 総合設計 7.5、11.3 |
| デモ組み立て | 未実装 | 総合設計 7.5 |
| `transcribe` / `narrate` / `repo-demo` のResolve適用 | 未実装。最後に実施 | 総合設計 10.3 |

## 実装順序と担当境界

| 順序 | フェーズ | この担当が完了させる範囲 | 次フェーズへ残すもの |
|---:|---|---|---|
| 1 | `script-draft` | ローカル動画からの代表フレーム・メタデータ・人が記入する台本テンプレート | AI台本化、Resolve適用 |
| 2 | `narrate` | 人が承認した台本からのローカル音声、字幕、任意preview | Resolve適用 |
| 3 | `repo-demo` ローカル工程 | 安全な候補化、概要、台本テンプレート、ショットリスト、`awaiting_capture` | Codex送信、自動録画、BGM、組み立て |
| 4 | `repo-demo -AI` | 許可済み一時バンドルからの根拠付き構造化デモ計画と人の承認待ち | 元リポジトリ編集、無確認送信、後工程 |
| 5 | デモ組み立て | 手動録画、承認済み台本、音声、字幕による確認用MP4 | Resolveタイムラインへの自動配置 |
| 6 | Resolve連携とまとめて受入 | 字幕・音声・デモ素材を含む編集可能タイムラインと実機受入 | 最終レンダー自動化 |

各フェーズの具体的な入出力、ジョブ状態、時間表現、上書き防止、プライバシー、
外部プロセス、失敗時の扱いは、実装前に必ず総合設計とフェーズ専用specで確定する。

## すべての担当に共通するガードレール

以下は既存の総合設計の実装時チェックリストである。詳細な根拠は
[`media-automation-design.md`](media-automation-design.md)を参照する。

- Windows専用・固定モード一つずつとし、任意工程を接続するワークフローエンジンは作らない。
- ジョブの時間は整数ミリ秒とし、入力、成功済み成果物、既存Resolveタイムラインを上書き・削除しない。
- `beat-sync`、`transcribe`、Resolveアダプターの既存挙動を変更しない。
- 外部送信は既定で禁止する。VOICEVOXはローカルHTTP APIだけ、Codex CLIは`-AI`と送信確認の両方を必須にする。
- OpenAI API、クラウド音声認識、クラウドTTS、Web自動操作、最終レンダー自動化は追加しない。
- リモートへのfetch、push、PR作成は明示依頼がある場合だけ行う。

ユーザーは、新モードの包括的な自動試験・実機受入・Resolve試験を最後にまとめて行う
方針である。ただし各フェーズでも、変更した契約を壊さない最小限の機械確認は行う。
フェーズ専用specには、最終まとめで追加・実行する試験と実機受入項目を明記し、
その実行前に受入済みとは主張しない。

## 次のエージェントへの依頼テンプレート

```text
MinoruStudioのフェーズ <script-draft / narrate / repo-demo-local / repo-demo-ai /
demo-assembly / resolve-final> を担当してください。

最初に docs/agent-handoff.md、docs/media-automation-design.md、README.md を読み、
このフェーズに関係する既存spec/planだけを確認してください。

まだコードを書かず、フェーズ専用の詳細specを作成して承認を待ってください。
specには、入力、出力、ジョブ状態、外部プロセス、再開、上書き防止、秘密情報、
フェーズ末に行う試験・実機受入条件を明記してください。

既存のbeat-sync/transcribe/Resolveアダプターの挙動を変更しないでください。
外部送信、VOICEVOX実機、Codex CLI、Resolveプロジェクト変更が必要になったら、
実行前にユーザーの明示承認を求めてください。リモート操作は行わないでください。
```

## フェーズ完了時の記録

各フェーズの最後に、既存のドキュメント体系へ次を追加または更新する。

1. `docs/superpowers/specs/` のフェーズ専用spec
2. `docs/superpowers/plans/` の実装plan
3. READMEの利用方法と非対象範囲
4. 必要なら受入runbook（実機受入の実行自体は最後のまとめフェーズまで保留）
5. この文書の「現在地」表

この引き継ぎ文書はロードマップの索引であり、個別フェーズの詳細仕様やテスト設計を
重複して持たない。
