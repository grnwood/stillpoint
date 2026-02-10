Windows Install (Unsigned)

This installer is unsigned, so Windows SmartScreen may show a warning.

Install:
- Run `StillPointSetup-win-x64.exe`
- Or run the helper script: `.\install-win32.ps1`

If SmartScreen appears:
- Click "More info"
- Click "Run anyway"

Optional integrity check:
- Compare the SHA256 hash against the release hash (published at
  `https://github.com/grnwood/StillPoint/releases`).
  Command:
  `certutil -hashfile StillPointSetup-win-x64.exe SHA256`
