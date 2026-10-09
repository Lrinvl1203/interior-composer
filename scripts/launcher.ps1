param(
    [ValidateSet('Start', 'Stop', 'Status')][string]$Action = 'Start',
    [switch]$NoBrowser,
    [switch]$Quiet,
    [switch]$Production
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd('\')
$stateDir = Join-Path $root '.local\launcher'
$python = Join-Path $root '.venv\Scripts\python.exe'
$next = Join-Path $root 'frontend\node_modules\next\dist\bin\next'
$frontendDir = Join-Path $root 'frontend'
$hashAlgorithm = [Security.Cryptography.SHA256]::Create()
try { $rootHash = ([BitConverter]::ToString($hashAlgorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant())))).Replace('-', '').Substring(0, 12) }
finally { $hashAlgorithm.Dispose() }
$mutex = New-Object Threading.Mutex($false, "Local\InteriorComposer-$rootHash")
$locked = $false
$started = New-Object 'System.Collections.Generic.List[string]'

function Get-Port([string]$Name, [int]$Fallback) {
    $raw = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $envFile = Join-Path $root '.env'
        if (Test-Path -LiteralPath $envFile -PathType Leaf) {
            foreach ($line in (Get-Content -LiteralPath $envFile)) {
                if ($line -match '^\s*(INTERIOR_FRONTEND_PORT|INTERIOR_BACKEND_PORT)\s*=\s*(.*?)\s*$' -and $Matches[1] -eq $Name) {
                    $raw = $Matches[2].Trim().Trim('"').Trim("'")
                }
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($raw)) { return $Fallback }
    $value = 0
    if (-not [int]::TryParse($raw, [ref]$value) -or $value -lt 1024 -or $value -gt 65535) { throw "$Name must be an integer from 1024 to 65535." }
    return $value
}

function Get-Record([string]$Name) {
    $path = Join-Path $stateDir "$Name.json"
    if (Test-Path -LiteralPath $path) { return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json }
    return $null
}

function Get-OwnedProcess($Identity) {
    if (-not $Identity -or $Identity.projectRoot -ne $root) { return $null }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($Identity.processId)" -ErrorAction SilentlyContinue
    if ($process -and $process.CreationDate.ToUniversalTime().Ticks.ToString() -eq $Identity.creationTicks -and $process.CommandLine -eq $Identity.commandLine) { return $process }
    return $null
}

function Get-Identity($Process) {
    return [pscustomobject]@{ projectRoot=$root; processId=[int]$Process.ProcessId; creationTicks=$Process.CreationDate.ToUniversalTime().Ticks.ToString(); commandLine=$Process.CommandLine }
}

function Get-Tree($RootProcess) {
    $all = @(Get-CimInstance Win32_Process)
    $found = New-Object 'System.Collections.Generic.Dictionary[int,object]'
    $found.Add([int]$RootProcess.ProcessId, $RootProcess)
    do {
        $added = $false
        foreach ($entry in $all) {
            if ($found.ContainsKey([int]$entry.ProcessId) -or -not $found.ContainsKey([int]$entry.ParentProcessId)) { continue }
            if ($entry.CreationDate -ge $found[[int]$entry.ParentProcessId].CreationDate) {
                $found.Add([int]$entry.ProcessId, $entry)
                $added = $true
            }
        }
    } while ($added)
    return @($found.Values)
}

function Get-Listeners([int]$Port) {
    return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Save-Record([string]$Name, $Record, $RootProcess) {
    $Record | Add-Member -NotePropertyName processes -NotePropertyValue @(Get-Tree $RootProcess | ForEach-Object { Get-Identity $_ }) -Force
    $Record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $stateDir "$Name.json") -Encoding UTF8
}

function Test-Health([string]$Name, [int]$Port) {
    $path = if ($Name -eq 'backend') { '/api/health' } else { '/tools/interior-composer' }
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$path" -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    } catch { return $false }
}

function Get-ServiceState([string]$Name, [int]$Port) {
    $record = Get-Record $Name
    $rootProcess = Get-OwnedProcess $record
    $identities = @($record) + @($record.processes)
    $owned = @($identities | ForEach-Object { Get-OwnedProcess $_ } | Where-Object { $_ })
    $listeners = @(Get-Listeners $Port)
    $listenerOwned = $listeners.Count -gt 0 -and @($listeners | Where-Object { $_.OwningProcess -notin @($owned | ForEach-Object ProcessId) }).Count -eq 0
    if ($rootProcess -and $listeners.Count -gt 0) {
        $treeIds = @(Get-Tree $rootProcess | ForEach-Object ProcessId)
        $listenerOwned = @($listeners | Where-Object { $_.OwningProcess -notin $treeIds }).Count -eq 0
        if ($listenerOwned) { Save-Record $Name $record $rootProcess }
    }
    return [pscustomobject]@{ record=$record; rootProcess=$rootProcess; owned=$owned; listeners=$listeners; listenerOwned=$listenerOwned; healthy=($listenerOwned -and (Test-Health $Name $Port)) }
}

function Stop-ServiceOwned([string]$Name, [int]$Port) {
    $record = Get-Record $Name
    if (-not $record) { return }
    if ($record.port) { $Port = [int]$record.port }
    $rootProcess = Get-OwnedProcess $record
    $identities = @()
    if ($rootProcess) {
        $tree = @(Get-Tree $rootProcess)
        [array]::Reverse($tree)
        $identities += @($tree | ForEach-Object { Get-Identity $_ })
    }
    $saved = @($record.processes)
    [array]::Reverse($saved)
    $identities += $saved
    $identities += @($record)
    $seen = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($identity in $identities) {
        if (-not $identity -or -not $seen.Add([int]$identity.processId)) { continue }
        $process = Get-OwnedProcess $identity
        if ($process) { Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop }
    }
    $deadline = (Get-Date).AddSeconds(10)
    do {
        $remaining = @($identities | ForEach-Object { Get-OwnedProcess $_ } | Where-Object { $_ })
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)
    if ($remaining.Count -gt 0) { throw "Could not stop $Name PID $($remaining[0].ProcessId); ownership record retained." }
    $listeners = @(Get-Listeners $Port)
    if ($listeners.Count -gt 0) { throw "Port $Port remains occupied by another process. It was not stopped; ownership record retained." }
    Remove-Item -LiteralPath (Join-Path $stateDir "$Name.json")
}

function Start-ServiceOwned([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory, [int]$Port) {
    $state = Get-ServiceState $Name $Port
    if ($state.healthy) { return }
    if ($state.listeners.Count -gt 0) { throw "Port $Port is in use; $Name cannot start. No unmanaged process was stopped." }
    if ($state.owned.Count -gt 0) { throw "$Name has an owned process without a healthy listener. Run stop-composer.bat first." }
    if (-not (Test-Path -LiteralPath $Executable)) { throw "Missing runtime: $Executable. Run install.bat first." }
    $launch = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stateDir "$Name.out.log") -RedirectStandardError (Join-Path $stateDir "$Name.err.log")
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($launch.Id)"
    if (-not $process) { throw "$Name exited during startup. Check $stateDir logs." }
    $record = Get-Identity $process
    $record | Add-Member -NotePropertyName port -NotePropertyValue $Port
    Save-Record $Name $record $process
    $started.Add($Name)
    $deadline = (Get-Date).AddSeconds(90)
    do {
        $state = Get-ServiceState $Name $Port
        if ($state.healthy) { return }
        if ($state.listeners.Count -gt 0 -and -not $state.listenerOwned) { throw "Another process took port $Port; startup cancelled." }
        if ($state.owned.Count -eq 0) { throw "$Name exited during startup. Check $stateDir\$Name.err.log" }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "$Name health check timed out. Check $stateDir logs."
}

try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another Interior Composer launcher operation is in progress.' }
    [void](New-Item -ItemType Directory -Path $stateDir -Force)
    $backendPort = Get-Port 'INTERIOR_BACKEND_PORT' 18877
    $frontendPort = Get-Port 'INTERIOR_FRONTEND_PORT' 3100
    if ($backendPort -eq $frontendPort) { throw 'Backend and frontend ports must differ.' }
    $env:INTERIOR_BACKEND_PORT = "$backendPort"
    $env:INTERIOR_FRONTEND_PORT = "$frontendPort"
    if ($Action -eq 'Stop') {
        Stop-ServiceOwned 'frontend' $frontendPort
        Stop-ServiceOwned 'backend' $backendPort
        Write-Output 'Interior Composer stopped (or already stopped). Other applications were not closed.'
    } elseif ($Action -eq 'Status') {
        foreach ($pair in @(@('backend', $backendPort), @('frontend', $frontendPort))) {
            $state = Get-ServiceState $pair[0] $pair[1]
            $status = if ($state.healthy) { 'READY' } elseif ($state.listeners.Count -gt 0) { 'PORT_OCCUPIED_OR_UNHEALTHY' } elseif ($state.owned.Count -gt 0) { 'OWNED_PROCESS_UNHEALTHY' } else { 'STOPPED' }
            Write-Output "$($pair[0]): $status (127.0.0.1:$($pair[1]))"
        }
    } else {
        $node = (Get-Command node.exe -ErrorAction Stop).Source
        if (-not (Test-Path -LiteralPath $next)) { throw 'Missing frontend/node_modules. Run install.bat first.' }
        Start-ServiceOwned 'backend' $python @('-m', 'uvicorn', 'webapp.server:app', '--host', '127.0.0.1', '--port', "$backendPort") $root $backendPort
        $mode = if ($Production) { 'start' } else { 'dev' }
        if ($Production -and -not (Test-Path -LiteralPath (Join-Path $frontendDir '.next\BUILD_ID') -PathType Leaf)) { throw 'Production build missing. Run npm run build in frontend first.' }
        $nextArgs = @(('"' + $next + '"'), $mode)
        if (-not $Production) { $nextArgs += '--webpack' }
        $nextArgs += @('--hostname', '127.0.0.1', '--port', "$frontendPort")
        Start-ServiceOwned 'frontend' $node $nextArgs $frontendDir $frontendPort
        $url = "http://127.0.0.1:$frontendPort/tools/interior-composer"
        if (-not $NoBrowser) { Start-Process $url }
        Write-Output "READY $url"
    }
} catch {
    $message = $_.Exception.Message
    if ($Action -eq 'Start') {
        for ($i = $started.Count - 1; $i -ge 0; $i--) {
            try {
                $port = if ($started[$i] -eq 'frontend') { $frontendPort } else { $backendPort }
                Stop-ServiceOwned $started[$i] $port
            } catch { $message += "`nCleanup: $($_.Exception.Message)" }
        }
    }
    [Console]::Error.WriteLine("$message`nLogs: $stateDir")
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
