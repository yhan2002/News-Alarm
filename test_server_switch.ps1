# Offline process-identity checks: powershell -File .\test_server_switch.ps1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'server-switch.ps1')
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
$created = [datetime]'2026-09-10T05:00:00Z'
$share = @{server_pid=100; tunnel_pid=200; public_url='https://test-news.trycloudflare.com'; server_started=$created.ToUniversalTime().ToString('o')}
$parent = [pscustomobject]@{ProcessId=100; ParentProcessId=1; ExecutablePath=$pythonExecutable; CommandLine=('"' + $pythonExecutable + '" -u app.py serve --public-url ' + $share.public_url); CreationDate=$created}
Assert (Test-ManagedProcess $parent 'server' $share) 'Original relative server must be recognized.'
$child = [pscustomobject]@{ProcessId=101; ParentProcessId=100; ExecutablePath=$basePython; CommandLine=('"' + $basePython + '" -u "' + $appFile + '" serve --public-url ' + $share.public_url); CreationDate=$created.AddMilliseconds(10)}
Assert (Test-ManagedProcess $child 'server' $share) 'Absolute venv child must be recognized.'
$parent.CreationDate = $created.AddSeconds(1)
Assert (-not (Test-ManagedProcess $parent 'server' $share)) 'Reused PID must not be accepted.'
$parent.CreationDate = $created
$original = $parent.CommandLine
$parent.CommandLine = $original + ' --port 8765'
Assert (-not (Test-ManagedProcess $parent 'server' $share)) 'Another port must not be accepted.'
$parent.CommandLine = $original.Replace(' serve ', ' collect ')
Assert (-not (Test-ManagedProcess $parent 'server' $share)) 'Collector must not be stopped.'
$parent.CommandLine = $original.Replace('app.py', 'other.py')
Assert (-not (Test-ManagedProcess $parent 'server' $share)) 'Other apps must not be stopped.'
$parent.CommandLine = $original
$parent.ExecutablePath = 'C:\other\python.exe'
Assert (-not (Test-ManagedProcess $parent 'server' $share)) 'Other Python installs must not be accepted.'
$parent.ExecutablePath = $pythonExecutable
$tunnel = [pscustomobject]@{ProcessId=200; ExecutablePath=$tunnelExecutable; CommandLine=('"' + $tunnelExecutable + '" tunnel --no-autoupdate --url ' + $localUrl); CreationDate=$created}
Assert (Test-ManagedProcess $tunnel 'tunnel' $share) 'Own tunnel must be recognized.'
$tunnel.CommandLine = $tunnel.CommandLine.Replace('8873', '8765')
Assert (-not (Test-ManagedProcess $tunnel 'tunnel' $share)) 'Other tunnels must not be accepted.'

# Exercise child-first stopping without stopping any real process.
$script:fixtures = @($parent, $child)
$script:stopped = @()
function Get-CimInstance($ClassName, $Filter) {
    if ($Filter -match '^ProcessId = (\d+)$') { $script:fixtures | Where-Object ProcessId -eq ([int]$Matches[1]) }
    elseif ($Filter -match '^ParentProcessId = (\d+)$') { $script:fixtures | Where-Object ParentProcessId -eq ([int]$Matches[1]) }
    else { throw 'Unexpected process query.' }
}
function Stop-Process($Id, [switch]$Force) {
    $script:stopped += $Id
    $script:fixtures = @($script:fixtures | Where-Object ProcessId -ne $Id)
}
Stop-ManagedServer $share
Assert (($script:stopped -join ',') -eq '101,100') 'Stop the verified child before its launcher.'
Assert ($script:fixtures.Count -eq 0) 'Both owned Python processes must be stopped.'

# Start against fake processes/HTTP responses; never launch a tunnel or write metadata.
function Save-Share($Share) { $script:lastSaved = $Share.Clone() }
function Get-NetTCPConnection {
    if ($script:fixtures.ProcessId -contains 101) { [pscustomobject]@{OwningProcess=101} }
}
function Invoke-WebRequest { [pscustomobject]@{StatusCode=200; Content='<title>News Alarm - test</title>'} }
$script:launches = @()
function Start-Process($FilePath, $ArgumentList) {
    $script:launches += $FilePath
    if ($FilePath -eq $tunnelExecutable) {
        $script:fixtures += [pscustomobject]@{ProcessId=200; ExecutablePath=$FilePath; CommandLine=('"' + $FilePath + '" ' + ($ArgumentList -join ' ')); CreationDate=$created}
        return [pscustomobject]@{Id=200}
    }
    Assert ($FilePath -eq $pythonExecutable) 'Unexpected executable.'
    $script:fixtures += [pscustomobject]@{ProcessId=100; ParentProcessId=1; ExecutablePath=$FilePath; CommandLine=('"' + $FilePath + '" ' + $ArgumentList); CreationDate=$created}
    $script:fixtures += [pscustomobject]@{ProcessId=101; ParentProcessId=100; ExecutablePath=$basePython; CommandLine=('"' + $basePython + '" ' + $ArgumentList); CreationDate=$created.AddMilliseconds(10)}
    return [pscustomobject]@{Id=100; HasExited=$false}
}
$tunnel.CommandLine = $tunnel.CommandLine.Replace('8765', '8873')
$script:fixtures = @($tunnel)
$share.server_pid = 0
Start-ManagedServer $share
Assert ($script:launches.Count -eq 1) 'Reuse the live tunnel without changing its URL.'
Assert ($script:lastSaved.public_url -eq 'https://test-news.trycloudflare.com') 'Existing share URL changed.'
Assert ($script:lastSaved.server_pid -eq 100) 'New server identity must be saved.'
$script:fixtures = @()
$script:launches = @()
$share = @{}
function Get-Content { 'A test feed is not fetched. https://new-news-test.trycloudflare.com' }
Start-ManagedServer $share
Assert (($script:launches -join ',') -eq (@($tunnelExecutable, $pythonExecutable) -join ',')) 'Start the missing tunnel before the server.'
Assert ($share.public_url -eq 'https://new-news-test.trycloudflare.com') 'New URL must be passed to the authenticated server.'
Write-Output 'Server switch identity, stop, existing-tunnel and new-tunnel checks passed (offline mocks).'
