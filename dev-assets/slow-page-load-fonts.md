# Slow Page Load: Font Scan Notes

Summary of diagnosing the 6–8s UI stall and the workaround we added.

## What we saw
- Page load logs showed everything in Python finished in ~30ms, but the Qt event loop stalled for ~7s.
- `strace` captured Qt walking hundreds of system fonts (Noto/URW), with repeated open/mmap/munmap calls.
- You have ~568 system fonts; the stall was the font database scan during Qt startup.

## Workaround added
- New opt-in env flag `ZIMX_MINIMAL_FONT_SCAN=1` (in `sp/app/main.py`).
- When enabled, at startup we:
  - Create a tiny fontconfig dir under `~/.cache/stillpoint/fonts-minimal/`.
  - Copy a single common font into it (Linux: DejaVu/Liberation/Noto; Windows: Segoe UI/Arial/Tahoma).
  - Write a minimal `fonts.conf` pointing only to that dir.
  - Set `FONTCONFIG_FILE`, `FONTCONFIG_PATH`, and `QT_QPA_FONTDIR` so Qt sees only that small set.
- This prevents Qt from scanning all system fonts and eliminates the startup stall.

## How to use
- Run with the flag set:
  - Linux/macOS: `ZIMX_MINIMAL_FONT_SCAN=1 python -m sp.app.main`
  - Windows (PowerShell): `set ZIMX_MINIMAL_FONT_SCAN=1; python -m sp.app.main`
- Keep `ZIMX_DETAILED_PAGE_LOGGING=1` if you want the timing logs.

## Other tips
- Rebuilding fontconfig caches (`fc-cache -r`) can help if caches are stale, but the minimal font mode is the reliable fix.
- If you want to use a different font, drop a TTF/OTF into `~/.cache/stillpoint/fonts-minimal/fonts` and point the helper there, or adjust the code to prefer your font.
