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
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs"
)

$ExeName = "stillpoint.exe"
$CaptureExeName = "stillpoint-capture.exe"

# Base directory = folder where this script lives
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Detect build type and set paths accordingly
# Case 1: PyInstaller build - exe is in same dir as script or script is in dist/
# Case 2: Source build - exe is in dist/stillpoint/stillpoint.exe relative to script

$DistDir = $null
$ExePathInDist = $null
$AssetsDir = $null
$CaptureDistDir = $null
$CaptureExePath = $null

# Try PyInstaller structure first (script in dist/ or same dir as exe)
$PyInstallerExe = Join-Path $ScriptRoot $ExeName
if (Test-Path $PyInstallerExe) {
    Write-Host "Detected PyInstaller build"
    $DistDir = $ScriptRoot
    $ExePathInDist = $PyInstallerExe
    $AssetsDir = Join-Path $ScriptRoot "_internal\sp\assets"
    $CaptureDistDir = Join-Path $ScriptRoot "stillpoint-capture"
    if (Test-Path (Join-Path $CaptureDistDir $CaptureExeName)) {
        $CaptureExePath = Join-Path $CaptureDistDir $CaptureExeName
    }
}
# Try source build structure (dist/stillpoint/stillpoint.exe)
elseif (Test-Path (Join-Path $ScriptRoot "dist\stillpoint\$ExeName")) {
    Write-Host "Detected source build"
    $DistDir = Join-Path $ScriptRoot "dist\stillpoint"
    $ExePathInDist = Join-Path $DistDir $ExeName
    # For source builds, assets are in dist/stillpoint/_internal/sp/assets
    $AssetsDir = Join-Path $DistDir "_internal\sp\assets"
    # Fallback: try relative to script root
    if (-not (Test-Path $AssetsDir)) {
        $AssetsDir = Join-Path $ScriptRoot "sp\assets"
    }
    $CaptureDistDir = Join-Path $ScriptRoot "dist\stillpoint-capture"
    if (Test-Path (Join-Path $CaptureDistDir $CaptureExeName)) {
        $CaptureExePath = Join-Path $CaptureDistDir $CaptureExeName
    }
}
else {
    Write-Host "ERROR: Could not locate $ExeName" -ForegroundColor Red
    Write-Host "  Tried PyInstaller: $PyInstallerExe" -ForegroundColor Yellow
    Write-Host "  Tried source build: $(Join-Path $ScriptRoot "dist\stillpoint\$ExeName")" -ForegroundColor Yellow
    exit 1
}

# Install location (user space)
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$CaptureInstallDir = Join-Path $InstallDir "stillpoint-capture"

# Shortcuts
$ShortcutName = "$AppName.lnk"
$CreateDesktopShortcut = $true

Write-Host "Installing $AppName from: $DistDir"
Write-Host "Target install directory: $InstallDir"
Write-Host ""

# === RESOLVE ICON FROM assets\ ===

$IconSource = $null

$IconIco = Join-Path $AssetsDir "sp-icon.ico"
$IconPng = Join-Path $AssetsDir "sp-icon.png"

if (Test-Path $IconIco) {
    $IconSource = $IconIco
    Write-Host " Using icon: $IconSource"
}
elseif (Test-Path $IconPng) {
    $IconSource = $IconPng
    Write-Host " Using icon: $IconSource"
}
else {
    Write-Host " No assets\icon.ico or assets\icon.png found. Shortcuts will use exe icon." -ForegroundColor Yellow
}

# === CREATE INSTALL DIR ===

if (-not (Test-Path $InstallDir)) {
    Write-Host " Creating install directory: $InstallDir"
    New-Item -ItemType Directory -Path $InstallDir | Out-Null
} else {
    Write-Host " Using existing install directory: $InstallDir"
}

# === COPY FILES FROM dist\ ===

Write-Host " Copying files from $DistDir to $InstallDir"
Copy-Item -Recurse -Force (Join-Path $DistDir "*") $InstallDir

$InstalledExe = Join-Path $InstallDir $ExeName
if (-not (Test-Path $InstalledExe)) {
    Write-Host "Something went wrong: installed exe not found at $InstalledExe" -ForegroundColor Red
    exit 1
}

# === COPY ICON INTO INSTALL DIR (if present) ===

$IconDest = $InstalledExe  # default: exe icon

if ($IconSource) {
    $IconLeaf = Split-Path $IconSource -Leaf
    $IconDest = Join-Path $InstallDir $IconLeaf

    Write-Host " Copying icon to: $IconDest"
    Copy-Item -Force $IconSource $IconDest
}

# === COPY QUICK CAPTURE (if present) ===

if ($CaptureExePath) {
    Write-Host " Copying Quick Capture to: $CaptureInstallDir"
    if (-not (Test-Path $CaptureInstallDir)) {
        New-Item -ItemType Directory -Path $CaptureInstallDir | Out-Null
    }
    Copy-Item -Recurse -Force (Join-Path $CaptureDistDir "*") $CaptureInstallDir
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

Write-Host " Start Menu shortcut created: $StartMenuShortcutPath"

# Quick Capture shortcut (Start Menu only)
if ($CaptureExePath) {
    $CaptureShortcutPath = Join-Path $StartMenuDir "$AppName Quick Capture.lnk"
    $CaptureShortcut = $WshShell.CreateShortcut($CaptureShortcutPath)
    $CaptureShortcut.TargetPath = (Join-Path $CaptureInstallDir $CaptureExeName)
    $CaptureShortcut.WorkingDirectory = $CaptureInstallDir
    $CaptureShortcut.WindowStyle = 1
    $CaptureShortcut.IconLocation = $IconDest
    $CaptureShortcut.Save()
    Write-Host " Quick Capture shortcut created: $CaptureShortcutPath"
}

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

    Write-Host " Desktop shortcut created: $DesktopShortcutPath"
}

Write-Host ""
Write-Host " $AppName installed successfully!" -ForegroundColor Green
Write-Host "   - Installed to: $InstallDir"
Write-Host "   - Start Menu entry under your user profile"

if ($CreateDesktopShortcut) {
    Write-Host "   - Desktop shortcut created"
}
Write-Host ""
