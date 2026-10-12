param(
    [ValidateSet('Toggle', 'Start', 'Stop', 'Status', 'InstallShortcut')][string]$Mode = 'Toggle',
    [switch]$NoPopup
)
$ErrorActionPreference = 'Stop'
$pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$basePython = ((Get-Content -Encoding UTF8 -LiteralPath (Join-Path $PSScriptRoot '.venv\pyvenv.cfg') |
    Where-Object { $_ -like 'executable = *' }) -replace '^executable = ', '').Trim()
$appFile = Join-Path $PSScriptRoot 'app.py'
$tunnelExecutable = Join-Path $PSScriptRoot 'data\tools\cloudflared.exe'
$shareFile = Join-Path $PSScriptRoot 'data\share-session.json'
$localUrl = 'http://127.0.0.1:8873'

function Test-ManagedProcess($Process, $Kind, $Share) {
    if (-not $Process) { return $false }
    $tokens = @([regex]::Matches([string]$Process.CommandLine, '"([^"]*)"|(\S+)') | ForEach-Object {
        if ($_.Groups[1].Success) { $_.Groups[1].Value } else { $_.Groups[2].Value }
    })
    if ($tokens.Count -lt 2) { return $false }
    $arguments = $tokens[1..($tokens.Count - 1)] -join "`n"
    if ($Kind -eq 'server') {
        if ($Process.ExecutablePath -notin @($pythonExecutable, $basePython)) { return $false }
        if ([string]$Share.public_url -notmatch '^https://[a-z0-9-]+\.trycloudflare\.com$') { return $false }
        # Accept the original relative launch as well as this switch's absolute path.
        $matchesCommand = $false
        foreach ($entry in @('app.py', $appFile)) {
            if ($arguments -ceq (@('-u', $entry, 'serve', '--public-url', $Share.public_url) -join "`n")) { $matchesCommand = $true }
        }
    } else {
        if ($Process.ExecutablePath -ne $tunnelExecutable) { return $false }
        $matchesCommand = $arguments -ceq (@('tunnel', '--no-autoupdate', '--url', $localUrl) -join "`n")
    }
    $started = $Share["${Kind}_started"]
    if ($started -and $Process.ProcessId -eq $Share["${Kind}_pid"] -and
        $Process.CreationDate.ToUniversalTime().Ticks -ne ([datetime]$started).ToUniversalTime().Ticks) { return $false }
    return $matchesCommand
}

function Get-ManagedProcess($Kind, $Share) {
    $processId = [int]$Share["${Kind}_pid"]
    if ($processId -le 0) { return }
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
    if (Test-ManagedProcess $candidate $Kind $Share) { return $candidate }
}

function Get-ServerProcesses($Share) {
    $parent = Get-ManagedProcess 'server' $Share
    if (-not $parent) { return }
    $parent
    # Windows venv Python has a launcher and a base-Python child that owns the port.
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $($parent.ProcessId)" | Where-Object {
        (Test-ManagedProcess $_ 'server' $Share) -and $_.CreationDate -ge $parent.CreationDate
    }
}

function Stop-ManagedServer($Share) {
    $processes = @(Get-ServerProcesses $Share)
    [array]::Reverse($processes)
    foreach ($candidate in $processes) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($candidate.ProcessId)"
        if ($current -and $current.CreationDate -eq $candidate.CreationDate -and (Test-ManagedProcess $current 'server' $Share)) {
            # The venv launcher may exit by itself immediately after its child exits.
            Stop-Process -Id $current.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    if (@(Get-ServerProcesses $Share).Count) { throw 'News Radar did not stop completely.' }
}

function Save-Share($Share) {
    $temporary = $shareFile + '.tmp'
    [IO.File]::WriteAllText($temporary, ($Share | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $shareFile -Force
}

function Start-ManagedServer($Share) {
    foreach ($required in @($pythonExecutable, $appFile, (Join-Path $PSScriptRoot 'data\auth.json'))) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required file missing: $required" }
    }
    if (Get-NetTCPConnection -LocalPort 8873 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Port 8873 is already occupied. No process was stopped.'
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    if (-not (Get-ManagedProcess 'tunnel' $Share)) {
        if (-not (Test-Path -LiteralPath $tunnelExecutable)) { throw 'Local cloudflared.exe is missing. See README.' }
        $Share.public_url = ''
        $Share.tunnel_log = Join-Path $PSScriptRoot "data\tunnel-$stamp.log"
        $tunnel = Start-Process -FilePath $tunnelExecutable -ArgumentList @('tunnel', '--no-autoupdate', '--url', $localUrl) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardError $Share.tunnel_log -RedirectStandardOutput (Join-Path $PSScriptRoot "data\tunnel-$stamp.out.log")
        $Share.tunnel_pid = $tunnel.Id
        $Share.tunnel_started = (Get-CimInstance Win32_Process -Filter "ProcessId = $($tunnel.Id)").CreationDate.ToUniversalTime().ToString('o')
        Save-Share $Share
    }
    if ([string]$Share.public_url -notmatch '^https://[a-z0-9-]+\.trycloudflare\.com$') {
        $deadline = (Get-Date).AddSeconds(50)
        do {
            if (-not (Get-ManagedProcess 'tunnel' $Share)) { throw 'Cloudflare tunnel exited. Check data/tunnel-*.log.' }
            $log = Get-Content -Encoding UTF8 -LiteralPath $Share.tunnel_log -Raw -ErrorAction SilentlyContinue
            if ($log -match 'https://[a-z0-9-]+\.trycloudflare\.com') { $Share.public_url = $Matches[0]; break }
            Start-Sleep -Milliseconds 250
        } while ((Get-Date) -lt $deadline)
        if (-not $Share.public_url) { throw 'A share URL is not ready. Check the tunnel log, then try again.' }
    }
    $Share.local_url = $localUrl
    $Share.server_log = Join-Path $PSScriptRoot "data\server-$stamp.log"
    $serverArguments = '-u "' + $appFile + '" serve --public-url ' + $Share.public_url
    $server = Start-Process -FilePath $pythonExecutable -ArgumentList $serverArguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardError $Share.server_log -RedirectStandardOutput (Join-Path $PSScriptRoot "data\server-$stamp.out.log")
    $Share.server_pid = $server.Id
    $Share.server_started = (Get-CimInstance Win32_Process -Filter "ProcessId = $($server.Id)").CreationDate.ToUniversalTime().ToString('o')
    Save-Share $Share
    $deadline = (Get-Date).AddSeconds(10)
    do {
        if ($server.HasExited) { throw "News Radar exited. Check $($Share.server_log)." }
        $owned = @(Get-ServerProcesses $Share | ForEach-Object { $_.ProcessId })
        $listener = Get-NetTCPConnection -LocalPort 8873 -State Listen -ErrorAction SilentlyContinue
        if ($listener -and $listener.OwningProcess -in $owned) {
            $response = Invoke-WebRequest -Uri ($localUrl + '/login') -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200 -and $response.Content -match '<title>News Alarm') { return }
        }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)
    throw "News Radar is not ready. Check $($Share.server_log)."
}

function Install-Shortcut {
    $desktop = [Environment]::GetFolderPath('Desktop')
    if (-not $desktop) { throw 'Desktop folder is unavailable for this Windows account.' }
    $shortcutPath = Join-Path $desktop 'News Radar - On Off.lnk'
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    $launcher = Join-Path $PSScriptRoot 'server-switch.vbs'
    if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.Arguments -notlike ('*"' + $launcher + '"*')) {
        throw 'A different shortcut already has this name. It was not replaced.'
    }
    $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $shortcut.Arguments = '//NoLogo "' + $launcher + '"'
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',167'
    $shortcut.Description = 'Toggle News Radar web server. Scheduled collection continues.'
    $shortcut.Save()
    Write-Output $shortcutPath
}

# Dot sourcing loads the same functions for the offline safety checks.
if ($MyInvocation.InvocationName -eq '.') { return }
try {
    if ($Mode -eq 'InstallShortcut') { Install-Shortcut; exit 0 }
    if ($Mode -ne 'Status') {
        $switchLock = [IO.File]::Open((Join-Path $PSScriptRoot 'data\server-switch.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    }
    $share = @{}
    if (Test-Path -LiteralPath $shareFile) {
        $document = Get-Content -Encoding UTF8 -LiteralPath $shareFile -Raw | ConvertFrom-Json
        foreach ($property in $document.PSObject.Properties) { $share[$property.Name] = $property.Value }
    }
    if ($Mode -eq 'Status') {
        [pscustomobject]@{ServerRunning=(@(Get-ServerProcesses $share).Count -gt 0); TunnelRunning=[bool](Get-ManagedProcess 'tunnel' $share); PublicUrl=$share.public_url} | ConvertTo-Json
        exit 0
    }
    try {
        $running = @(Get-ServerProcesses $share).Count -gt 0
        if ($Mode -eq 'Stop' -or ($Mode -eq 'Toggle' -and $running)) {
            Stop-ManagedServer $share
            $share.server_pid = 0
            $share.server_started = ''
            Save-Share $share
            $message = 'News Radar OFF. Scheduled news collection continues.'
        } else {
            if (-not $running) { Start-ManagedServer $share }
            $message = "News Radar ON.`n$($share.public_url)`nScheduled news collection continues."
        }
    } finally { $switchLock.Dispose() }
    if ($NoPopup) { Write-Output $message }
    else { [void](New-Object -ComObject WScript.Shell).Popup($message, 8, 'News Radar', 64) }
} catch {
    $_ | Out-File -LiteralPath (Join-Path $PSScriptRoot 'data\server-switch-error.log') -Append -Encoding utf8
    if ($NoPopup) { Write-Error $_ }
    else { [void](New-Object -ComObject WScript.Shell).Popup($_.Exception.Message, 15, 'News Radar - Error', 16) }
    exit 1
} finally { if ($switchLock) { $switchLock.Dispose() } }
