from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_folder_navigator_icon_set_has_all_platform_formats() -> None:
    icons = ROOT / "sp" / "assets" / "icons"
    assert (icons / "FolderNavigator.ico").stat().st_size > 0
    assert (icons / "FolderNavigator.icns").stat().st_size > 0
    for size in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        assert (icons / "linux-png" / f"folder-navigator-{size}x{size}.png").stat().st_size > 0


def test_pyinstaller_specs_bundle_the_icon_assets() -> None:
    for spec_name in ("sp.spec", "sp-macos.spec"):
        spec = (ROOT / "packaging" / spec_name).read_text()
        assert "for subdir in ['assets', 'slipstream', 'rag', 'ai']" in spec


def test_linux_installer_registers_folder_navigator_identity() -> None:
    script = (ROOT / "packaging" / "linux-desktop" / "install-linux.sh").read_text()
    assert "stillpoint-folder-navigator.desktop" in script
    assert "folder-navigator-512x512.png" in script
    assert "--folder-navigator" in script
    assert "StartupWMClass=stillpoint-folder-navigator" in script


def test_macos_bundle_includes_companion_launcher_and_icon() -> None:
    script = (ROOT / "packaging" / "macos" / "build-macos.sh").read_text()
    assert "StillPoint Folder Navigator.app" in script
    assert "FolderNavigator.icns" in script
    assert "app.stillpoint.foldernavigator" in script
    assert 'exec "$STILLPOINT_EXECUTABLE" --folder-navigator "$@"' in script
