param([switch]$Quiet, [switch]$ForcePortableRuntimes)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime.ps1')
$root = $script:ComposerRoot
$venv = Join-Path $root '.venv\Scripts\python.exe'
$frontend = Join-Path $root 'frontend'
$local = Join-Path $root '.local'

function Assert-NoOwnedServices {
    foreach ($name in @('backend','frontend')) {
        $path = Join-Path $root ".local\launcher\$name.json"
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $record = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        if ($record.projectRoot -ne [IO.Path]::GetFullPath($root).TrimEnd('\')) { continue }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.processId)" -ErrorAction SilentlyContinue
        if ($process -and $process.CreationDate.ToUniversalTime().Ticks.ToString() -eq $record.creationTicks -and $process.CommandLine -eq $record.commandLine) {
            throw "Owned $name service is running. Use bootstrap.ps1 to update safely; direct install will not mutate a live service."
        }
    }
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments, [string]$Directory) {
    Push-Location -LiteralPath $Directory
    $previousErrorAction = $ErrorActionPreference
    try {
        # Native tools may write ordinary progress/warnings to stderr in Windows PowerShell 5.
        $ErrorActionPreference = 'Continue'
        if ($Quiet) { & $Executable @Arguments | Out-Null }
        else { & $Executable @Arguments }
        if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
    } finally {
        $ErrorActionPreference = $previousErrorAction
        Pop-Location
    }
}

try {
    Assert-WindowsX64
    Assert-NoOwnedServices
    if (-not (Test-Path -LiteralPath (Join-Path $root 'requirements.txt') -PathType Leaf)) { throw 'Missing requirements.txt.' }
    if (-not (Test-Path -LiteralPath (Join-Path $frontend 'package-lock.json') -PathType Leaf)) { throw 'Missing frontend/package-lock.json.' }
    $lockHash = (Get-FileHash -LiteralPath (Join-Path $frontend 'package-lock.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    $lockHashPath = Join-Path $local 'npm-lock.sha256'
    $installedHash = if (Test-Path -LiteralPath $lockHashPath -PathType Leaf) { (Get-Content -LiteralPath $lockHashPath -Raw).Trim() } else { '' }
    if (-not $Quiet) { Write-Output 'Checking compatible Python (project-local verified download if needed)...' }
    $python = Get-CompatiblePython -ForcePortable:$ForcePortableRuntimes
    if (-not $Quiet) { Write-Output 'Checking compatible Node.js (project-local verified download if needed)...' }
    $node = Get-CompatibleNode -ForcePortable:$ForcePortableRuntimes
    $nodeDir = Split-Path -Parent $node.node
    $oldPath = $env:PATH
    $oldPipCache = $env:PIP_CACHE_DIR
    $oldNpmCache = $env:npm_config_cache
    $env:PATH = "$nodeDir;$oldPath"
    $env:PIP_CACHE_DIR = Join-Path $script:RuntimeRoot 'pip-cache'
    $env:npm_config_cache = Join-Path $script:RuntimeRoot 'npm-cache'
    try {
        if (-not (Test-Path -LiteralPath $venv -PathType Leaf)) {
            if (Test-Path -LiteralPath (Join-Path $root '.venv')) { throw 'Existing incomplete .venv was not overwritten; inspect it before retrying.' }
            Invoke-Checked $python @('-m', 'venv', (Join-Path $root '.venv')) $root
        }
        if (-not $Quiet) { Write-Output 'Installing Python dependencies into project .venv...' }
        Invoke-Checked $venv @('-m', 'pip', 'install', '--upgrade', 'pip>=26.2,<27') $root
        Invoke-Checked $venv @('-m', 'pip', 'install', '-r', (Join-Path $root 'requirements.txt')) $root
        if (-not (Test-Path -LiteralPath (Join-Path $frontend 'node_modules\next\dist\bin\next') -PathType Leaf) -or $installedHash -ne $lockHash) {
            if (-not $Quiet) { Write-Output 'Installing frontend dependencies from package-lock.json...' }
            Invoke-Checked $node.npm @('ci') $frontend
            [void](New-Item -ItemType Directory -Path $local -Force)
            Set-Content -LiteralPath $lockHashPath -Value $lockHash -Encoding ASCII
        }
    } finally {
        $env:PATH = $oldPath
        $env:PIP_CACHE_DIR = $oldPipCache
        $env:npm_config_cache = $oldNpmCache
    }
    $envPath = Join-Path $root '.env'
    $examplePath = Join-Path $root '.env.example'
    if (-not (Test-Path -LiteralPath $envPath) -and (Test-Path -LiteralPath $examplePath)) {
        Copy-Item -LiteralPath $examplePath -Destination $envPath
        if (-not $Quiet) { Write-Output 'Created .env from .env.example; no API key is required for installation.' }
    }
    $runtimeRecord = [ordered]@{ node=$node.node; npm=$node.npm; python=$venv; portableNode=$node.portable; created=(Get-Date).ToUniversalTime().ToString('o') }
    [void](New-Item -ItemType Directory -Path $local -Force)
    $runtimeRecord | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $local 'runtime-paths.json') -Encoding UTF8
    if (-not $Quiet) { Write-Output 'Interior Composer dependencies installed locally. API key can be added later.' }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
