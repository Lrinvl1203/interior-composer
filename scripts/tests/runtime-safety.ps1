$ErrorActionPreference = 'Stop'
$scripts = Split-Path -Parent $PSScriptRoot
. (Join-Path $scripts 'runtime.ps1')

foreach ($name in @('runtime.ps1','install.ps1','bootstrap.ps1','launcher.ps1','desktop-shortcuts.ps1')) {
    $tokens = $null
    $errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile((Join-Path $scripts $name), [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) { throw "PowerShell syntax invalid in $name`: $($errors[0].Message)" }
}

$base = Join-Path $env:TEMP ('composer-archive-safety-' + [guid]::NewGuid().ToString('N'))
try {
    $safe = Assert-ArchiveEntry $base 'node-v22.23.3-win-x64/node.exe'
    if (-not $safe.StartsWith(([IO.Path]::GetFullPath($base).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Safe archive entry escaped destination.'
    }
    foreach ($unsafe in @('../outside.exe','..\outside.exe','C:/outside.exe','/outside.exe','node/../../outside.exe','node:stream')) {
        $rejected = $false
        try { $null = Assert-ArchiveEntry $base $unsafe } catch { $rejected = $true }
        if (-not $rejected) { throw "Unsafe archive entry accepted: $unsafe" }
    }
    [void](New-Item -ItemType Directory -Path $base -Force)
    $badArchive = Join-Path $base 'invalid.zip'
    [IO.File]::WriteAllBytes($badArchive, [byte[]]@(1,2,3))
    $rejected = $false
    try { Expand-VerifiedArchive $badArchive (Join-Path $base 'out') ('0' * 64) } catch { $rejected = $_.Exception.Message -match 'SHA256 mismatch' }
    if (-not $rejected) { throw 'Digest mismatch was not rejected before extraction.' }
    if (Test-Path -LiteralPath (Join-Path $base 'out')) { throw 'Digest mismatch created an extraction directory.' }
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $safeArchive = Join-Path $base 'safe.zip'
    $zip = [IO.Compression.ZipFile]::Open($safeArchive, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry = $zip.CreateEntry('node-v22.23.3-win-x64/node.exe')
        $stream = $entry.Open()
        try { $stream.Write([byte[]]@(4,5,6), 0, 3) } finally { $stream.Dispose() }
    } finally { $zip.Dispose() }
    $safeHash = (Get-FileHash -LiteralPath $safeArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    Expand-VerifiedArchive $safeArchive (Join-Path $base 'safe-out') $safeHash
    if (-not (Test-Path -LiteralPath (Join-Path $base 'safe-out\node-v22.23.3-win-x64\node.exe') -PathType Leaf)) {
        throw 'Verified archive did not extract safely.'
    }
    Write-Output 'runtime-safety: PASS (syntax, archive traversal, SHA256-before-extract, safe extraction)'
} finally {
    if (Test-Path -LiteralPath $base) {
        $resolved = [IO.Path]::GetFullPath($base)
        $tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
        if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolved) -like 'composer-archive-safety-*') {
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
}
