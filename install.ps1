# MinoruDouga セットアップ:
#  1. ビート解析に必要な Python ライブラリをインストール
#  2. ランチャーを DaVinci Resolve のスクリプトメニュー (Utility) に配置
$ErrorActionPreference = "Stop"

Write-Host "[1/2] Python ライブラリをインストール..."
py -m pip install -r (Join-Path $PSScriptRoot "requirements.txt") --quiet

Write-Host "[2/2] ランチャーを Resolve のスクリプトフォルダに配置..."
$dest = Join-Path $env:APPDATA "Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item (Join-Path $PSScriptRoot "scripts\MinoruDouga.py") $dest -Force

Write-Host "完了。Resolve の [ワークスペース] → [スクリプト] → [MinoruDouga] から起動できます。"
Write-Host "(Resolve 起動中ならメニューに出るまで Resolve を再起動してください)"
