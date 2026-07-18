# Resolve Beat-Sync Acceptance Runbook

この手順は MinoruStudio の Resolve 連携をリリース前に確認するためのものです。
必ず破棄可能な DaVinci Resolve プロジェクトと複製したテスト素材を使用してください。
制作中のプロジェクト、唯一の素材、共有データベースでは実行しません。

## 1. 実行条件と記録欄

- 実施日:
- 実施者:
- Resolve product:
- Resolve version:
- プロジェクト名 / project ID:
- timeline frame rate:
- MinoruStudio commit:
- テスト用ジョブ:
  - 写真のみ:
  - 写真・動画混在:

Resolve の製品名、バージョン、project ID、実測timeline rateは、適用後の
`resolve/applications/<attempt-id>.json` に記録された `resolve`、`project_id`、
`timeline_rate` から転記します。

## 2. 安全なテストプロジェクトを作る

1. 新しいローカルプロジェクトを作り、`MinoruStudio Acceptance` など明確な名前を付ける。
2. 空のタイムライン `SENTINEL-DO-NOT-TOUCH` を1本作り、短い任意のクリップを置く。
3. センチネルのtimeline ID、名前、クリップ数を記録する。
4. BGM、写真、動画をテスト専用フォルダへ複製する。動画は2本以上用意する。
5. テスト素材の事前ハッシュを保存する。

```powershell
Get-ChildItem -LiteralPath '<test-media-directory>' -File |
    Get-FileHash -Algorithm SHA256 |
    ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath '<evidence-directory>\input-hashes-before.json' -Encoding utf8
```

既存プロジェクトや元素材を使わないことを確認してから先へ進みます。

## 3. 準備ジョブを作る

写真のみのジョブと、写真・動画混在ジョブを別々に作ります。CLIまたは引数なしGUIの
どちらを使っても構いません。両方の `job.json` が `status: succeeded` になり、
`outputs/beat-sync-plan.json` が存在することを確認します。

CLI例:

```powershell
uv run minoru-studio beat-sync `
    -Music '<bgm-path>' `
    -MediaDir '<photo-only-directory>' `
    -TimelineName 'Acceptance Photo' `
    -Name 'acceptance-photo' `
    -OutputDir '<jobs-directory>'

uv run minoru-studio beat-sync `
    -Music '<bgm-path>' `
    -MediaDir '<mixed-media-directory>' `
    -TimelineName 'Acceptance Mixed' `
    -Name 'acceptance-mixed' `
    -OutputDir '<jobs-directory>'
```

## 4. アダプターをインストールする

この工程は明示承認後にだけ実行します。

```powershell
pwsh -NoProfile -File .\install.ps1
```

次を記録します。

- `uv sync --locked` が成功した。
- 表示されたadapter install path。
- 表示されたUtility launcher path。
- Resolve再起動後、`ワークスペース → スクリプト → MinoruStudio` が見える。
- 既存の `MinoruDouga` launcherが削除されていない。

## 5. 写真のみを1回の呼び出しで適用する

1. `MinoruStudio` を起動し、写真のみの成功ジョブを選ぶ。
2. `素材を取り込む` を実行する。
3. スチル設定が適合していれば、同じウィンドウが `タイムラインを生成` に変わることを確認する。
4. 確認ダイアログのBPM、間隔、概算カット長、BGM長、素材数、スチル長、提案名を記録する。
5. 承認し、新しいタイムラインが1本作られることを確認する。

期待結果:

- A1にBGMが1本ある。
- V1にだけ写真が配置され、写真の音声はない。
- 成功した各カット開始に青い `beat` markerがある。
- 実測長と目標長の差が各配置で±1timeline frame以内である。

## 6. 動画のIn/Outを設定して再開する

1. 混在ジョブで `素材を取り込む` を実行する。
2. 状態が `awaiting_in_out` となり、案内後にUtilityウィンドウが閉じることを確認する。
3. application bin内で、動画AにはInだけ、動画BにはInとOutの両方を設定する。
4. `MinoruStudio` を再起動し、同じジョブを選ぶ。
5. `In/Out設定後に再開` を実行する。
6. application JSONで、Inだけの動画は素材末尾まで、In/Out動画は
   `mark_out_frame_exclusive = Resolve Out + 1` と記録されたことを確認する。
7. 最終確認を承認してタイムラインを生成する。

期待結果は写真のみと同じく、A1 BGM、V1-only visuals、青marker、±1frame以内です。

## 7. 意図的なスチル長不一致と再測定

1. 破棄可能プロジェクトでのみ、Resolveの標準スチル長を明らかに異なる値へ変更する。
2. 写真を含む新しいattemptを開始し、`awaiting_still_setting` になることを確認する。
3. ダイアログにrequired framesとactual framesが明示されることを記録する。
4. `環境設定 → ユーザー → 編集 → 一般設定 → 標準スチルの長さ` をrequired値相当に変更する。
5. `MinoruStudio` を再起動し、`スチル設定変更後に再測定` を実行する。
6. 写真だけがretry sub-binへ再importされ、動画item IDとIn/Out記録は保持されることを確認する。
7. `ready` へ進み、最終適用が成功することを確認する。

プローブtimelineは削除され、実行前に選択されていたtimelineへ戻る必要があります。

## 8. キャンセルと再適用を確認する

### readyからキャンセル

1. 新しいattemptを `ready` まで進める。
2. `タイムラインを生成` を押し、最終確認でキャンセルする。
3. stateが `ready` のまま、final timelineが1本も増えていないことを確認する。

### 同じジョブを再適用

1. terminal attemptに対して `新しい適用を開始` を選ぶ。
2. 必要な再開操作を経て適用する。
3. 最初が `Acceptance Mixed` なら、次が `Acceptance Mixed-002` になることを確認する。
4. 既存timelineを再利用・上書きしていないことを確認する。

## 9. 部分失敗の保持はFakeだけで確認する

実Resolveプロジェクトで意図的な失敗を誘発しません。次のFake Resolveテストだけを実行します。

```powershell
uv run pytest tests/resolve_adapter/test_apply.py::test_mid_apply_failure_keeps_partial_timeline_and_fails_attempt -q
uv run pytest tests/resolve_adapter/test_resolve_log.py::test_apply_failure_records_traceback_and_partial_timeline -q
```

期待結果:

- attemptは`failed`になる。
- 作成済みの部分timeline IDがapplication JSONに残る。
- application binと部分timelineは自動削除されない。
- `logs/resolve.log` に例外traceと部分timeline IDが残る。

## 10. 非破壊性を再確認する

1. `SENTINEL-DO-NOT-TOUCH` のID、名前、クリップ数、内容が開始前と一致する。
2. MinoruStudioが作ったbinとtimeline以外が増減していない。
3. 入力ファイルの事後ハッシュを取り、事前ハッシュと比較する。

```powershell
Get-ChildItem -LiteralPath '<test-media-directory>' -File |
    Get-FileHash -Algorithm SHA256 |
    ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath '<evidence-directory>\input-hashes-after.json' -Encoding utf8

Compare-Object `
    (Get-Content -Raw -LiteralPath '<evidence-directory>\input-hashes-before.json' | ConvertFrom-Json) `
    (Get-Content -Raw -LiteralPath '<evidence-directory>\input-hashes-after.json' | ConvertFrom-Json) `
    -Property Path, Hash
```

`Compare-Object` が何も出力しないことが合格条件です。

## 11. 合否表

| # | シナリオ | 合格条件 | Pass / Fail | Notes |
|---|---|---|---|---|
| 1 | 環境記録 | product/version/project ID/timeline rateを記録 |  |  |
| 2 | install/menu | adapterとUtility pathを記録し、メニュー表示、legacy残存 |  |  |
| 3 | 写真のみ | 1 invocationでreadyへ進み、新規timelineを適用 |  |  |
| 4 | mixed In-only/In-Out | exclusive Outを記録し、resume成功 |  |  |
| 5 | still mismatch/retry | required値表示、設定変更、写真だけ再import |  |  |
| 6 | 配置 | A1 BGM、V1-only、青marker、±1frame |  |  |
| 7 | 再適用 | `-002` suffixで新規timeline作成 |  |  |
| 8 | cancel | ready維持、final timeline増加なし |  |  |
| 9 | Fake部分失敗 | partial objectsとtraceを保持 |  |  |
| 10 | sentinel | 既存timelineが変更されていない |  |  |
| 11 | input hashes | 事前・事後SHA-256が一致 |  |  |
| 12 | application evidence | detail path、bin ID、timeline IDを収集 |  |  |

1つでもFailまたは未確認なら、バージョンを0.2.0へ昇格しません。

## 12. ソース素材を含めず証跡を収集する

次の例は、`job.json`、全application JSON、`logs/resolve.log`、入力ハッシュ一覧だけを
収集します。素材ファイル自体、`inputs`、`outputs`、`work`はコピーしません。

```powershell
$jobRoot = (Resolve-Path -LiteralPath '<job.media-job>').Path
$evidenceRoot = Join-Path '<evidence-directory>' 'minoru-studio-acceptance'
$applicationsRoot = Join-Path $evidenceRoot 'applications'
New-Item -ItemType Directory -Force -Path $applicationsRoot | Out-Null

Copy-Item -LiteralPath (Join-Path $jobRoot 'job.json') -Destination $evidenceRoot
Get-ChildItem -LiteralPath (Join-Path $jobRoot 'resolve\applications') -Filter '*.json' -File |
    Copy-Item -Destination $applicationsRoot

$resolveLog = Join-Path $jobRoot 'logs\resolve.log'
if (Test-Path -LiteralPath $resolveLog) {
    Copy-Item -LiteralPath $resolveLog -Destination $evidenceRoot
}

$job = Get-Content -Raw -LiteralPath (Join-Path $jobRoot 'job.json') | ConvertFrom-Json
$job.inputs |
    ForEach-Object {
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_.path
        [pscustomobject]@{
            input_path = $_.path
            sha256 = $hash.Hash
            manifest_sha256 = $_.sha256
            unchanged = ($hash.Hash.ToLowerInvariant() -eq $_.sha256.ToLowerInvariant())
        }
    } |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $evidenceRoot 'input-hashes.json') -Encoding utf8
```

application IDの一覧:

```powershell
Get-ChildItem -LiteralPath $applicationsRoot -Filter '*.json' -File |
    ForEach-Object {
        $application = Get-Content -Raw -LiteralPath $_.FullName | ConvertFrom-Json
        [pscustomobject]@{
            detail_path = $_.FullName
            attempt_id = $application.attempt_id
            state = $application.state
            project_id = $application.project_id
            bin_id = $application.bin.id
            timeline_id = $application.timeline.id
            timeline_name = $application.timeline.name
        }
    } |
    Format-Table -AutoSize
```

証跡を共有する場合は、`job.json` とapplication JSONに含まれるローカル絶対パスを
必要に応じて伏せます。元素材は証跡ディレクトリへ入れません。
