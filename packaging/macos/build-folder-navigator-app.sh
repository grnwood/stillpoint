#!/usr/bin/env bash
# Create the macOS companion launcher for Folder Navigator.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${1:-$SCRIPT_DIR/dist}"
VERSION="${2:-${STILLPOINT_VERSION:-local}}"
PLUTIL_BIN="${PLUTIL_BIN:-plutil}"
FOLDER_NAV_APP="$OUTPUT_DIR/StillPoint Folder Navigator.app"
FOLDER_NAV_ICON="$SCRIPT_DIR/../../sp/assets/icons/FolderNavigator.icns"
FOLDER_NAV_EXECUTABLE="$FOLDER_NAV_APP/Contents/MacOS/stillpoint-folder-navigator"

if [[ ! -f "$FOLDER_NAV_ICON" ]]; then
  echo "ERROR: Missing Folder Navigator icon: $FOLDER_NAV_ICON" >&2
  exit 1
fi

rm -rf "$FOLDER_NAV_APP"
mkdir -p "$FOLDER_NAV_APP/Contents/MacOS" "$FOLDER_NAV_APP/Contents/Resources"
cp "$FOLDER_NAV_ICON" "$FOLDER_NAV_APP/Contents/Resources/FolderNavigator.icns"

cat > "$FOLDER_NAV_EXECUTABLE" <<'EOF'
#!/bin/sh
set -eu
APPLICATIONS_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
STILLPOINT_EXECUTABLE="$APPLICATIONS_DIR/StillPoint.app/Contents/MacOS/StillPoint"
if [ ! -x "$STILLPOINT_EXECUTABLE" ]; then
  echo "StillPoint.app must be installed beside StillPoint Folder Navigator.app." >&2
  exit 1
fi
exec "$STILLPOINT_EXECUTABLE" --folder-navigator "$@"
EOF
chmod +x "$FOLDER_NAV_EXECUTABLE"

cat > "$FOLDER_NAV_APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleDisplayName</key><string>StillPoint Folder Navigator</string>
  <key>CFBundleExecutable</key><string>stillpoint-folder-navigator</string>
  <key>CFBundleIconFile</key><string>FolderNavigator</string>
  <key>CFBundleIdentifier</key><string>app.stillpoint.foldernavigator</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>StillPoint Folder Navigator</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>10.13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

"$PLUTIL_BIN" -lint "$FOLDER_NAV_APP/Contents/Info.plist"
test -d "$FOLDER_NAV_APP"
test -x "$FOLDER_NAV_EXECUTABLE"

echo "Created: $FOLDER_NAV_APP"
