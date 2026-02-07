#!/usr/bin/env bash
# Linux installer for StillPoint (bundled with distribution)
# Run with: sudo ./install.sh

set -e

# --- Require sudo ---
if [[ $EUID -ne 0 ]]; then
    echo "❌ This installer must be run with sudo."
    echo "   Try: sudo ./install.sh"
    exit 1
fi

APP_NAME="StillPoint"
EXEC_NAME="stillpoint"
CAPTURE_NAME="stillpoint-capture"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/stillpoint"
BIN_LINK="/usr/local/bin/stillpoint"
CAPTURE_INSTALL_DIR="/opt/stillpoint-capture"
CAPTURE_LINK="/usr/local/bin/stillpoint-capture"
ICON_TARGET="/usr/share/icons/stillpoint.png"
DESKTOP_FILE="/usr/share/applications/stillpoint.desktop"

echo "📦 Installing $APP_NAME..."

# --- Find dist directory (bundled or build from source) ---
# First try: script is bundled in dist folder alongside executable
DIST_DIR="$SCRIPT_DIR"
if [[ -f "$DIST_DIR/$EXEC_NAME" ]]; then
    echo "✔️  Found executable in bundled distribution"
else
    # Second try: running from source, check ../../dist/stillpoint
    DIST_DIR="$SCRIPT_DIR/../../dist/stillpoint"
    if [[ -f "$DIST_DIR/$EXEC_NAME" ]]; then
        echo "✔️  Found executable in build directory (source)"
        DIST_DIR="$(cd "$DIST_DIR" && pwd)"  # Resolve to absolute path
    else
        echo "❌ Executable not found in:"
        echo "   • $SCRIPT_DIR/$EXEC_NAME (bundled)"
        echo "   • $SCRIPT_DIR/../../dist/stillpoint/$EXEC_NAME (build)"
        exit 1
    fi
fi

ICON_SOURCE="$DIST_DIR/_internal/sp/assets/icons/linux-png/stillpoint-512x512.png"
CAPTURE_DIST_DIR="$SCRIPT_DIR/../stillpoint-capture"
if [[ -d "$SCRIPT_DIR/$CAPTURE_NAME" ]]; then
    CAPTURE_DIST_DIR="$SCRIPT_DIR/$CAPTURE_NAME"
elif [[ -d "$SCRIPT_DIR/../../dist/$CAPTURE_NAME" ]]; then
    CAPTURE_DIST_DIR="$SCRIPT_DIR/../../dist/$CAPTURE_NAME"
fi

# --- Install to /opt ---
echo "➡️  Creating install dir: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

echo "➡️  Copying files..."
cp -r "$DIST_DIR"/* "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/$EXEC_NAME"

# --- Symlink ---
echo "➡️  Creating symlink: $BIN_LINK"
ln -sf "$INSTALL_DIR/$EXEC_NAME" "$BIN_LINK"

# --- Quick Capture ---
if [[ -d "$CAPTURE_DIST_DIR" && -f "$CAPTURE_DIST_DIR/$CAPTURE_NAME" ]]; then
    echo "➡️  Installing Quick Capture to $CAPTURE_INSTALL_DIR"
    mkdir -p "$CAPTURE_INSTALL_DIR"
    cp -r "$CAPTURE_DIST_DIR"/* "$CAPTURE_INSTALL_DIR/"
    chmod +x "$CAPTURE_INSTALL_DIR/$CAPTURE_NAME"
    echo "➡️  Creating symlink: $CAPTURE_LINK"
    ln -sf "$CAPTURE_INSTALL_DIR/$CAPTURE_NAME" "$CAPTURE_LINK"
else
    echo "ℹ️  Quick Capture bundle not found; skipping."
fi

# --- Icon ---
if [[ -f "$ICON_SOURCE" ]]; then
    echo "➡️  Installing icon to $ICON_TARGET"
    cp "$ICON_SOURCE" "$ICON_TARGET"
else
    echo "ℹ️  No icon found at $ICON_SOURCE — skipping icon install"
fi

# --- Desktop entry ---
echo "➡️  Creating desktop entry at $DESKTOP_FILE"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Local-first notes, tasks, and knowledge management
Exec=$INSTALL_DIR/$EXEC_NAME
Icon=$ICON_TARGET
Terminal=false
Categories=Office;TextEditor;Utility;
StartupNotify=true
StartupWMClass=stillpoint
EOF

chmod 644 "$DESKTOP_FILE"

# Update desktop database
if command -v update-desktop-database &> /dev/null; then
    echo "➡️  Updating desktop database..."
    update-desktop-database /usr/share/applications
fi

echo ""
echo "🎉 $APP_NAME installed successfully!"
echo ""
echo "Launch from:"
echo "  • Applications menu → StillPoint"
echo "  • Terminal: stillpoint"
echo ""
