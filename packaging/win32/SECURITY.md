Unsigned Installer Notice

This Windows installer is unsigned, so Windows may show a SmartScreen warning
("Unknown publisher"). This is expected for unsigned builds.

If you trust the download, you can proceed via:
- SmartScreen: "More info" -> "Run anyway"

Optional integrity check:
- Compute a SHA256 hash and compare it to the published release hash (posted at
  `https://github.com/grnwood/StillPoint/releases`).
  Command:
  `certutil -hashfile StillPointSetup-win-x64.exe SHA256`
