$ErrorActionPreference = 'Stop'
$script:ComposerRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd('\')
$script:RuntimeRoot = Join-Path $script:ComposerRoot '.local\runtime'
$script:UvVersion = '0.13.0'
$script:NodeVersion = '22.23.3'
$script:UvHash = '088962f9e7b7bd9ea740c04c650b2a21c8928c345bd99ac24350dc924dba656c'
$script:NodeHash = '2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71'

function Assert-WindowsX64 {
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'Automatic portable runtime installation supports Windows x64 only.'
    }
}

function Invoke-ComposerProbe([string]$Executable, [string[]]$Arguments, [int]$TimeoutMs = 30000) {
    try {
        $psi = New-Object Diagnostics.ProcessStartInfo
        $psi.FileName = $Executable
        $psi.Arguments = (($Arguments | ForEach-Object {
            if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ }
        }) -join ' ')
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $process = [Diagnostics.Process]::Start($psi)
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMs)) {
            $process.Kill()
            throw 'Probe timed out.'
        }
        return [pscustomobject]@{ exitCode=$process.ExitCode; stdout=$stdout.GetAwaiter().GetResult().Trim(); stderr=$stderr.GetAwaiter().GetResult().Trim() }
    } catch {
        return [pscustomobject]@{ exitCode=1; stdout=''; stderr=$_.Exception.Message }
    } finally {
        if ($process) { $process.Dispose() }
    }
}

function Assert-ArchiveEntry([string]$BaseDirectory, [string]$EntryName) {
    if ([string]::IsNullOrWhiteSpace($EntryName) -or $EntryName -match '(^[\\/]|:|(^|[\\/])\.\.([\\/]|$))') {
        throw "Unsafe archive entry: $EntryName"
    }
    $base = [IO.Path]::GetFullPath($BaseDirectory).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $target = [IO.Path]::GetFullPath((Join-Path $base $EntryName.Replace('/', '\')))
    if (-not $target.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe archive entry: $EntryName" }
    return $target
}

function Expand-VerifiedArchive([string]$Archive, [string]$Destination, [string]$ExpectedHash) {
    $actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedHash) { throw "SHA256 mismatch for $Archive. Expected $ExpectedHash, got $actual. No file was executed or extracted." }
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        $total = [long]0
        foreach ($entry in $zip.Entries) {
            $null = Assert-ArchiveEntry $Destination $entry.FullName
            $mode = ($entry.ExternalAttributes -shr 16) -band 0xF000
            if ($mode -eq 0xA000) { throw "Archive symlink refused: $($entry.FullName)" }
            $total += $entry.Length
            if ($entry.Length -gt 1073741824 -or $total -gt 1073741824) { throw 'Archive exceeds the 1 GiB extraction safety limit.' }
        }
        foreach ($entry in $zip.Entries) {
            $target = Assert-ArchiveEntry $Destination $entry.FullName
            if ($entry.FullName.EndsWith('/')) { [void](New-Item -ItemType Directory -Path $target -Force); continue }
            [void](New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force)
            if (Test-Path -LiteralPath $target) { throw "Portable runtime target already exists: $target" }
            $inputStream = $entry.Open()
            $outputStream = [IO.File]::Open($target, [IO.FileMode]::CreateNew)
            try { $inputStream.CopyTo($outputStream) }
            finally { $outputStream.Dispose(); $inputStream.Dispose() }
        }
    } finally { $zip.Dispose() }
}

function Get-VerifiedArchive([string]$Uri, [string]$Name, [string]$Hash) {
    $downloads = Join-Path $script:RuntimeRoot 'downloads'
    [void](New-Item -ItemType Directory -Path $downloads -Force)
    $path = Join-Path $downloads $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $partial = Join-Path $downloads ($Name + '.partial')
        if (Test-Path -LiteralPath $partial) { throw "Incomplete prior download requires inspection: $partial" }
        Write-Host "Downloading and verifying project-local runtime archive: $Name"
        try { Invoke-WebRequest -Uri $Uri -OutFile $partial -UseBasicParsing -TimeoutSec 600 }
        catch {
            if (Test-Path -LiteralPath $partial -PathType Leaf) { Remove-Item -LiteralPath $partial -Force }
            throw
        }
        $actual = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $Hash) { throw "SHA256 mismatch for downloaded $Name. Expected $Hash, got $actual. Partial file retained for inspection." }
        Move-Item -LiteralPath $partial -Destination $path
    }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Hash) { throw "SHA256 mismatch for cached $Name. No file was executed or extracted." }
    return $path
}

function Get-PortableNode {
    Assert-WindowsX64
    $destination = Join-Path $script:RuntimeRoot 'node'
    $nodeDir = Join-Path $destination "node-v$($script:NodeVersion)-win-x64"
    $node = Join-Path $nodeDir 'node.exe'
    if (-not (Test-Path -LiteralPath $node -PathType Leaf)) {
        if (Test-Path -LiteralPath $destination) { throw "Incomplete portable Node directory requires inspection: $destination" }
        $name = "node-v$($script:NodeVersion)-win-x64.zip"
        $archive = Get-VerifiedArchive "https://nodejs.org/dist/v$($script:NodeVersion)/$name" $name $script:NodeHash
        Expand-VerifiedArchive $archive $destination $script:NodeHash
    }
    $probe = Invoke-ComposerProbe $node @('-p', 'process.versions.node')
    if ($probe.exitCode -ne 0 -or $probe.stdout -ne $script:NodeVersion) { throw "Portable Node version check failed: $node" }
    return $node
}

function Get-PortableUv {
    Assert-WindowsX64
    $destination = Join-Path $script:RuntimeRoot 'uv'
    $uv = Join-Path $destination 'uv.exe'
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        if (Test-Path -LiteralPath $destination) { throw "Incomplete portable uv directory requires inspection: $destination" }
        $name = 'uv-x86_64-pc-windows-msvc.zip'
        $archive = Get-VerifiedArchive "https://github.com/astral-sh/uv/releases/download/$($script:UvVersion)/$name" $name $script:UvHash
        Expand-VerifiedArchive $archive $destination $script:UvHash
    }
    $probe = Invoke-ComposerProbe $uv @('--version')
    if ($probe.exitCode -ne 0 -or $probe.stdout -notmatch "^uv $([regex]::Escape($script:UvVersion))\b") { throw "Portable uv version check failed: $uv" }
    return $uv
}

function Set-PortableUvEnvironment {
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $script:RuntimeRoot 'python'
    $env:UV_PYTHON_BIN_DIR = Join-Path $script:RuntimeRoot 'python-bin'
    $env:UV_CACHE_DIR = Join-Path $script:RuntimeRoot 'cache'
    $env:UV_TOOL_DIR = Join-Path $script:RuntimeRoot 'tools'
}

function Get-CompatiblePython([switch]$ForcePortable) {
    $venv = Join-Path $script:ComposerRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv -PathType Leaf) {
        $probe = Invoke-ComposerProbe $venv @('-c', 'import sys; print(sys.version_info.major, sys.version_info.minor)')
        if ($probe.exitCode -ne 0 -or $probe.stdout -notmatch '^3\s+(11|12|13)$') { throw 'Existing .venv is unusable or unsupported; it was not overwritten.' }
        return $venv
    }
    if (-not $ForcePortable) {
        $candidates = New-Object 'System.Collections.Generic.List[string]'
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($py) {
            foreach ($version in @('3.13','3.12','3.11')) {
                $result = Invoke-ComposerProbe $py.Source @("-$version", '-c', 'import sys; print(sys.executable)')
                if ($result.exitCode -eq 0 -and $result.stdout) { [void]$candidates.Add($result.stdout) }
            }
        }
        foreach ($name in @('python.exe','python3.exe')) {
            $command = Get-Command $name -ErrorAction SilentlyContinue
            if ($command -and $command.Source -notmatch '\\WindowsApps\\') { [void]$candidates.Add($command.Source) }
        }
        foreach ($candidate in ($candidates | Select-Object -Unique)) {
            $result = Invoke-ComposerProbe $candidate @('-c', 'import sys; print(sys.version_info.major, sys.version_info.minor)')
            if ($result.exitCode -eq 0 -and $result.stdout -match '^3\s+(11|12|13)$') { return $candidate }
        }
    }
    Set-PortableUvEnvironment
    $uv = Get-PortableUv
    Write-Host 'Installing project-local Python 3.13 with uv (up to 10 minutes)...'
    $install = Invoke-ComposerProbe $uv @('python','install','3.13','--quiet') -TimeoutMs 600000
    if ($install.exitCode -ne 0) { throw "uv could not install project-local Python 3.13 (exit $($install.exitCode)): $($install.stderr)" }
    $find = Invoke-ComposerProbe $uv @('python','find','--managed-python','3.13')
    $python = @($find.stdout -split "`r?`n" | Where-Object { $_ }) | Select-Object -Last 1
    if ($find.exitCode -ne 0 -or -not $python -or -not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'uv did not return a project-local Python executable.' }
    $managedRoot = [IO.Path]::GetFullPath($env:UV_PYTHON_INSTALL_DIR).TrimEnd('\') + '\'
    if (-not ([IO.Path]::GetFullPath($python)).StartsWith($managedRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'uv returned a Python outside the project-local runtime.' }
    return $python
}

function Get-CompatibleNode([switch]$ForcePortable) {
    if (-not $ForcePortable) {
        $command = Get-Command node.exe -ErrorAction SilentlyContinue
        if ($command) {
            $probe = Invoke-ComposerProbe $command.Source @('-p', 'process.versions.node')
            if ($probe.exitCode -eq 0) {
                $version = $null
                if ([version]::TryParse($probe.stdout, [ref]$version) -and $version -ge [version]'22.0.0') {
                    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
                    if ($npm) { return [pscustomobject]@{ node=$command.Source; npm=$npm.Source; portable=$false } }
                }
            }
        }
    }
    $node = Get-PortableNode
    $dir = Split-Path -Parent $node
    $npm = Join-Path $dir 'npm.cmd'
    if (-not (Test-Path -LiteralPath $npm -PathType Leaf)) { throw "Portable npm is missing: $npm" }
    return [pscustomobject]@{ node=$node; npm=$npm; portable=$true }
}
