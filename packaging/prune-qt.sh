#!/usr/bin/env bash
set -euo pipefail
BASE="dist/StillPoint/_internal/PySide6/Qt"

rm -rf "$BASE/qml" "$BASE/translations"
rm -f  "$BASE/plugins/imageformats/libqtiff.so"

# keep xcb + wayland (comment out wayland line if you choose lean mode)
rm -f "$BASE/plugins/platforms/libqeglfs.so" \
      "$BASE/plugins/platforms/libqminimal.so" \
      "$BASE/plugins/platforms/libqminimalegl.so" \
      "$BASE/plugins/platforms/libqvnc.so" \
      "$BASE/plugins/platforms/libqoffscreen.so" \
      "$BASE/plugins/platforms/libqlinuxfb.so" \
      "$BASE/plugins/platforms/libqvkkhrdisplay.so"

rm -f "$BASE/plugins/platformthemes/libqxdgdesktopportal.so"
rm -f "$BASE/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.so"
# optional:
# rm -f "$BASE/plugins/platforminputcontexts/libibusplatforminputcontextplugin.so"
