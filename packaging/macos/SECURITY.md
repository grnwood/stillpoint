Unsigned App Notice

This macOS build is unsigned and not notarized, so Gatekeeper may block it or
show a warning. This is expected for unsigned open source builds.

If you trust the download, you can proceed via:
- Right-click the app -> Open -> Open
- Or System Settings -> Privacy & Security -> "Open Anyway"

If the app is still blocked after download, you can remove quarantine:
`xattr -dr com.apple.quarantine "StillPoint.app"`

Optional integrity check:
- Compute a SHA256 hash and compare it to the published release hash (posted at
  `https://github.com/grnwood/StillPoint/releases`).
  Command:
  `shasum -a 256 <artifact>`
