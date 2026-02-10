macOS Install (Unsigned)

This app is unsigned and not notarized, so Gatekeeper may block it.

Install:
- Open `StillPoint.app` from the `dist/` folder or from a ZIP

If macOS blocks it:
- Right-click `StillPoint.app` -> Open -> Open
- Or System Settings -> Privacy & Security -> "Open Anyway"

If the quarantine attribute is set:
- `xattr -dr com.apple.quarantine "StillPoint.app"`

Optional integrity check:
- Compare the SHA256 hash against the release hash (published at
  `https://github.com/grnwood/StillPoint/releases`).
  Command:
  `shasum -a 256 <artifact>`
