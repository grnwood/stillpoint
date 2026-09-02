Windows Install (Unsigned)

This installer is unsigned, so Windows SmartScreen may show a warning.

Install:
- Run `StillPointSetup-win-x64.exe`
- Or run the helper script: `.\install-win32.ps1`

Both installers use the same per-user location:

`%LOCALAPPDATA%\Programs\StillPoint`

Upgrades replace the complete application bundle instead of copying over an
older bundle. The first unified-layout upgrade also removes the historical
`%LOCALAPPDATA%\StillPoint` install and stale StillPoint shortcuts. If a
StillPoint taskbar pin pointed at that old location, pin the newly installed
Start Menu entry again. Vaults, preferences, templates, and other user data are
not stored in these application directories and are not removed.

Very old installers could be switched to an all-users installation. If that
copy is still registered, the new per-user installer will ask you to uninstall
it from **Windows Settings > Apps > Installed apps** before continuing. This
prevents Windows from retaining two searchable StillPoint applications.

If SmartScreen appears:
- Click "More info"
- Click "Run anyway"

Optional integrity check:
- Compare the SHA256 hash against the release hash (published at
  `https://github.com/grnwood/StillPoint/releases`).
  Command:
  `certutil -hashfile StillPointSetup-win-x64.exe SHA256`
