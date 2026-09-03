from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WIN32 = ROOT / "packaging" / "win32"


def test_inno_installer_enforces_one_clean_per_user_layout() -> None:
    script = (WIN32 / "stillpoint.iss").read_text(encoding="utf-8")

    assert "DefaultDirName={localappdata}\\Programs\\StillPoint" in script
    assert "UsePreviousAppDir=no" in script
    assert "DisableDirPage=yes" in script
    assert "PrivilegesRequired=lowest" in script
    assert "PrivilegesRequiredOverridesAllowed" not in script
    assert 'Type: filesandordirs; Name: "{app}\\stillpoint"' in script
    assert 'Type: filesandordirs; Name: "{app}\\stillpoint-capture"' in script
    assert 'Type: filesandordirs; Name: "{localappdata}\\StillPoint"' in script
    assert "LegacyInstallWasPresent" in script
    assert "User Pinned\\TaskBar\\StillPoint*.lnk" in script
    assert "older all-users StillPoint installation" in script
    assert "RegKeyExists(" in script


def test_inno_shortcuts_only_target_the_canonical_bundle() -> None:
    script = (WIN32 / "stillpoint.iss").read_text(encoding="utf-8")

    icon_lines = [line for line in script.splitlines() if line.startswith("Name: ")]
    stillpoint_icons = [line for line in icon_lines if "StillPoint" in line]
    assert stillpoint_icons
    assert all("{app}\\stillpoint" in line for line in stillpoint_icons)
    assert "{app}\\stillpoint\\stillpoint.exe" in script


def test_powershell_installer_matches_and_replaces_the_canonical_layout() -> None:
    script = (WIN32 / "install-win32.ps1").read_text(encoding="utf-8")

    assert '"Programs\\$AppName"' in script
    assert 'Join-Path $InstallDir "stillpoint"' in script
    assert 'Join-Path $ScriptRoot "..\\stillpoint-capture"' in script
    assert "Remove-InstalledPath $MainInstallDir" in script
    assert "Remove-InstalledPath $CaptureInstallDir" in script
    assert "Remove-InstalledPath $LegacyInnoDir" in script
    assert '$InstalledExe = Join-Path $MainInstallDir $ExeName' in script
    assert '$Shortcut.WorkingDirectory = $MainInstallDir' in script
    assert 'User Pinned\\TaskBar' in script
    assert "Close StillPoint and StillPoint Capture" in script
    assert "$MachineUninstallKeys" in script
    assert "older all-users StillPoint installation" in script


def test_install_cleanup_does_not_target_stillpoint_user_data() -> None:
    inno = (WIN32 / "stillpoint.iss").read_text(encoding="utf-8").lower()
    powershell = (WIN32 / "install-win32.ps1").read_text(encoding="utf-8").lower()

    assert "\\.stillpoint" not in inno
    assert "\\.stillpoint" not in powershell
    assert ".stillpoint_config.json" not in inno
    assert ".stillpoint_config.json" not in powershell
