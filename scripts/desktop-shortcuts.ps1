param([switch]$Quiet, [string]$DestinationDirectory)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd('\')
$desktop = if ($DestinationDirectory) { [IO.Path]::GetFullPath($DestinationDirectory) } else { [Environment]::GetFolderPath('Desktop') }
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) { throw 'Shortcut destination directory is unavailable.' }
$shell = New-Object -ComObject WScript.Shell
$entries = @(
    @{ name='Interior Composer START'; file='start-composer.bat'; icon=137 },
    @{ name='Interior Composer STOP'; file='stop-composer.bat'; icon=131 }
)
foreach ($entry in $entries) {
    $target = Join-Path $root $entry.file
    if (-not (Test-Path -LiteralPath $target)) { throw "Missing shortcut target: $target" }
    $path = Join-Path $desktop ($entry.name + '.lnk')
    if (Test-Path -LiteralPath $path) {
        $existing = $shell.CreateShortcut($path)
        if (-not [string]::Equals($existing.TargetPath, $target, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Shortcut name already belongs to another target: $path. It was not changed."
        }
    }
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $target
    $shortcut.Arguments = ''
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,$($entry.icon)"
    $shortcut.Description = "$($entry.name) - local standalone application"
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    if (-not $Quiet) { Write-Output $path }
}
