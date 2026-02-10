Linux Install (Root Required)

This installer writes to system locations like `/opt`, `/usr/local/bin`,
and `/usr/share`, so it must be run with root privileges.

Install:
- `sudo ./install-linux.sh`

If you do not want to use root:
- Manually copy the files from `dist/stillpoint` to a user-owned directory.
- Add that directory to your PATH or create a user-local desktop entry.

Optional integrity check:
- Compare the SHA256 hash against the release hash (published at
  `https://github.com/grnwood/StillPoint/releases`).
  Command:
  `sha256sum <artifact>`
