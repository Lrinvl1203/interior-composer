param([switch]$Quiet)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$requirements = Join-Path $root 'requirements.txt'
$frontend = Join-Path $root 'frontend'

function Invoke-Checked([string]$Executable, [string[]]$Arguments, [string]$Directory) {
    Push-Location -LiteralPath $Directory
    try {
        & $Executable @Arguments
        if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
    } finally { Pop-Location }
}

function Invoke-PythonProbe([string]$Executable, [string[]]$Arguments) {
    try {
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = $Executable
        $start.Arguments = (($Arguments | ForEach-Object {
            if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ }
        }) -join ' ')
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $process = [Diagnostics.Process]::Start($start)
        if (-not $process.WaitForExit(10000)) {
            $process.Kill()
            return [pscustomobject]@{ exitCode=1; stdout=''; stderr='Python probe timed out.' }
        }
        return [pscustomobject]@{
            exitCode=$process.ExitCode
            stdout=$process.StandardOutput.ReadToEnd().Trim()
            stderr=$process.StandardError.ReadToEnd().Trim()
        }
    } catch {
        return [pscustomobject]@{ exitCode=1; stdout=''; stderr=$_.Exception.Message }
    } finally {
        if ($process) { $process.Dispose() }
    }
}

function Get-SupportedPython {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyCommand) {
        $listed = Invoke-PythonProbe $pyCommand.Source @('-0p')
        if ($listed.exitCode -eq 0) {
            $pathPattern = '^\s+-V:\S+\s+(?:\*\s+)?(.+?python(?:3)?\.exe)\s*$'
            $rows = @($listed.stdout -split "`r?`n" | Where-Object { $_ -match $pathPattern })
            foreach ($row in ($rows | Where-Object { $_ -match '3\.12' })) {
                [void]$candidates.Add(([regex]::Match($row, $pathPattern).Groups[1].Value))
            }
            foreach ($row in ($rows | Where-Object { $_ -match '3\.11' })) {
                [void]$candidates.Add(([regex]::Match($row, $pathPattern).Groups[1].Value))
            }
            foreach ($row in ($rows | Where-Object { $_ -notmatch '3\.12|3\.11' })) {
                [void]$candidates.Add(([regex]::Match($row, $pathPattern).Groups[1].Value))
            }
        }
    }
    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notmatch '\\WindowsApps\\') { [void]$candidates.Add($command.Source) }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $probe = Invoke-PythonProbe $candidate @('-c', 'import sys; print(sys.version_info.major, sys.version_info.minor)')
        if ($probe.exitCode -ne 0 -or $probe.stdout -notmatch '^3\s+(\d+)$') { continue }
        if ([int]$Matches[1] -ge 11 -and [int]$Matches[1] -le 13) { return $candidate }
    }
    return $null
}

try {
    if (-not (Test-Path -LiteralPath $requirements)) { throw "Missing requirements.txt: $requirements" }
    if (-not (Test-Path -LiteralPath (Join-Path $frontend 'package-lock.json'))) { throw 'Missing frontend/package-lock.json; npm ci requires the lockfile.' }
    $existingVenv = Test-Path -LiteralPath $venvPython -PathType Leaf
    if ($existingVenv) {
        $venvProbe = Invoke-PythonProbe $venvPython @('-c', 'import sys; print(sys.version_info.major, sys.version_info.minor)')
        if ($venvProbe.exitCode -ne 0 -or $venvProbe.stdout -notmatch '^3\s+(\d+)$' -or [int]$Matches[1] -lt 11 -or [int]$Matches[1] -gt 13) {
            throw 'Existing .venv is unusable or uses Python outside the supported 3.11–3.13 range. Inspect it before reinstalling.'
        }
        Write-Output "Using existing Python environment: $venvPython"
    } else {
        $pythonExecutable = Get-SupportedPython
        if (-not $pythonExecutable) { throw 'Python 3.12 (recommended) or another supported Python 3.11–3.13 installation is required.' }
        Write-Output "Using Python: $pythonExecutable"
    }
    $nodeVersion = (& node.exe -p 'process.versions.node').Trim()
    if ($LASTEXITCODE -ne 0 -or [version]$nodeVersion -lt [version]'22.0.0') { throw 'Node.js 22 or newer is required.' }
    $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    if (-not $existingVenv) {
        Invoke-Checked $pythonExecutable @('-m', 'venv', (Join-Path $root '.venv')) $root
    }
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '--upgrade', 'pip>=26.2,<27') $root
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '-r', $requirements) $root
    Invoke-Checked $npm @('ci') $frontend
    $envPath = Join-Path $root '.env'
    $examplePath = Join-Path $root '.env.example'
    if (-not (Test-Path -LiteralPath $envPath) -and (Test-Path -LiteralPath $examplePath)) {
        Copy-Item -LiteralPath $examplePath -Destination $envPath
        Write-Output 'Created .env from .env.example. Add your own API keys only if needed.'
    }
    Write-Output 'Interior Composer installed. Run start-composer.bat to start local servers.'
    Write-Output 'Optional SAM/large GPU dependencies are not installed by default.'
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
