#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# StillPoint macOS bootstrap + .app bundle
# Location: packaging/macos/install-mac.sh
# Entrypoint: python -m sp.app.main
# ------------------------------------------------------------

APP_NAME="StillPoint"
BUNDLE_ID="app.stillpoint.desktop"
APP_VERSION="0.1.0"
ENTRYPOINT_MODULE="sp.app.main"

INSTALL_APP=false
if [[ "${1:-}" == "--install-app" ]]; then
  INSTALL_APP=true
fi

# This script lives at: <repo>/packaging/macos/install-mac.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

REQ_FILE="$PROJECT_DIR/sp/requirements.txt"
ICON_PNG="$PROJECT_DIR/sp/assets/icon.png"

VENV_DIR="$PROJECT_DIR/.venv"
APP_DIR="$PROJECT_DIR/${APP_NAME}.app"

echo "== StillPoint macOS installer =="
echo "Project dir: $PROJECT_DIR"
echo "Install into /Applications: $INSTALL_APP"

# ------------------------------------------------------------
# Checks
# ------------------------------------------------------------
[[ -f "$REQ_FILE" ]] || { echo "ERROR: $REQ_FILE not found"; exit 1; }
[[ -f "$ICON_PNG" ]] || { echo "ERROR: $ICON_PNG not found"; exit 1; }

# ------------------------------------------------------------
# Xcode CLT (needed for some wheels)
# ------------------------------------------------------------
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Installing Xcode Command Line Tools..."
  xcode-select --install || true
  echo "⚠️  Finish installer popup, then re-run script if needed."
fi

# ------------------------------------------------------------
# Homebrew
# ------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  echo "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi

# ------------------------------------------------------------
# Python
# ------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  brew install python
fi

echo "Python: $(python3 --version)"

# ------------------------------------------------------------
# Virtualenv
# ------------------------------------------------------------
if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating venv..."
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "$REQ_FILE"

# ------------------------------------------------------------
# Build .icns
# ------------------------------------------------------------
ICONSET_DIR="$PROJECT_DIR/sp/assets/${APP_NAME}.iconset"
ICNS_OUT="$PROJECT_DIR/sp/assets/${APP_NAME}.icns"

rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"

sips -z 16 16     "$ICON_PNG" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
sips -z 32 32     "$ICON_PNG" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
sips -z 32 32     "$ICON_PNG" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
sips -z 64 64     "$ICON_PNG" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
sips -z 128 128   "$ICON_PNG" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
sips -z 256 256   "$ICON_PNG" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
sips -z 256 256   "$ICON_PNG" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
sips -z 512 512   "$ICON_PNG" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
sips -z 512 512   "$ICON_PNG" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$ICON_PNG" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null

iconutil -c icns "$ICONSET_DIR" -o "$ICNS_OUT"
rm -rf "$ICONSET_DIR"

# ------------------------------------------------------------
# Build .app bundle
# ------------------------------------------------------------
echo "Creating app bundle..."
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

cp "$ICNS_OUT" "$APP_DIR/Contents/Resources/${APP_NAME}.icns"

cat > "$APP_DIR/Contents/MacOS/$APP_NAME" <<EOF
#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$PROJECT_DIR"
source "\$PROJECT_DIR/.venv/bin/activate"
cd "\$PROJECT_DIR"
exec python -m $ENTRYPOINT_MODULE "\$@"
EOF

chmod +x "$APP_DIR/Contents/MacOS/$APP_NAME"

cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleVersion</key>
  <string>$APP_VERSION</string>
  <key>CFBundleExecutable</key>
  <string>$APP_NAME</string>
  <key>CFBundleIconFile</key>
  <string>$APP_NAME.icns</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

xattr -dr com.apple.quarantine "$APP_DIR" 2>/dev/null || true

# ------------------------------------------------------------
# Optional install into /Applications
# ------------------------------------------------------------
if [[ "$INSTALL_APP" == true ]]; then
  echo "Installing into /Applications (sudo required)..."
  sudo rm -rf "/Applications/${APP_NAME}.app"
  sudo cp -R "$APP_DIR" "/Applications/${APP_NAME}.app"
  sudo xattr -dr com.apple.quarantine "/Applications/${APP_NAME}.app" || true
  echo "✅ Installed: /Applications/${APP_NAME}.app"
fi

# ------------------------------------------------------------
# CLI helper
# ------------------------------------------------------------
RUNNER="$PROJECT_DIR/run-stillpoint.sh"
cat > "$RUNNER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "$PROJECT_DIR/.venv/bin/activate"
cd "$PROJECT_DIR"
exec python -m $ENTRYPOINT_MODULE "\$@"
EOF
chmod +x "$RUNNER"

echo ""
echo "== Done =="
echo "CLI:   $RUNNER"
echo "App:   open \"$APP_DIR\""
if [[ "$INSTALL_APP" == true ]]; then
  echo "Dock:  /Applications/${APP_NAME}.app"
fi
