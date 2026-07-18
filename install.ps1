[CmdletBinding()]
param(
    [string] $AppRoot = (Join-Path $env:LOCALAPPDATA "MinoruStudio"),
    [string] $ResolveScriptsRoot = (
        Join-Path $env:APPDATA "Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
    ),
    [switch] $SkipSync
)

$ErrorActionPreference = "Stop"

if (-not $SkipSync) {
    $uvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
    if ($null -eq $uvCommand) {
        throw "uv が見つかりません。uv をインストールするか -SkipSync を指定してください。"
    }
    Write-Host "[1/3] 依存関係を uv.lock に同期..."
    Push-Location $PSScriptRoot
    try {
        & $uvCommand.Source sync --locked
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync --locked が終了コード $LASTEXITCODE で失敗しました。"
        }
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Host "[1/3] 依存関係の同期をスキップ"
}

$adapterRoot = Join-Path $AppRoot "resolve_adapter"
$adapterSource = Join-Path $PSScriptRoot "resolve_adapter\minoru_studio_resolve"
New-Item -ItemType Directory -Force -Path $adapterRoot | Out-Null
Copy-Item -LiteralPath $adapterSource -Destination $adapterRoot -Recurse -Force
$installedAdapter = Join-Path $adapterRoot "minoru_studio_resolve"

Write-Host "[2/3] Resolve アダプターを配置: $installedAdapter"

$launcherSource = Join-Path $PSScriptRoot "scripts\MinoruStudio.py"
New-Item -ItemType Directory -Force -Path $ResolveScriptsRoot | Out-Null
Copy-Item -LiteralPath $launcherSource -Destination $ResolveScriptsRoot -Force
$installedLauncher = Join-Path $ResolveScriptsRoot "MinoruStudio.py"

Write-Host "[3/3] Utility ランチャーを配置: $installedLauncher"
Write-Host "完了。Resolve の [ワークスペース] → [スクリプト] → [MinoruStudio] から起動できます。"
Write-Host "(Resolve 起動中ならメニューに出るまで Resolve を再起動してください)"
