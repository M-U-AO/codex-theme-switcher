$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$coreScript = Join-Path $scriptDirectory "codex_theme_core.py"
$forwardedArguments = @($args)

function Test-PythonRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    try {
        & $Executable @PrefixArguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 1>$null 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

$pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
$windowsAppsAlias = $null -ne $pythonCommand -and $pythonCommand.Source -like "*\Microsoft\WindowsApps\python*.exe"

if ($null -ne $pythonCommand -and -not $windowsAppsAlias -and (Test-PythonRuntime -Executable $pythonCommand.Source)) {
    & $pythonCommand.Source $coreScript @forwardedArguments
    exit $LASTEXITCODE
}

$pythonLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
if ($null -ne $pythonLauncher -and (Test-PythonRuntime -Executable $pythonLauncher.Source -PrefixArguments @("-3"))) {
    & $pythonLauncher.Source -3 $coreScript @forwardedArguments
    exit $LASTEXITCODE
}

# Codex Desktop ships a private Python runtime on some Windows installations.
# Use it as a best-effort fallback so the switcher works before Python is added
# to PATH. This location is not a public Codex API and may change in the future.
$userProfileDirectory = [Environment]::GetFolderPath("UserProfile")
if ([string]::IsNullOrWhiteSpace($userProfileDirectory)) {
    $userProfileDirectory = $env:USERPROFILE
}
if ([string]::IsNullOrWhiteSpace($userProfileDirectory)) {
    $userProfileDirectory = "$env:HOMEDRIVE$env:HOMEPATH"
}
if ([string]::IsNullOrWhiteSpace($userProfileDirectory)) {
    Write-Error "Unable to resolve the user profile directory"
    exit 1
}
$codexPython = Join-Path $userProfileDirectory ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if ((Test-Path -LiteralPath $codexPython -PathType Leaf) -and (Test-PythonRuntime -Executable $codexPython)) {
    & $codexPython $coreScript @forwardedArguments
    exit $LASTEXITCODE
}

if ($windowsAppsAlias -and (Test-PythonRuntime -Executable $pythonCommand.Source)) {
    & $pythonCommand.Source $coreScript @forwardedArguments
    exit $LASTEXITCODE
}

Write-Error "Python 3.10 or newer is required"
exit 1
