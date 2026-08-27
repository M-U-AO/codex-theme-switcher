$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$coreScript = Join-Path $scriptDirectory "codex_theme_core.py"
$forwardedArguments = @($args)
$pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue

if ($null -ne $pythonCommand) {
    & $pythonCommand.Source $coreScript @forwardedArguments
    exit $LASTEXITCODE
}

$pythonLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
if ($null -ne $pythonLauncher) {
    & $pythonLauncher.Source -3 $coreScript @forwardedArguments
    exit $LASTEXITCODE
}

# Codex Desktop ships a private Python runtime on some Windows installations.
# Use it as a best-effort fallback so the switcher works before Python is added
# to PATH. This location is not a public Codex API and may change in the future.
$userProfileDirectory = [Environment]::GetFolderPath("UserProfile")
$codexPython = Join-Path $userProfileDirectory ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (Test-Path -LiteralPath $codexPython -PathType Leaf) {
    & $codexPython $coreScript @forwardedArguments
    exit $LASTEXITCODE
}

Write-Error "需要 Python 3.10 或更高版本"
exit 1
