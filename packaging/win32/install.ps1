# ---------------------------------------------
#  StillPoint Win32 User-Space Installer (PowerShell)
# ---------------------------------------------
#  - No admin needed
#  - Installs to:  $env:LOCALAPPDATA\Programs\StillPoint
#  - Icons loaded from: assets\icon.ico or assets\icon.png
# ---------------------------------------------
#
# Run With
#   powershell -ExecutionPolicy Bypass -File .\install.ps1

param(
    [string]$AppName = "StillPoint",
    [string]$ExeName = "StillPoint.exe"
)

# Base directory = folder where this script lives
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Paths relative to script
$DistDir    = Join-Path $ScriptRoot "..\..\dist\StillPoint"
$AssetsDir  = Join-Path $ScriptRoot "..\..\assets"

# Install location (user space)
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppName"

# Shortcuts
$ShortcutName = "$AppName.lnk"
$CreateDesktopShortcut = $true

Write-Host "Installing $AppName from: $DistDir"
Write-Host "Target install directory: $InstallDir"
Write-Host ""

# === VALIDATE dist\ AND EXE ===

if (-not (Test-Path $DistDir)) {
    Write-Host "❌ dist\ folder not found at: $DistDir" -ForegroundColor Red
    exit 1
}

$ExePathInDist = Join-Path $DistDir $ExeName
if (-not (Test-Path $ExePathInDist)) {
    Write-Host "❌ Executable not found: $ExePathInDist" -ForegroundColor Red
    Write-Host "   Make sure `\$ExeName` matches your built .exe" -ForegroundColor Yellow
    exit 1
}

# === RESOLVE ICON FROM assets\ ===

$IconSource = $null

$IconIco = Join-Path $AssetsDir "icon.ico"
$IconPng = Join-Path $AssetsDir "icon.png"

if (Test-Path $IconIco) {
    $IconSource = $IconIco
    Write-Host "✔️  Using icon: $IconSource"
}
elseif (Test-Path $IconPng) {
    $IconSource = $IconPng
    Write-Host "✔️  Using icon: $IconSource"
}
else {
    Write-Host "ℹ️  No assets\icon.ico or assets\icon.png found. Shortcuts will use exe icon." -ForegroundColor Yellow
}

# === CREATE INSTALL DIR ===

if (-not (Test-Path $InstallDir)) {
    Write-Host "➡️  Creating install directory: $InstallDir"
    New-Item -ItemType Directory -Path $InstallDir | Out-Null
}
else {
    Write-Host "ℹ️  Using existing install directory: $InstallDir"
}

# === COPY FILES FROM dist\ ===

Write-Host "➡️  Copying files from $DistDir to $InstallDir"
Copy-Item -Recurse -Force (Join-Path $DistDir "*") $InstallDir

$InstalledExe = Join-Path $InstallDir $ExeName
if (-not (Test-Path $InstalledExe)) {
    Write-Host "❌ Something went wrong: installed exe not found at $InstalledExe" -ForegroundColor Red
    exit 1
}

# === COPY ICON INTO INSTALL DIR (if present) ===

$IconDest = $InstalledExe  # default: exe icon

if ($IconSource) {
    $IconLeaf = Split-Path $IconSource -Leaf
    $IconDest = Join-Path $InstallDir $IconLeaf

    Write-Host "➡️  Copying icon to: $IconDest"
    Copy-Item -Force $IconSource $IconDest
}

# === CREATE START MENU SHORTCUT (USER ONLY) ===

$StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
if (-not (Test-Path $StartMenuDir)) {
    New-Item -ItemType Directory -Path $StartMenuDir | Out-Null
}

$StartMenuShortcutPath = Join-Path $StartMenuDir $ShortcutName

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($StartMenuShortcutPath)
$Shortcut.TargetPath = $InstalledExe
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.WindowStyle = 1
$Shortcut.IconLocation = $IconDest
$Shortcut.Save()

Write-Host "✔️  Start Menu shortcut created: $StartMenuShortcutPath"

# === OPTIONAL DESKTOP SHORTCUT (USER ONLY) ===

if ($CreateDesktopShortcut) {
    $DesktopDir = [Environment]::GetFolderPath("Desktop")
    $DesktopShortcutPath = Join-Path $DesktopDir $ShortcutName

    $DesktopShortcut = $WshShell.CreateShortcut($DesktopShortcutPath)
    $DesktopShortcut.TargetPath = $InstalledExe
    $DesktopShortcut.WorkingDirectory = $InstallDir
    $DesktopShortcut.WindowStyle = 1
    $DesktopShortcut.IconLocation = $IconDest
    $DesktopShortcut.Save()

    Write-Host "✔️  Desktop shortcut created: $DesktopShortcutPath"


Write-Host ""
Write-Host "🎉 $AppName installed successfully!" -ForegroundColor Green
Write-Host "   - Installed to: $InstallDir"
Write-Host "   - Start Menu entry under your user profile"
if ($CreateDesktopShortcut) {
    Write-Host "   - Desktop shortcut created"
}
Write-Host ""
