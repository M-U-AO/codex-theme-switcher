$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$coreScript = Join-Path $scriptDirectory "codex_theme_core.py"
$pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue

if ($null -ne $pythonCommand) {
    & $pythonCommand.Source $coreScript @args
    exit $LASTEXITCODE
}

$pythonLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
if ($null -ne $pythonLauncher) {
    & $pythonLauncher.Source -3 $coreScript @args
    exit $LASTEXITCODE
}

Write-Error "需要 Python 3.10 或更高版本"
exit 1
