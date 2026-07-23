# `repo-demo` 廃止 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 未実装の `repo-demo`、`repo-demo -AI`、デモ組み立てを MinoruStudio のコードと現行製品文書から完全に撤去する。

**Architecture:** `repo-demo` は `JobMode` の列挙値だけが先行しているため、その値と汎用 GUI テストを先に削除する。次に製品の正本である README、総合設計、引き継ぎを現在の4モード（`beat-sync`、`transcribe`、`script-draft`、`narrate`）と Resolve 最終受入に再編する。過去の spec/plan は履歴なので変更しない。

**Tech Stack:** Python 3.12、pytest、Tkinter ランチャー、Markdown、uv。

## Global Constraints

- Windows 専用の既存固定モード `beat-sync`、`transcribe`、`script-draft`、`narrate` と Resolve アダプターの挙動を変更しない。
- `repo-demo` の既存 `.media-job` を削除、移行、書換えない。列挙値の廃止後は通常の未対応マニフェストとして読み込みを拒否するだけにする。
- 過去の `docs/superpowers/specs/` と `docs/superpowers/plans/` は変更しない。今回追加する spec と plan は履歴ではなく現行の廃止決定として残す。
- 依存関係、VOICEVOX 実機、FFmpeg メディア処理、Resolve プロジェクト、リモート接続を変更・実行しない。
- 実装中は TDD を用い、変更した契約のテストと最終全体テストを実行する。

---

### Task 1: `repo-demo` ジョブモードを撤去する

**Files:**
- Modify: `src/minoru_studio/jobs/model.py:15-20`
- Modify: `tests/jobs/test_model.py:100-119`
- Modify: `tests/test_gui_controller.py:12-18`

**Interfaces:**
- Consumes: `JobMode` は GUI の combobox と `LauncherController.create_job(mode, name, output_dir)` が利用する `StrEnum`。
- Produces: 有効なモード集合は `beat-sync`、`transcribe`、`script-draft`、`narrate` の4つだけになる。`JobMode("repo-demo")` は `ValueError` になる。

- [ ] **Step 1: 廃止モードを拒否する失敗テストを書く**

  `tests/jobs/test_model.py` の固定モード集合を次だけに変更し、`repo-demo` が残っている現在の状態で失敗させる。

  ```python
  def test_all_fixed_modes_are_declared():
      assert {mode.value for mode in JobMode} == {
          "beat-sync",
          "transcribe",
          "narrate",
          "script-draft",
      }
  ```

  `tests/test_gui_controller.py` の先頭付近に、旧値を新規ジョブへ渡せないことを追加する。

  ```python
  def test_controller_rejects_retired_repo_demo_mode(tmp_path):
      with pytest.raises(ValueError):
          LauncherController().create_job("repo-demo", "demo", str(tmp_path))
  ```

  既存の汎用作成テストは `script-draft` を使うよう変更する。

  ```python
  def test_controller_creates_and_inspects_pending_job(tmp_path):
      controller = LauncherController()
      job_dir = controller.create_job("script-draft", "draft", str(tmp_path))
      manifest = controller.inspect_job(str(job_dir))
      assert manifest.mode.value == "script-draft"
      assert manifest.status.value == "pending"
  ```

- [ ] **Step 2: 失敗を確認する**

  Run: `uv run pytest tests/jobs/test_model.py tests/test_gui_controller.py -q`

  Expected: `test_all_fixed_modes_are_declared` が `repo-demo` の余分な列挙値で失敗し、廃止値拒否テストも失敗する。

- [ ] **Step 3: enum から廃止値を削除する**

  `src/minoru_studio/jobs/model.py` の enum を次の4値だけにする。

  ```python
  class JobMode(StrEnum):
      BEAT_SYNC = "beat-sync"
      TRANSCRIBE = "transcribe"
      NARRATE = "narrate"
      SCRIPT_DRAFT = "script-draft"
  ```

  `JobMode(mode)` を使う `LauncherController.create_job` は変更しない。列挙値の削除だけで、旧文字列は安全に `ValueError` となる。

- [ ] **Step 4: モデルと GUI 境界のテストを通す**

  Run: `uv run pytest tests/jobs/test_model.py tests/test_gui_controller.py -q`

  Expected: PASS。`repo-demo` を選ぶ UI 値が列挙から消え、既存モードでのジョブ作成・検査が通る。

- [ ] **Step 5: Task 1 をコミットする**

  ```powershell
  git add src/minoru_studio/jobs/model.py tests/jobs/test_model.py tests/test_gui_controller.py
  git commit -m "refactor: remove repo demo job mode"
  ```

### Task 2: 現行製品文書からデモ制作ロードマップを撤去する

**Files:**
- Modify: `README.md:1-5`
- Modify: `docs/media-automation-design.md`
- Modify: `docs/agent-handoff.md`

**Interfaces:**
- Consumes: Task 1 後の有効モード集合と、`docs/superpowers/specs/2026-07-23-remove-repo-demo-design.md` の対象範囲。
- Produces: README、総合設計、引き継ぎが `repo-demo`、`repo-demo -AI`、デモ組み立て、Codex CLI によるリポジトリ送信を現行または将来の機能として案内しない。

- [ ] **Step 1: 正本に残る廃止機能の失敗検査を追加する**

  `tests/test_repo_demo_removal.py` を新規作成し、現行製品文書だけを対象に禁止語を検査する。過去の `docs/superpowers/specs/` と `docs/superpowers/plans/` は検索対象に含めない。

  ```python
  from pathlib import Path

  import pytest


  @pytest.mark.parametrize(
      "path",
      ("README.md", "docs/agent-handoff.md", "docs/media-automation-design.md"),
  )
  def test_current_product_docs_do_not_announce_retired_repo_demo(path):
      text = Path(path).read_text(encoding="utf-8")
      assert "repo-demo" not in text
      assert "デモ組み立て" not in text
  ```

  Run: `uv run pytest tests/test_repo_demo_removal.py -q`

  Expected: FAIL。現行文書に廃止予定の機能説明が残っている。

- [ ] **Step 2: README の製品説明を4モードに合わせる**

  `README.md` 冒頭の紹介文から「リポジトリのデモ動画制作」を削り、音ハメ、文字起こし、読み上げを扱うローカル制作ツールとして表現する。既存の利用手順と既存モードの説明は変更しない。

- [ ] **Step 3: 総合設計を現在の製品境界に再編する**

  `docs/media-automation-design.md` で、次を同じ変更内で行う。

  - ステータスと目的から、リポジトリ解析・手動画面録画デモ・デモ素材の Resolve 配置を削る。
  - 外部送信/Codex CLI を前提にする 4.3、6.2 の説明とコマンド例を削り、現在の機能が外部送信を行わないことだけを明記する。
  - アーキテクチャ図とコンポーネント説明から `Codex CLI` を削る。
  - `### 7.5 repo-demo` 節全体、`### 11.3 Codex CLI` 節、`## 12. repo-demo の構造化出力` 節を削る。後ろの見出し番号は連番に修正する。
  - ジョブパッケージ例から `script.txt`、`captures/`、`project-summary.md`、`demo-script.txt`、`shot-list.md`、`demo-plan.json` を削る。
  - プライバシー、安全性、再開、検証方針、ローカル統合検証、受入条件から AI バンドル、Codex CLI、リポジトリ送信、デモ台本、ダミー録画に関する項目を削る。
  - 実装順序を「共通基盤、beat-sync、transcribe、narrate、Resolve連携とまとめて受入、後続拡張」に更新し、後続拡張から Web 自動撮影を除く。

- [ ] **Step 4: 引き継ぎの現在地と次フェーズを更新する**

  `docs/agent-handoff.md` で、`repo-demo` ローカル工程、`repo-demo -AI`、デモ組み立ての現在地行を削除する。Resolve の未実装対象を `transcribe` と `narrate` だけにする。

  実装順序は次の3フェーズだけにする。

  ```text
  1. script-draft
  2. narrate
  3. Resolve連携とまとめて受入
  ```

  共通ガードレールの外部送信文を、現行製品には外部送信経路がないことへ更新する。次のエージェント向けテンプレートから `repo-demo-local`、`repo-demo-ai`、`demo-assembly` を外す。

- [ ] **Step 5: 文書撤去テストを通す**

  Run: `uv run pytest tests/test_repo_demo_removal.py -q`

  Expected: PASS。現行製品文書に `repo-demo` または「デモ組み立て」が残らない。

- [ ] **Step 6: Task 2 をコミットする**

  ```powershell
  git add README.md docs/media-automation-design.md docs/agent-handoff.md tests/test_repo_demo_removal.py
  git commit -m "docs: remove repo demo roadmap"
  ```

### Task 3: 廃止後の全体境界を検証して記録する

**Files:**
- Modify: `docs/agent-handoff.md`（Task 2 の文書撤去に追加が必要な場合だけ）
- Test: `tests/jobs/test_model.py`
- Test: `tests/test_gui_controller.py`
- Test: `tests/test_repo_demo_removal.py`

**Interfaces:**
- Consumes: Task 1 の4モード enum、Task 2 の現行製品文書。
- Produces: 既存モードの回帰がなく、ソース・テスト・現行文書に実装対象としての `repo-demo` が残らない検証結果。

- [ ] **Step 1: 追跡対象の残存参照を機械検査する**

  Run:

  ```powershell
  rg -n -i "repo-demo|repo demo|デモ組み立て" README.md docs/agent-handoff.md docs/media-automation-design.md src tests
  ```

  Expected: exit 1（一致なし）。過去の `docs/superpowers/specs/` と `docs/superpowers/plans/` は履歴のため、この検査に含めない。

- [ ] **Step 2: 変更境界のテストを実行する**

  Run:

  ```powershell
  uv run pytest tests/jobs/test_model.py tests/test_gui_controller.py tests/test_repo_demo_removal.py -q
  ```

  Expected: PASS。4モードだけが有効で、GUI の既存モード導線と現行文書の廃止境界が通る。

- [ ] **Step 3: 全自動テストと環境診断を実行する**

  Run: `uv run pytest -q`

  Expected: PASS（Windows の symlink 権限不足による既知の1 skipped は許容）。

  Run: `uv run minoru-studio doctor --json`

  Expected: exit 0 かつ JSON の `ok` が `true`。FFmpeg/FFprobe はバージョン照会のみで、メディア処理は行わない。

- [ ] **Step 4: diff と作業ツリーを確認する**

  Run:

  ```powershell
  git diff --check
  git status --short --branch
  ```

  Expected: whitespace エラーなし。今回のコード・文書・テスト変更以外の未追跡変更なし。

- [ ] **Step 5: Task 3 をコミットする**

  Task 3 で Task 2 後に修正が必要になった文書だけをコミットする。コードや文書の追加変更がなければ、検証結果を報告して新規コミットは作らない。

## Plan Self-Review

- Spec coverage: `JobMode`、GUI の旧値拒否、現行文書の撤去、旧ジョブの非破壊、履歴文書の保存、全体回帰のすべてを Task 1〜3 に割り当てた。
- Placeholder scan: 空欄、仮置き、未定義の手順を含めない。
- Type consistency: `JobMode(mode)` が旧文字列を `ValueError` として拒否する既存の型境界を利用し、新規の互換性 API や移行形式を導入しない。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-23-remove-repo-demo.md`.

1. **Subagent-Driven (recommended)** — タスクごとに新しい実装エージェントとレビューを使う。
2. **Inline Execution** — このセッションで実装バッチを進める。
