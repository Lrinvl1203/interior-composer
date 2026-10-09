param(
    [switch]$NoBrowser,
    [switch]$Quiet,
    [switch]$InstallOnly,
    [switch]$CreateDesktopIcons,
    [switch]$ForcePortableRuntimes
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd('\')
$frontendDir = Join-Path $root 'frontend'
$launcher = Join-Path $PSScriptRoot 'launcher.ps1'
$markerPath = Join-Path $root '.local\bootstrap-marker.json'

function Get-EnvStamp {
    $path = Join-Path $root '.env'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return '' }
    return (Get-Item -LiteralPath $path).LastWriteTimeUtc.Ticks.ToString()
}

function Get-SourceFingerprint {
    $files = New-Object 'System.Collections.Generic.List[IO.FileInfo]'
    foreach ($relative in @('requirements.txt','frontend\package.json','frontend\package-lock.json','frontend\next.config.ts','frontend\postcss.config.mjs','frontend\tsconfig.json','webapp\extraction_provenance.json','start-composer.bat','stop-composer.bat','install.bat')) {
        $path = Join-Path $root $relative
        if (Test-Path -LiteralPath $path -PathType Leaf) { $files.Add((Get-Item -LiteralPath $path)) }
    }
    foreach ($relative in @('frontend\app','frontend\components','frontend\lib','frontend\public','webapp','webapp\data\finish_library','room-asset-composer\services','scripts')) {
        $path = Join-Path $root $relative
        if (-not (Test-Path -LiteralPath $path -PathType Container)) { continue }
        $include = if ($relative -eq 'webapp') { '*.py' } elseif ($relative -eq 'scripts') { '*.ps1' } else { '*' }
        $recursive = $relative -notin @('webapp','scripts')
        foreach ($file in (Get-ChildItem -LiteralPath $path -File -Filter $include -Recurse:$recursive)) {
            if ($file.FullName -match '\\(__pycache__|node_modules|\.next|tmp|logs)\\') { continue }
            $files.Add($file)
        }
    }
    $lines = New-Object 'System.Collections.Generic.List[string]'
    foreach ($file in ($files | Sort-Object FullName -Unique)) {
        $relative = $file.FullName.Substring($root.Length + 1).Replace('\','/')
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $lines.Add("$relative $hash")
    }
    $joined = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($joined))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-RequestedPort([string]$Name, [int]$Fallback) {
    $raw = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $portFile = Join-Path $root '.local\ports.json'
        if (Test-Path -LiteralPath $portFile -PathType Leaf) {
            $ports = Get-Content -LiteralPath $portFile -Raw | ConvertFrom-Json
            $raw = if ($Name -eq 'INTERIOR_FRONTEND_PORT') { "$($ports.frontend)" } else { "$($ports.backend)" }
        }
    }
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
    if (-not [int]::TryParse($raw, [ref]$value) -or $value -lt 1024 -or $value -gt 65535) { throw "$Name must be a port from 1024 to 65535." }
    return $value
}

function Test-PortAvailable([int]$Port) {
    $listener = $null
    try {
        $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch { return $false }
    finally { if ($listener) { $listener.Stop() } }
}

function Find-FreePort([int]$Preferred, [int]$Other) {
    if ($Preferred -ne $Other -and (Test-PortAvailable $Preferred)) { return $Preferred }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
        try {
            $listener.Start()
            $port = [int]$listener.LocalEndpoint.Port
        } finally { $listener.Stop() }
        if ($port -ne $Other) { return $port }
    }
    throw 'Could not find a free local port.'
}

function Write-BootstrapResult([string]$Status, [int]$FrontendPort, [int]$BackendPort) {
    $providerStatus = 'not_checked'
    $apiKeyPending = $true
    if ($Status -eq 'ready') {
        $providerStatus = 'unknown'
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/ai-providers/status" -Method Get -TimeoutSec 5
            if ($response.provider -eq 'OpenAI' -and $response.configured -is [bool]) {
                $apiKeyPending = -not $response.configured
                $providerStatus = if ($response.configured) { 'configured' } else { 'pending' }
            }
        } catch { } # Local metadata probe only; never request a generation.
    }
    $result = [ordered]@{
        status=$Status
        url="http://127.0.0.1:$FrontendPort/tools/interior-composer"
        backend="http://127.0.0.1:$BackendPort/api/health"
        apiKeyPending=$apiKeyPending
        providerStatus=$providerStatus
        paidApiCallsMade=$false
    }
    Write-Output ($result | ConvertTo-Json -Compress)
}

try {
    # Keep a key from the invoking Codex/shell out of install and build subprocesses.
    Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'Automatic setup supports Windows x64 only.'
    }
    $status = @(& $launcher -Action Status)
    if ($status.Count -lt 2) { throw 'Could not check existing local service ownership.' }
    $ready = @($status | Where-Object { $_ -match ': READY ' })
    $sourceFingerprint = Get-SourceFingerprint
    $marker = $null
    if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
        $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    }
    if ($ready.Count -eq 2) {
        $frontendPort = Get-RequestedPort 'INTERIOR_FRONTEND_PORT' 3100
        $backendPort = Get-RequestedPort 'INTERIOR_BACKEND_PORT' 18877
        if (-not $ForcePortableRuntimes -and $marker -and $marker.fingerprint -eq $sourceFingerprint -and $marker.envStamp -eq (Get-EnvStamp) -and [int]$marker.frontendPort -eq $frontendPort -and [int]$marker.backendPort -eq $backendPort) {
            if ($CreateDesktopIcons) { & (Join-Path $PSScriptRoot 'desktop-shortcuts.ps1') -Quiet:$Quiet }
            Write-BootstrapResult 'ready' $frontendPort $backendPort
            exit 0
        }
        if (-not $Quiet) { Write-Output 'Source changed or install marker missing; stopping only verified-owned Composer servers before rebuild.' }
        & $launcher -Action Stop -Quiet
        if (-not $?) { throw 'Could not stop verified-owned Composer servers; no build was attempted.' }
    }
    if ($ready.Count -eq 1 -or @($status | Where-Object { $_ -match 'OWNED_PROCESS_UNHEALTHY' }).Count -gt 0) {
        throw 'An owned service is already running or unhealthy. No reinstall/build was attempted; inspect status and logs first.'
    }
    $requestedFrontend = Get-RequestedPort 'INTERIOR_FRONTEND_PORT' 3100
    $requestedBackend = Get-RequestedPort 'INTERIOR_BACKEND_PORT' 18877
    $backendPort = Find-FreePort $requestedBackend 0
    $frontendPort = Find-FreePort $requestedFrontend $backendPort
    $env:INTERIOR_BACKEND_PORT = "$backendPort"
    $env:INTERIOR_FRONTEND_PORT = "$frontendPort"
    $local = Join-Path $root '.local'
    [void](New-Item -ItemType Directory -Path $local -Force)
    [ordered]@{ frontend=$frontendPort; backend=$backendPort } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $local 'ports.json') -Encoding UTF8
    if (-not $Quiet) { Write-Output "Using local ports: frontend $frontendPort, backend $backendPort" }

    & (Join-Path $PSScriptRoot 'install.ps1') -Quiet:$Quiet -ForcePortableRuntimes:$ForcePortableRuntimes
    if (-not $?) { throw 'Dependency installation failed.' }
    $runtime = Get-Content -LiteralPath (Join-Path $local 'runtime-paths.json') -Raw | ConvertFrom-Json
    if (-not (Test-Path -LiteralPath $runtime.npm -PathType Leaf)) { throw 'Node/npm runtime record is missing or stale.' }
    $oldPath = $env:PATH
    $oldErrorAction = $ErrorActionPreference
    $env:PATH = "$(Split-Path -Parent $runtime.node);$oldPath"
    try {
        Push-Location -LiteralPath $frontendDir
        try {
            $ErrorActionPreference = 'Continue'
            if ($Quiet) { & $runtime.npm run build | Out-Null }
            else { & $runtime.npm run build }
            if ($LASTEXITCODE -ne 0) { throw "Production frontend build failed (exit $LASTEXITCODE)." }
        } finally { Pop-Location }
    } finally {
        $env:PATH = $oldPath
        $ErrorActionPreference = $oldErrorAction
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontendDir '.next\BUILD_ID') -PathType Leaf)) { throw 'Production build did not produce a BUILD_ID.' }
    if ((Get-SourceFingerprint) -ne $sourceFingerprint) { throw 'Source changed while building; no install marker was written. Re-run setup after edits finish.' }
    if ($CreateDesktopIcons) { & (Join-Path $PSScriptRoot 'desktop-shortcuts.ps1') -Quiet:$Quiet }
    if ($InstallOnly) {
        [ordered]@{ fingerprint=$sourceFingerprint; envStamp=(Get-EnvStamp); frontendPort=$frontendPort; backendPort=$backendPort; status='installed'; completed=(Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding UTF8
        Write-BootstrapResult 'installed' $frontendPort $backendPort
        exit 0
    }
    & $launcher -Action Start -Production -NoBrowser:$NoBrowser -Quiet:$Quiet
    if (-not $?) { throw 'Local server launch or health check failed.' }
    [ordered]@{ fingerprint=$sourceFingerprint; envStamp=(Get-EnvStamp); frontendPort=$frontendPort; backendPort=$backendPort; status='ready'; completed=(Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding UTF8
    Write-BootstrapResult 'ready' $frontendPort $backendPort
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
