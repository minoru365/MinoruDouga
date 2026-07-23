# `repo-demo` 廃止設計

## 目的

未実装の `repo-demo`、`repo-demo -AI`、およびデモ組み立てを MinoruStudio の製品対象から完全に外す。将来の実装候補として残さず、CLI、GUI、ジョブモデル、現行ドキュメントのいずれからも選択・案内できない状態にする。

## 対象範囲

- `JobMode.REPO_DEMO` と、このモードを前提にしたテストを削除する。
- 現在の製品を説明する `README.md`、`docs/media-automation-design.md`、`docs/agent-handoff.md` から、`repo-demo`、AI送信によるリポジトリ解析、デモ組み立てを削除する。
- Resolve の後続対象を `transcribe` と `narrate` のみに更新する。
- 過去の `docs/superpowers/specs/` と `docs/superpowers/plans/` は、当時の判断を示す履歴として変更しない。

## 非対象

- `beat-sync`、`transcribe`、`script-draft`、`narrate`、既存 Resolve アダプターの動作変更。
- 既に存在する `.media-job` の削除、移行、内容変更。
- Codex CLI、OpenAI API、クラウド送信機能の追加または削除以外の依存関係変更。
- README の利用方法に含まれる既存モードの再設計。

## 挙動

- `repo-demo` は `JobMode` の有効値でなくなる。新規ジョブとして作成できない。
- 旧 `repo-demo` マニフェストは変更しない。enum 廃止後に開いた場合は、既存のマニフェスト検証が未対応モードとして拒否するだけであり、ファイルを削除・書換えない。
- GUI の汎用ジョブ作成テストは、存続する固定モードを使うよう更新する。

## ドキュメント方針

- 総合設計は、目的、固定モード、外部送信、受入条件、検証、ロードマップから `repo-demo` 固有の説明を削る。
- その結果不要となる Codex CLI の専用節と `demo-plan.json` の専用節も削除する。
- README は「リポジトリのデモ動画制作」を製品説明から除外する。
- 引き継ぎ文書は、現在地、実装順序、担当テンプレート、最終 Resolve 受入の対象を更新する。

## 検証

- `tests/jobs/test_model.py` は有効な固定モード集合に `repo-demo` が含まれないことを確認する。
- `tests/test_gui_controller.py` は存続するモードで既存の汎用ジョブ作成・検査を確認する。
- `rg -n -i "repo-demo|repo demo|デモ組み立て" README.md docs/agent-handoff.md docs/media-automation-design.md src tests` が、履歴文書以外で一致しないことを確認する。
- `uv run pytest -q` と `uv run minoru-studio doctor --json` を実行し、既存モードに回帰がないことを確認する。

## 受入条件

- 現在の製品コードに `repo-demo` を選択・作成する経路がない。
- 現行の製品文書は、`repo-demo` とデモ組み立てを将来機能として案内しない。
- 既存 `.media-job` を含むユーザーデータは自動変更・削除されない。
- 既存モードの全自動テストと doctor が成功する。
