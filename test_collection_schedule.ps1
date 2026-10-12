# Offline migration check: no scheduled task is registered or changed.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
function Get-ScheduledTask($TaskName, $ErrorAction) {
    if ($TaskName -eq $expectedName) {
        [pscustomobject]@{TaskName=$TaskName;State='Ready';Actions=@([pscustomobject]@{Execute=(Get-Command powershell.exe).Source;Arguments=$legacy})}
    }
}
function Set-ScheduledTask($TaskName, $Action) { $global:NewsScheduleTestInstalled = @{Name=$TaskName;Action=$Action;Keys=@($PSBoundParameters.Keys)} }
function Register-ScheduledTask { throw 'Migration must preserve the existing schedule, not re-register it.' }
function Disable-ScheduledTask { throw 'No legacy eight-stock task exists in this fixture.' }
function Get-ScheduledTaskInfo($TaskName) { [pscustomobject]@{LastTaskResult=0} }
function New-ScheduledTaskAction($Execute,$Argument,$WorkingDirectory) { [pscustomobject]@{Execute=$Execute;Arguments=$Argument;WorkingDirectory=$WorkingDirectory} }
function New-ScheduledTaskTrigger { [pscustomobject]@{} }
function New-ScheduledTaskPrincipal { [pscustomobject]@{} }
function New-ScheduledTaskSettingsSet { [pscustomobject]@{} }
function Set-Content { } # Never touch the live Qwen enabled marker.
foreach ($job in @('Universe', 'Qwen')) {
$expectedName = if ($job -eq 'Universe') { 'NewsAlarm-CSQ-30min' } else { 'NewsAlarm-Qwen-Continuous' }
$legacy = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + (Join-Path $root 'schedule.ps1') + '" -Action Run -Job ' + $job
$expectedArgs = if ($job -eq 'Universe') { '-u "' + (Join-Path $root 'collection_job.py') + '"' } else { '-u "' + (Join-Path $root 'first_pass.py') + '" --run --limit 1000 --seconds 1800 --publish' }
& (Join-Path $root 'schedule.ps1') -Action Install -Job $job | Out-Null
if ($global:NewsScheduleTestInstalled.Name -ne $expectedName) { throw 'Wrong task changed.' }
if ($global:NewsScheduleTestInstalled.Action.Execute -ne (Join-Path $root '.venv\Scripts\pythonw.exe')) { throw 'Task must directly own the Python worker.' }
if ($global:NewsScheduleTestInstalled.Action.Arguments -ne $expectedArgs) { throw 'Wrong worker arguments.' }
if (($global:NewsScheduleTestInstalled.Keys | Sort-Object) -join ',' -ne 'Action,TaskName') { throw 'Unexpected trigger/account/settings mutation.' }
}
'Direct worker migration preserves triggers, account and settings. No tasks changed.'
