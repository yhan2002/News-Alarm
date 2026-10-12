param(
    [ValidateSet('Install', 'Run', 'Status', 'Disable')][string]$Action = 'Status',
    [ValidateSet('News', 'Universe', 'Agenda', 'Qwen')][string]$Job = 'News',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$taskName = 'NewsAlarm-US-30min'
$logPrefix = 'auto-collect'
if ($Job -eq 'Agenda') { $taskName = 'NewsAlarm-Daily-Agenda'; $logPrefix = 'daily-agenda' }
if ($Job -eq 'Universe') { $taskName = 'NewsAlarm-CSQ-30min' }
if ($Job -eq 'Qwen') { $taskName = 'NewsAlarm-Qwen-Continuous'; $logPrefix = 'first-pass' }
$pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$scriptFile = Join-Path $PSScriptRoot 'schedule.ps1'
$taskArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $scriptFile + '" -Action Run'
if ($Job -eq 'Agenda') { $taskArguments += ' -Job Agenda' }
if ($Job -eq 'Universe') { $taskArguments += ' -Job Universe' }
if ($Job -eq 'Qwen') { $taskArguments += ' -Job Qwen' }
$taskExecutable = (Get-Command powershell.exe).Source
$legacyArguments = $taskArguments
if ($Job -eq 'Universe') {
    $taskExecutable = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    $taskArguments = '-u "' + (Join-Path $PSScriptRoot 'collection_job.py') + '"'
}
if ($Job -eq 'Qwen') {
    $taskExecutable = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    $taskArguments = '-u "' + (Join-Path $PSScriptRoot 'first_pass.py') + '" --run --limit 1000 --seconds 1800 --publish'
}

if ($Action -eq 'Run') {
    if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Project .venv is missing.' }
    if ($Job -eq 'Universe') {
        $workerArguments = @('-u', (Join-Path $PSScriptRoot 'collection_job.py'))
        if ($DryRun) { $workerArguments += '--dry-run' }
        & $pythonExecutable @workerArguments
        exit $LASTEXITCODE
    }
    $logDirectory = Join-Path $PSScriptRoot 'data'
    [IO.Directory]::CreateDirectory($logDirectory) | Out-Null
    # OS releases the file lock even after a crash; manual wrapper runs cannot overlap.
    try { $runLock = [IO.File]::Open((Join-Path $logDirectory "$logPrefix.lock"), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch [IO.IOException] { Write-Output 'Collection already running, or lock unavailable.'; exit 3 }
    try {
        $env:PYTHONUTF8 = '1'
        $env:HF_HUB_OFFLINE = '1'
        $env:TRANSFORMERS_OFFLINE = '1'
        $env:HF_HUB_DISABLE_TELEMETRY = '1'
        $collectArguments = '-u app.py collect --tickers NVDA,MSFT,AMZN,GOOGL,META,AAPL,TSLA,AMD --macro --limit 20 --engine finbert'
        if ($Job -eq 'Agenda') { $collectArguments = '-u daily_schedule.py --send --scheduled' }
        if ($Job -eq 'Qwen') { $collectArguments = '-u first_pass.py --run --limit 1000 --seconds 1800 --publish' }
        if ($DryRun) {
            if ($Job -eq 'Qwen') { $collectArguments = '-u first_pass.py' }
            else { $collectArguments += ' --dry-run' }
        }
        $processOptions = @{
            FilePath = $pythonExecutable; ArgumentList = $collectArguments
            WorkingDirectory = $PSScriptRoot; WindowStyle = 'Hidden'; PassThru = $true; Wait = $true
            RedirectStandardOutput = (Join-Path $logDirectory "$logPrefix.out.log")
            RedirectStandardError = (Join-Path $logDirectory "$logPrefix.err.log")
        }
        $collector = Start-Process @processOptions
        $result = $collector.ExitCode
        if ($null -eq $result) { throw 'Collector exit code unavailable; check the logs.' }
        Write-Output "Collector exit code: $result. Logs: $logDirectory\$logPrefix.*.log"
        exit $result
    } finally { $runLock.Dispose() }
}

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$matchesCurrent = $existing -and $existing.Actions.Count -eq 1 -and $existing.Actions[0].Arguments -eq $taskArguments -and $existing.Actions[0].Execute -eq $taskExecutable
$matchesLegacy = $existing -and $Job -in @('Universe', 'Qwen') -and $existing.Actions.Count -eq 1 -and $existing.Actions[0].Arguments -eq $legacyArguments -and $existing.Actions[0].Execute -eq (Get-Command powershell.exe).Source
if ($existing -and -not ($matchesCurrent -or $matchesLegacy)) {
    throw 'A task with this name belongs to a different command. It was not modified.'
}
if ($Action -eq 'Install') {
    if (-not (Test-Path -LiteralPath $pythonExecutable) -or ($Job -in @('News', 'Universe') -and -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'models\finbert\config.json')))) {
        throw 'Install the project .venv and local FinBERT model first.'
    }
    $account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    if (-not (Test-Path -LiteralPath $taskExecutable)) { throw 'Scheduled executable is missing.' }
    $taskAction = New-ScheduledTaskAction -Execute $taskExecutable -Argument $taskArguments -WorkingDirectory $PSScriptRoot
    $description = 'US stock and macro RSS every 30 minutes; local FinBERT; no paid API.'
    if ($Job -eq 'Agenda') {
        & $pythonExecutable (Join-Path $PSScriptRoot 'daily_schedule.py') --dry-run
        if ($LASTEXITCODE -ne 0) { throw 'Agenda configuration check failed.' }
        $todayKorea = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::Now, 'Korea Standard Time').ToString('yyyy-MM-dd')
        $trigger = New-ScheduledTaskTrigger -Daily -At '19:30'
        $trigger.StartBoundary = $todayKorea + 'T19:30:00+09:00'
        $triggers = @($trigger)
        $description = 'Daily 19:30 Korea time: official US agenda to QuantBot main group. No model calls.'
    } elseif ($Job -eq 'Qwen') {
        $triggers = @(
            (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)),
            (New-ScheduledTaskTrigger -AtLogOn -User $account)
        )
    } else {
        $triggers = @(
            (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(30) -RepetitionInterval (New-TimeSpan -Minutes 30)),
            (New-ScheduledTaskTrigger -AtLogOn -User $account)
        )
    }
    $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
    $minutes = 12
    if ($Job -eq 'Universe') { $minutes = 60; $description = 'CSQ RSS every 30 minutes; one Python worker saves and publishes. Qwen is independent.' }
    if ($Job -eq 'Qwen') { $minutes = 35; $description = 'Continuous local Qwen queue; 30-minute batches, resume within one minute. Cached successes skipped.' }
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes $minutes)
    if ($existing -and $Job -in @('Universe', 'Qwen')) {
        # Preserve the existing 30-minute cadence, account, and scheduler settings.
        Set-ScheduledTask -TaskName $taskName -Action $taskAction | Out-Null
    } else {
        Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $triggers -Principal $principal -Settings $settings -Description $description -Force | Out-Null
    }
    if ($Job -eq 'Qwen') { Set-Content -LiteralPath (Join-Path $PSScriptRoot 'data\qwen-continuous.enabled') -Value 'enabled' }
    if ($Job -eq 'Universe') {
        $old = Get-ScheduledTask -TaskName 'NewsAlarm-US-30min' -ErrorAction SilentlyContinue
        $oldArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $scriptFile + '" -Action Run'
        if ($old -and $old.Actions.Count -eq 1 -and $old.Actions[0].Arguments -eq $oldArguments) {
            Disable-ScheduledTask -TaskName 'NewsAlarm-US-30min' | Out-Null
        }
    }
} elseif ($Action -eq 'Disable') {
    if ($existing) { Disable-ScheduledTask -TaskName $taskName | Out-Null }
    if ($Job -eq 'Qwen') { Remove-Item -LiteralPath (Join-Path $PSScriptRoot 'data\qwen-continuous.enabled') -ErrorAction SilentlyContinue }
}
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $taskName | Select-Object LastRunTime, LastTaskResult, NextRunTime
