param(
    [string]$DestinationDirectory = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$startupPath = (Resolve-Path (Join-Path $PSScriptRoot 'start-dashboard.ps1')).Path
$powershellPath = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$destinationPath = (Resolve-Path -LiteralPath $DestinationDirectory).Path
$shortcutPath = Join-Path $destinationPath 'BSGO Mining Control.lnk'
$arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $startupPath + '"'
$description = 'Open the BSGO mining cockpit. New sessions start in Manual with actions paused.'

$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and
    ($shortcut.TargetPath -ne $powershellPath -or
     ($shortcut.Arguments -ne $arguments -and $shortcut.Description -ne $description))) {
    throw "An unrelated shortcut already exists at $shortcutPath. It was left unchanged."
}
$shortcut.TargetPath = $powershellPath
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $workspacePath
$shortcut.Description = $description
$shortcut.WindowStyle = 7
$shortcut.IconLocation = "$powershellPath,0"
$shortcut.Save()
Write-Output $shortcutPath
