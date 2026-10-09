$ErrorActionPreference = 'Stop'
$scripts = Split-Path -Parent $PSScriptRoot
$testDir = Join-Path $env:TEMP ('composer-shortcut-test-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $testDir)
try {
    & (Join-Path $scripts 'desktop-shortcuts.ps1') -Quiet -DestinationDirectory $testDir
    $links = @(Get-ChildItem -LiteralPath $testDir -Filter '*.lnk')
    if ($links.Count -ne 2) { throw 'Expected two shortcuts.' }
    Write-Output 'desktop-shortcuts: PASS (temporary destination)'
} finally {
    $resolved = [IO.Path]::GetFullPath($testDir)
    $tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolved) -like 'composer-shortcut-test-*') {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
