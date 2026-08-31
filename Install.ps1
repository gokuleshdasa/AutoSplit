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

# Refuse to run when the installer's own folder IS the install target - the
# script would delete its own source before it could copy.
$HereFull   = (Resolve-Path -LiteralPath $Here).Path.TrimEnd('\')
$TargetFull = $InstallDir.TrimEnd('\')
if ($HereFull -ieq $TargetFull) {
    Write-Host "This installer is already running from the install target." -ForegroundColor Yellow
    Write-Host "  Location: $HereFull" -ForegroundColor Yellow
    Write-Host "Run the installer from its original distribution folder, not from" -ForegroundColor Yellow
    Write-Host "the installed copy - otherwise it would delete its own source files." -ForegroundColor Yellow
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
    @{ Key = "01Split";         Verb = "Split&#8230;";              MultiSelect = "Single"; Args = '"%1"'                        },
    @{ Key = "02JoinQuick";     Verb = "Join (Quick)";              MultiSelect = "Player"; Args = '--join-quick "%1"'           },
    @{ Key = "03JoinCustom";    Verb = "Join (Customize)&#8230;";   MultiSelect = "Player"; Args = '--join-custom "%1"'          },
    @{ Key = "04ExtractFirst";  Verb = "Extract first frame";       MultiSelect = "Player"; Args = '--extract-first-frame "%1"'  },
    @{ Key = "05ExtractLast";   Verb = "Extract last frame";        MultiSelect = "Player"; Args = '--extract-last-frame "%1"'   }
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

# --- Hard link: AutoSplitJoiner.exe -> AutoSplit.exe -----------------------
# Same bytes on disk (hard link = zero-cost second name for the same file),
# but Windows' Open-with list keys off the exe name, so two names = two
# distinct "Open with" entries. main.py routes on argv[0].
$JoinerExeName = "AutoSplitJoiner.exe"
$JoinerExe     = Join-Path $InstallDir $JoinerExeName
if (-not (Test-Path -LiteralPath $JoinerExe)) {
    try {
        New-Item -ItemType HardLink -Path $JoinerExe -Target $InstalledExe -ErrorAction Stop | Out-Null
        Write-Host "  Created hard link: $JoinerExeName -> AutoSplit.exe" -ForegroundColor DarkGray
    } catch {
        # Some filesystems (FAT32, some network drives) don't support hard links;
        # fall back to a plain copy so both names still exist.
        Write-Host "  Hard link unsupported here; copying binary as $JoinerExeName" -ForegroundColor Yellow
        Copy-Item -LiteralPath $InstalledExe -Destination $JoinerExe -Force
    }
}

# --- 'Open with' registration for both apps -------------------------------
Write-Host "  Registering 'Open with' entries..." -ForegroundColor DarkGray

function Register-OpenWithApp {
    param(
        [string]$ExeName,
        [string]$ExePath,
        [string]$FriendlyName,
        [string]$ExtraArgs = "",
        [string[]]$Extensions
    )
    $appRoot = "HKCU:\Software\Classes\Applications\$ExeName"
    New-Item -Path $appRoot -Force | Out-Null
    Set-ItemProperty -Path $appRoot -Name "FriendlyAppName" -Value $FriendlyName
    Set-ItemProperty -Path $appRoot -Name "IsHostApp"       -Value ""   # marker; empty REG_SZ is fine

    $cmdKey = "$appRoot\shell\open\command"
    New-Item -Path $cmdKey -Force | Out-Null
    $cmdLine = if ($ExtraArgs) { "`"$ExePath`" $ExtraArgs `"%1`"" } else { "`"$ExePath`" `"%1`"" }
    Set-ItemProperty -Path $cmdKey -Name "(Default)" -Value $cmdLine

    # DefaultIcon for the "Open with" list.
    $iconKey = "$appRoot\DefaultIcon"
    New-Item -Path $iconKey -Force | Out-Null
    Set-ItemProperty -Path $iconKey -Name "(Default)" -Value $ExePath

    # SupportedTypes: declares which extensions the app can handle. Each
    # extension is a value name (empty data). Required for the app to appear
    # in the "Open with" list for those types.
    $stKey = "$appRoot\SupportedTypes"
    New-Item -Path $stKey -Force | Out-Null
    foreach ($ext in $Extensions) {
        Set-ItemProperty -Path $stKey -Name $ext -Value ""
    }
}

Register-OpenWithApp -ExeName "AutoSplit.exe" -ExePath $InstalledExe `
    -FriendlyName "AutoSplit" -Extensions $AllExts

Register-OpenWithApp -ExeName $JoinerExeName -ExePath $JoinerExe `
    -FriendlyName "AutoSplit Joiner" -Extensions $AllExts

# Add to HKCU\...\Classes\<ext>\OpenWithList (used by 'Choose another app').
foreach ($ext in $AllExts) {
    $listRoot = "HKCU:\Software\Classes\$ext\OpenWithList"
    New-Item -Path $listRoot -Force | Out-Null
    foreach ($n in @("AutoSplit.exe", $JoinerExeName)) {
        $entry = "$listRoot\$n"
        if (-not (Test-Path -LiteralPath $entry)) {
            New-Item -Path $entry -Force | Out-Null
        }
    }
}

# The TOP-LEVEL 'Open with' flyout in Windows 11 reads Explorer's per-user
# FileExts store instead. Values here are single-letter names ('a','b','c'...)
# each pointing at an exe basename; MRUList orders them. Prepend our two apps
# so they appear at the very top of the picker.
$Letters = "abcdefghijklmnopqrstuvwxyz".ToCharArray()

function Ensure-FileExtsOpenWith {
    param([string]$Ext, [string[]]$ExeNames)

    $picker = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$Ext\OpenWithList"
    New-Item -Path $picker -Force | Out-Null

    # Existing MRU (a string of letters, e.g. "abc").
    $currentMru = ""
    try {
        $prop = Get-ItemProperty -Path $picker -Name "MRUList" -ErrorAction Stop
        $currentMru = $prop.MRUList
    } catch { $currentMru = "" }

    # Find any letters already mapped to our apps so we don't duplicate them.
    $existingByExe = @{}
    foreach ($ch in $currentMru.ToCharArray()) {
        try {
            $val = (Get-ItemProperty -Path $picker -Name $ch -ErrorAction Stop)."$ch"
            if ($val) { $existingByExe[$val] = $ch }
        } catch {}
    }

    # Letters already taken by SOMETHING (regardless of exe).
    $used = @{}
    foreach ($ch in $currentMru.ToCharArray()) { $used[$ch] = $true }

    $ourLetters = @()
    foreach ($exe in $ExeNames) {
        if ($existingByExe.ContainsKey($exe)) {
            $ourLetters += $existingByExe[$exe]
            continue
        }
        # Pick the first unused letter.
        $chosen = $null
        foreach ($ch in $Letters) {
            if (-not $used.ContainsKey($ch)) { $chosen = $ch; break }
        }
        if ($chosen) {
            Set-ItemProperty -Path $picker -Name "$chosen" -Value $exe -Type String
            $used[$chosen] = $true
            $ourLetters += $chosen
        }
    }

    # Rebuild MRUList: OUR letters first (so we appear at the top), then any
    # letters already present that weren't ours.
    $othersMru = ($currentMru.ToCharArray() | Where-Object { $_ -notin $ourLetters }) -join ""
    $newMru = (($ourLetters -join "") + $othersMru)
    Set-ItemProperty -Path $picker -Name "MRUList" -Value $newMru -Type String
}

foreach ($ext in $AllExts) {
    Ensure-FileExtsOpenWith -Ext $ext -ExeNames @("AutoSplit.exe", $JoinerExeName)
}

# Notify the shell that file-association data has changed so Explorer picks
# up the new 'Open with' entries without needing a sign-out / reboot.
Add-Type -Namespace Shell32 -Name Notify -MemberDefinition @'
    [System.Runtime.InteropServices.DllImport("shell32.dll", CharSet=System.Runtime.InteropServices.CharSet.Auto, SetLastError=true)]
    public static extern void SHChangeNotify(int wEventId, uint uFlags, System.IntPtr dwItem1, System.IntPtr dwItem2);
'@ -ErrorAction SilentlyContinue
try { [Shell32.Notify]::SHChangeNotify(0x08000000, 0x0000, [System.IntPtr]::Zero, [System.IntPtr]::Zero) } catch {}

# --- Start Menu shortcuts (Splitter and Joiner as independent entries) ---
Write-Host "  Creating Start Menu shortcuts..." -ForegroundColor DarkGray
$WshShell = New-Object -ComObject WScript.Shell

# Splitter (default AutoSplit launch)
$Shortcut = $WshShell.CreateShortcut($ShortcutFn)
$Shortcut.TargetPath       = $InstalledExe
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.IconLocation     = $InstalledExe
$Shortcut.Description      = "AutoSplit Splitter - cut audio and video files"
$Shortcut.Save()

# Joiner (same binary, --join-custom flag)
$JoinerShortcutFn = Join-Path $StartMenu "AutoSplit Joiner.lnk"
$Shortcut = $WshShell.CreateShortcut($JoinerShortcutFn)
$Shortcut.TargetPath       = $InstalledExe
$Shortcut.Arguments        = "--join-custom"
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.IconLocation     = $InstalledExe
$Shortcut.Description      = "AutoSplit Joiner - combine multiple audio or video files"
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
Write-Host "Explorer menu: AutoSplit -> Split / Join Quick / Join Customize / Extract first frame / Extract last frame" -ForegroundColor Green
Write-Host "Start Menu shortcut: AutoSplit" -ForegroundColor Green
Write-Host ""
Write-Host "Launch it now? (Y/N) " -NoNewline -ForegroundColor Cyan
try {
    $ans = Read-Host
    if ($ans -match '^[Yy]') { Start-Process $InstalledExe }
} catch {
    # Non-interactive session - skip.
}
