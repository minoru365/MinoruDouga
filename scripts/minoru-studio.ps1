[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $CommandArgs
)

$ErrorActionPreference = "Stop"
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    [Console]::Error.WriteLine(
        "uv が見つかりません。MinoruStudioの専用Python環境を起動できません。"
    )
    exit 127
}

$projectRoot = Split-Path -Parent $PSScriptRoot
& $uvCommand.Source run --project $projectRoot minoru-studio @CommandArgs
exit $LASTEXITCODE
