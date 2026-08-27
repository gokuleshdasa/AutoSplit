# AutoSplit uninstaller - reverses everything Install.ps1 did.
# Per-user; no administrator rights required.

$ErrorActionPreference = "Continue"

$AppName    = "AutoSplit"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$StartMenu  = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$ShortcutFn = Join-Path $StartMenu "$AppName.lnk"

Write-Host "Uninstalling $AppName..." -ForegroundColor Cyan

# 1. Stop any running instance.
Get-Process $AppName -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  Stopping $AppName (PID $($_.Id))" -ForegroundColor Yellow
    $_ | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 400

# 2. Remove Explorer context menu.
Write-Host "  Removing Explorer context menu..." -ForegroundColor DarkGray
$VIDEO_EXTS = @(".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")
$AUDIO_EXTS = @(".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus")
foreach ($ext in ($VIDEO_EXTS + $AUDIO_EXTS)) {
    $paths = @(
        "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell\AutoSplit",
        "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell\AutoSplitMedia"
    )
    foreach ($p in $paths) {
        if (Test-Path -LiteralPath $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
    }
}
if (Test-Path -LiteralPath "HKCU:\Software\Classes\AutoSplitCommands") {
    Remove-Item -Recurse -Force -LiteralPath "HKCU:\Software\Classes\AutoSplitCommands" -ErrorAction SilentlyContinue
}

# 3. Remove Start Menu shortcuts (both Splitter and Joiner entries).
foreach ($lnk in @($ShortcutFn, (Join-Path $StartMenu "AutoSplit Joiner.lnk"))) {
    if (Test-Path -LiteralPath $lnk) {
        Remove-Item -Force -LiteralPath $lnk -ErrorAction SilentlyContinue
    }
}

# 4. Remove uninstall registry entry.
$uninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
if (Test-Path -LiteralPath $uninstKey) {
    Remove-Item -Recurse -Force -LiteralPath $uninstKey -ErrorAction SilentlyContinue
}

# 5. Remove installed files.
if (Test-Path -LiteralPath $InstallDir) {
    Write-Host "  Removing $InstallDir" -ForegroundColor DarkGray
    # Delay to let the process handle release.
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force -LiteralPath $InstallDir -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "$AppName uninstalled." -ForegroundColor Green
Write-Host "Note: user settings in the registry (HKCU\Software\AutoSplitMedia) are kept" -ForegroundColor DarkGray
Write-Host "so a reinstall preserves your preferences. Delete that key manually to wipe them." -ForegroundColor DarkGray
