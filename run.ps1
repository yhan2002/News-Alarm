param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AppArgs)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonCandidates = @(
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
)
$pythonExecutable = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonExecutable) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonExecutable = $pythonCommand.Source }
}
if (-not $pythonExecutable) { throw 'Python 3.10+ is required. Install Python or create .venv.' }
if (-not $AppArgs) { $AppArgs = @('serve') }
& $pythonExecutable (Join-Path $PSScriptRoot 'app.py') @AppArgs
exit $LASTEXITCODE
