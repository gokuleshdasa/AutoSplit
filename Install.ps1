# AutoSplit installer
# Installs the built distribution to %LOCALAPPDATA%\Programs\AutoSplit,
# registers the Explorer right-click menu, and adds a Start Menu shortcut.
#
# Run:
#   powershell -ExecutionPolicy Bypass -File .\Install.ps1
#
# No administrator rights required (per-user install).

$ErrorActionPreference = "Stop"

$AppName    = "AutoSplit"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$StartMenu  = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$ShortcutFn = Join-Path $StartMenu "$AppName.lnk"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Exe  = Join-Path $Here "$AppName.exe"

if (-not (Test-Path -LiteralPath $Exe)) {
    Write-Host "Cannot find $AppName.exe next to this installer." -ForegroundColor Red
    Write-Host "Expected: $Exe" -ForegroundColor Red
    exit 1
}

Write-Host "Installing $AppName..." -ForegroundColor Cyan
Write-Host "  Source: $Here"
Write-Host "  Target: $InstallDir"

# Stop any running instance so we can overwrite files.
Get-Process $AppName -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  Stopping running $AppName (PID $($_.Id))" -ForegroundColor Yellow
    $_ | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
}

# Fresh copy.
if (Test-Path -LiteralPath $InstallDir) {
    Write-Host "  Removing previous install..." -ForegroundColor DarkGray
    Remove-Item -Recurse -Force -LiteralPath $InstallDir
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Write-Host "  Copying files..." -ForegroundColor DarkGray
# robocopy is faster and reliable than Copy-Item -Recurse for many files;
# exit codes 0-7 all mean success.
$rc = & robocopy $Here $InstallDir /E /NFL /NDL /NJH /NJS /NP /MT:8
if ($LASTEXITCODE -ge 8) {
    Write-Host "File copy failed (robocopy exit code $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

$InstalledExe = Join-Path $InstallDir "$AppName.exe"

# --- Explorer right-click menu (nested: Split / Join Quick / Join Custom) ---
Write-Host "  Registering Explorer context menu..." -ForegroundColor DarkGray

$VIDEO_EXTS = @(".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")
$AUDIO_EXTS = @(".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus")
$AllExts    = $VIDEO_EXTS + $AUDIO_EXTS

$SubCmdsRoot = "HKCU:\Software\Classes\AutoSplitCommands"
$SubShell    = "$SubCmdsRoot\shell"

# Wipe the old flat entries and prior sub-commands so we start clean.
foreach ($ext in $AllExts) {
    $parent = "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell\AutoSplitMedia"
    if (Test-Path -LiteralPath $parent) { Remove-Item -Recurse -Force -LiteralPath $parent }
}
if (Test-Path -LiteralPath $SubCmdsRoot) { Remove-Item -Recurse -Force -LiteralPath $SubCmdsRoot }

# Create the sub-commands.
$Entries = @(
    @{ Key = "01Split";      Verb = "Split&#8230;";              MultiSelect = "Single"; Args = '"%1"'                    },
    @{ Key = "02JoinQuick";  Verb = "Join (Quick)";              MultiSelect = "Player"; Args = '--join-quick "%1"'       },
    @{ Key = "03JoinCustom"; Verb = "Join (Customize)&#8230;";   MultiSelect = "Player"; Args = '--join-custom "%1"'      }
)
# Fix up the &#8230; (ellipsis). Raw glyph is avoided so this file stays ASCII.
foreach ($e in $Entries) { $e.Verb = $e.Verb -replace '&#8230;', [char]0x2026 }

foreach ($e in $Entries) {
    $item    = "$SubShell\$($e.Key)"
    $cmdKey  = "$item\command"
    New-Item -Path $item -Force | Out-Null
    New-Item -Path $cmdKey -Force | Out-Null
    Set-ItemProperty -Path $item -Name "MUIVerb"           -Value $e.Verb
    Set-ItemProperty -Path $item -Name "MultiSelectModel"  -Value $e.MultiSelect
    Set-ItemProperty -Path $item -Name "Icon"              -Value $InstalledExe
    $cmdLine = "`"$InstalledExe`" $($e.Args)"
    Set-ItemProperty -Path $cmdKey -Name "(Default)" -Value $cmdLine
}

# Attach the parent (nested) menu to every supported extension.
foreach ($ext in $AllExts) {
    $parent = "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell\AutoSplit"
    New-Item -Path $parent -Force | Out-Null
    Set-ItemProperty -Path $parent -Name "(Default)"              -Value "AutoSplit"
    Set-ItemProperty -Path $parent -Name "MUIVerb"                -Value "AutoSplit"
    Set-ItemProperty -Path $parent -Name "SubCommands"            -Value ""
    Set-ItemProperty -Path $parent -Name "ExtendedSubCommandsKey" -Value "AutoSplitCommands"
    Set-ItemProperty -Path $parent -Name "Icon"                   -Value $InstalledExe
}

# --- Start Menu shortcut ---
Write-Host "  Creating Start Menu shortcut..." -ForegroundColor DarkGray
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutFn)
$Shortcut.TargetPath       = $InstalledExe
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.IconLocation     = $InstalledExe
$Shortcut.Description      = "AutoSplit - media splitter and joiner"
$Shortcut.Save()

# --- Uninstall registry entry (visible in Windows "Add or remove programs") ---
$uninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
New-Item -Path $uninstKey -Force | Out-Null
Set-ItemProperty -Path $uninstKey -Name "DisplayName"     -Value "AutoSplit"
Set-ItemProperty -Path $uninstKey -Name "DisplayIcon"     -Value $InstalledExe
Set-ItemProperty -Path $uninstKey -Name "DisplayVersion"  -Value "1.0.0"
Set-ItemProperty -Path $uninstKey -Name "Publisher"       -Value "gokuleshdasa"
Set-ItemProperty -Path $uninstKey -Name "InstallLocation" -Value $InstallDir
$uninstPs = Join-Path $InstallDir "Uninstall.ps1"
Set-ItemProperty -Path $uninstKey -Name "UninstallString" -Value "powershell -ExecutionPolicy Bypass -File `"$uninstPs`""

Write-Host ""
Write-Host "Installed to: $InstallDir" -ForegroundColor Green
Write-Host "Explorer menu: AutoSplit -> Split / Join (Quick) / Join (Customize)" -ForegroundColor Green
Write-Host "Start Menu shortcut: AutoSplit" -ForegroundColor Green
Write-Host ""
Write-Host "Launch it now? (Y/N) " -NoNewline -ForegroundColor Cyan
try {
    $ans = Read-Host
    if ($ans -match '^[Yy]') { Start-Process $InstalledExe }
} catch {
    # Non-interactive session - skip.
}
