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
# Case 2: Source build - exe is in ../../dist/stillpoint/stillpoint.exe relative to script

$DistDir = $null
$ExePathInDist = $null
$AssetsDir = $null
$CaptureDistDir = $null
$CaptureExePath = $null
$CaptureDistExists = $false

# Try PyInstaller structure first (script in dist/ or same dir as exe)
$PyInstallerExe = Join-Path $ScriptRoot $ExeName
if (Test-Path $PyInstallerExe) {
    Write-Host "Detected PyInstaller build"
    $DistDir = $ScriptRoot
    $ExePathInDist = $PyInstallerExe
    $AssetsDir = Join-Path $ScriptRoot "_internal\sp\assets"
    $CaptureDistDir = Join-Path $ScriptRoot "..\stillpoint-capture"
    $CaptureDistExists = Test-Path $CaptureDistDir
    if ($CaptureDistExists -and (Test-Path (Join-Path $CaptureDistDir $CaptureExeName))) {
        $CaptureExePath = Join-Path $CaptureDistDir $CaptureExeName
    }
}
# Try source build structure (dist/stillpoint/stillpoint.exe)
elseif (Test-Path (Join-Path $ScriptRoot "..\..\dist\stillpoint\$ExeName")) {
    Write-Host "Detected source build"
    $DistDir = Join-Path $ScriptRoot "..\..\dist\stillpoint"
    $ExePathInDist = Join-Path $DistDir $ExeName
    # For source builds, assets are in dist/stillpoint/_internal/sp/assets
    $AssetsDir = Join-Path $DistDir "_internal\sp\assets"
    # Fallback: try relative to script root
    if (-not (Test-Path $AssetsDir)) {
        $AssetsDir = Join-Path $ScriptRoot "..\..\sp\assets"
    }
    $CaptureDistDir = Join-Path $ScriptRoot "..\..\dist\stillpoint-capture"
    $CaptureDistExists = Test-Path $CaptureDistDir
    if ($CaptureDistExists -and (Test-Path (Join-Path $CaptureDistDir $CaptureExeName))) {
        $CaptureExePath = Join-Path $CaptureDistDir $CaptureExeName
    }
}
else {
    Write-Host "ERROR: Could not locate $ExeName" -ForegroundColor Red
    Write-Host "  Tried PyInstaller: $PyInstallerExe" -ForegroundColor Yellow
    Write-Host "  Tried source build: $(Join-Path $ScriptRoot "..\..\dist\stillpoint\$ExeName")" -ForegroundColor Yellow
    exit 1
}

# One canonical per-user layout shared with the Inno installer.
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$MainInstallDir = Join-Path $InstallDir "stillpoint"
$CaptureInstallDir = Join-Path $InstallDir "stillpoint-capture"
$LegacyInnoDir = Join-Path $env:LOCALAPPDATA $AppName
$LegacyFlatExe = Join-Path $InstallDir $ExeName
$HadLegacyInstall = (Test-Path $LegacyInnoDir) -or (Test-Path $LegacyFlatExe)

# A previous installer could be switched into all-users mode. Do not create a
# second per-user copy beside that elevated installation.
$MachineUninstallKeys = @(
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{E2D0C38B-BA4E-4C9D-9D75-2E6E7F9B6C7E}_is1",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{E2D0C38B-BA4E-4C9D-9D75-2E6E7F9B6C7E}_is1"
)
if ($MachineUninstallKeys | Where-Object { Test-Path $_ }) {
    Write-Host "ERROR: An older all-users StillPoint installation is still registered." -ForegroundColor Red
    Write-Host "Open Windows Settings > Apps > Installed apps, uninstall StillPoint, then run this installer again." -ForegroundColor Yellow
    Write-Host "Your vaults and preferences will not be removed." -ForegroundColor Yellow
    exit 1
}

# Updating from a script inside an installed tree would delete its own source
# bundle before it could copy it. Require the newly downloaded build instead.
$SourceFullPath = [IO.Path]::GetFullPath($DistDir).TrimEnd('\')
$InstallFullPath = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$LegacyFullPath = [IO.Path]::GetFullPath($LegacyInnoDir).TrimEnd('\')
function Test-PathAtOrBelow {
    param([string]$Candidate, [string]$Root)
    return $Candidate.Equals($Root, [StringComparison]::OrdinalIgnoreCase) -or
        $Candidate.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)
}
if ((Test-PathAtOrBelow $SourceFullPath $InstallFullPath) -or
    (Test-PathAtOrBelow $SourceFullPath $LegacyFullPath)) {
    Write-Host "ERROR: Run install-win32.ps1 from the newly downloaded build, not the installed copy." -ForegroundColor Red
    exit 1
}

# Shortcuts
$ShortcutName = "$AppName.lnk"
$CreateDesktopShortcut = $true

Write-Host "Installing $AppName from: $DistDir"
Write-Host "Target install directory: $InstallDir"
Write-Host ""

# === RESOLVE ICON FROM assets\ ===

$IconSource = $null

$IconIco = Join-Path $AssetsDir "icons\\StillPoint.ico"
$IconPng = Join-Path $AssetsDir "icons\\linux-png\\stillpoint-512x512.png"

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

# === REMOVE OLD APPLICATION BINARIES ===

function Remove-InstalledPath {
    param([string]$Path)
    if (Test-Path $Path) {
        Write-Host " Removing old application path: $Path"
        Remove-Item -Recurse -Force $Path -ErrorAction Stop
    }
}

try {
    # Fully replace PyInstaller trees so removed modules and DLLs cannot survive
    # an upgrade and form a mixed-version application.
    Remove-InstalledPath $MainInstallDir
    Remove-InstalledPath $CaptureInstallDir

    # Remove the old Inno location and the old flat PowerShell layout. User
    # settings and vaults are stored elsewhere and are not touched here.
    Remove-InstalledPath $LegacyInnoDir
    Remove-InstalledPath (Join-Path $InstallDir "_internal")
    foreach ($LegacyFile in @(
        $LegacyFlatExe,
        (Join-Path $InstallDir "install-win32.ps1"),
        (Join-Path $InstallDir "README.txt"),
        (Join-Path $InstallDir "LICENSE"),
        (Join-Path $InstallDir "NOTICE"),
        (Join-Path $InstallDir "StillPoint.ico")
    )) {
        if (Test-Path $LegacyFile) {
            Write-Host " Removing old application file: $LegacyFile"
            Remove-Item -Force $LegacyFile -ErrorAction Stop
        }
    }
}
catch {
    Write-Host "ERROR: Could not remove the previous StillPoint build." -ForegroundColor Red
    Write-Host "Close StillPoint and StillPoint Capture, then run this installer again." -ForegroundColor Yellow
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    exit 1
}

# If the helper migrated the historical Inno directory, remove only its stale
# per-user uninstall registration. The helper itself remains a portable-style
# install, as documented; a later Inno install will create a fresh entry.
$LegacyUninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{E2D0C38B-BA4E-4C9D-9D75-2E6E7F9B6C7E}_is1"
if ($HadLegacyInstall -and (Test-Path $LegacyUninstallKey)) {
    $RegisteredLocation = (Get-ItemProperty $LegacyUninstallKey -ErrorAction SilentlyContinue).InstallLocation
    if ($RegisteredLocation) {
        $RegisteredFullPath = [IO.Path]::GetFullPath($RegisteredLocation).TrimEnd('\')
        if ($RegisteredFullPath.Equals($LegacyFullPath, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -Recurse -Force $LegacyUninstallKey -ErrorAction SilentlyContinue
        }
    }
}

# === CREATE INSTALL DIR ===

if (-not (Test-Path $InstallDir)) {
    Write-Host " Creating install directory: $InstallDir"
    New-Item -ItemType Directory -Path $InstallDir | Out-Null
} else {
    Write-Host " Using existing install directory: $InstallDir"
}
New-Item -ItemType Directory -Force -Path $MainInstallDir | Out-Null

# === COPY FILES FROM dist\ ===

Write-Host " Copying files from $DistDir to $MainInstallDir"
Copy-Item -Recurse -Force (Join-Path $DistDir "*") $MainInstallDir

$InstalledExe = Join-Path $MainInstallDir $ExeName
if (-not (Test-Path $InstalledExe)) {
    Write-Host "Something went wrong: installed exe not found at $InstalledExe" -ForegroundColor Red
    exit 1
}
$InstalledCaptureExe = Join-Path $CaptureInstallDir $CaptureExeName

# === COPY ICON INTO INSTALL DIR (if present) ===

$IconDest = $InstalledExe  # default: exe icon

if ($IconSource) {
    $IconLeaf = Split-Path $IconSource -Leaf
    $IconDest = Join-Path $MainInstallDir $IconLeaf

    Write-Host " Copying icon to: $IconDest"
    Copy-Item -Force $IconSource $IconDest
}

# === COPY QUICK CAPTURE (if present) ===

if ($CaptureDistExists) {
    Write-Host " Copying Quick Capture to: $CaptureInstallDir"
    if (-not (Test-Path $CaptureInstallDir)) {
        New-Item -ItemType Directory -Path $CaptureInstallDir | Out-Null
    }
    Copy-Item -Recurse -Force (Join-Path $CaptureDistDir "*") $CaptureInstallDir
} else {
    Write-Host " Quick Capture dist folder not found at $CaptureDistDir" -ForegroundColor Yellow
}

# === CREATE START MENU SHORTCUT (USER ONLY) ===

$StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
if (-not (Test-Path $StartMenuDir)) {
    New-Item -ItemType Directory -Path $StartMenuDir | Out-Null
}

$StartMenuShortcutPath = Join-Path $StartMenuDir $ShortcutName

# Remove shortcuts created by either historical layout before writing the
# canonical targets. A legacy pin embeds the old executable path, so remove it
# once during migration and let the user pin the new Start Menu entry.
foreach ($OldShortcut in @(
    $StartMenuShortcutPath,
    (Join-Path $StartMenuDir "$AppName Quick Capture.lnk")
)) {
    Remove-Item -Force $OldShortcut -ErrorAction SilentlyContinue
}
Remove-Item -Recurse -Force (Join-Path $StartMenuDir $AppName) -ErrorAction SilentlyContinue
if ($HadLegacyInstall) {
    foreach ($PinnedDir in @(
        (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"),
        (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\StartMenu")
    )) {
        Get-ChildItem -Path $PinnedDir -Filter "$AppName*.lnk" -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($StartMenuShortcutPath)
$Shortcut.TargetPath = $InstalledExe
$Shortcut.WorkingDirectory = $MainInstallDir
$Shortcut.WindowStyle = 1
$Shortcut.IconLocation = $IconDest
$Shortcut.Save()

Write-Host " Start Menu shortcut created: $StartMenuShortcutPath"

# Quick Capture shortcut (Start Menu only)
if (Test-Path $InstalledCaptureExe) {
    $CaptureShortcutPath = Join-Path $StartMenuDir "$AppName Quick Capture.lnk"
    $CaptureShortcut = $WshShell.CreateShortcut($CaptureShortcutPath)
    $CaptureShortcut.TargetPath = $InstalledCaptureExe
    $CaptureShortcut.WorkingDirectory = $CaptureInstallDir
    $CaptureShortcut.WindowStyle = 1
    $CaptureShortcut.IconLocation = $IconDest
    $CaptureShortcut.Save()
    Write-Host " Quick Capture shortcut created: $CaptureShortcutPath"
} else {
    Write-Host " Quick Capture exe not found at $InstalledCaptureExe" -ForegroundColor Yellow
}

# === OPTIONAL DESKTOP SHORTCUT (USER ONLY) ===

if ($CreateDesktopShortcut) {
    $DesktopDir = [Environment]::GetFolderPath("Desktop")
    $DesktopShortcutPath = Join-Path $DesktopDir $ShortcutName
    Remove-Item -Force $DesktopShortcutPath -ErrorAction SilentlyContinue
    Remove-Item -Force (Join-Path $DesktopDir "$AppName Quick Capture.lnk") -ErrorAction SilentlyContinue

    $DesktopShortcut = $WshShell.CreateShortcut($DesktopShortcutPath)
    $DesktopShortcut.TargetPath = $InstalledExe
    $DesktopShortcut.WorkingDirectory = $MainInstallDir
    $DesktopShortcut.WindowStyle = 1
    $DesktopShortcut.IconLocation = $IconDest
    $DesktopShortcut.Save()

    Write-Host " Desktop shortcut created: $DesktopShortcutPath"

    if (Test-Path $InstalledCaptureExe) {
        $QuickCaptureShortcutName = "$AppName Quick Capture.lnk"
        $QuickCaptureShortcutPath = Join-Path $DesktopDir $QuickCaptureShortcutName

        $QuickCaptureShortcut = $WshShell.CreateShortcut($QuickCaptureShortcutPath)
        $QuickCaptureShortcut.TargetPath = $InstalledCaptureExe
        $QuickCaptureShortcut.WorkingDirectory = $CaptureInstallDir
        $QuickCaptureShortcut.WindowStyle = 1
        $QuickCaptureShortcut.IconLocation = $IconDest
        $QuickCaptureShortcut.Save()

        Write-Host " Desktop quick capture shortcut created: $QuickCaptureShortcutPath"
    } else {
        Write-Host " Quick capture exe not found at $InstalledCaptureExe" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host " $AppName installed successfully!" -ForegroundColor Green
Write-Host "   - Installed to: $InstallDir"
Write-Host "   - Start Menu entry under your user profile"

if ($CreateDesktopShortcut) {
    Write-Host "   - Desktop shortcut created"
}
Write-Host ""
