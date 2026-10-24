#!/usr/bin/env bash
# Create a self-contained macOS app bundle for StillPoint Quick Capture.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${1:-$SCRIPT_DIR/dist}"
VERSION="${2:-${STILLPOINT_VERSION:-local}}"
PLUTIL_BIN="${PLUTIL_BIN:-plutil}"
CAPTURE_OUTPUT="$OUTPUT_DIR/stillpoint-capture"
CAPTURE_BINARY="$CAPTURE_OUTPUT/stillpoint-capture"
QUICK_CAPTURE_APP="$OUTPUT_DIR/StillPoint Quick Capture.app"
QUICK_CAPTURE_ICON="$SCRIPT_DIR/../../sp/assets/icons/StillPoint.icns"
QUICK_CAPTURE_EXECUTABLE="$QUICK_CAPTURE_APP/Contents/MacOS/stillpoint-quick-capture"
EMBEDDED_CAPTURE_DIR="$QUICK_CAPTURE_APP/Contents/Resources/stillpoint-capture"

if [[ ! -x "$CAPTURE_BINARY" ]]; then
  echo "ERROR: Missing Quick Capture build: $CAPTURE_BINARY" >&2
  exit 1
fi
if [[ ! -f "$QUICK_CAPTURE_ICON" ]]; then
  echo "ERROR: Missing Quick Capture icon: $QUICK_CAPTURE_ICON" >&2
  exit 1
fi

rm -rf "$QUICK_CAPTURE_APP"
mkdir -p "$QUICK_CAPTURE_APP/Contents/MacOS" "$QUICK_CAPTURE_APP/Contents/Resources"
cp -R "$CAPTURE_OUTPUT" "$QUICK_CAPTURE_APP/Contents/Resources/"
cp "$QUICK_CAPTURE_ICON" "$QUICK_CAPTURE_APP/Contents/Resources/StillPoint.icns"

cat > "$QUICK_CAPTURE_EXECUTABLE" <<'EOF'
#!/bin/sh
set -eu
CONTENTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CAPTURE_EXECUTABLE="$CONTENTS_DIR/Resources/stillpoint-capture/stillpoint-capture"
if [ ! -x "$CAPTURE_EXECUTABLE" ]; then
  echo "StillPoint Quick Capture is incomplete; its embedded executable is missing." >&2
  exit 1
fi
exec "$CAPTURE_EXECUTABLE" "$@"
EOF
chmod +x "$QUICK_CAPTURE_EXECUTABLE"

cat > "$QUICK_CAPTURE_APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleDisplayName</key><string>StillPoint Quick Capture</string>
  <key>CFBundleExecutable</key><string>stillpoint-quick-capture</string>
  <key>CFBundleIconFile</key><string>StillPoint</string>
  <key>CFBundleIdentifier</key><string>app.stillpoint.quickcapture</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>StillPoint Quick Capture</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>10.13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

"$PLUTIL_BIN" -lint "$QUICK_CAPTURE_APP/Contents/Info.plist"
test -d "$QUICK_CAPTURE_APP"
test -x "$QUICK_CAPTURE_EXECUTABLE"
test -x "$EMBEDDED_CAPTURE_DIR/stillpoint-capture"

echo "Created: $QUICK_CAPTURE_APP"
