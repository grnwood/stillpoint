#!/usr/bin/env bash

set -e

# --- Require sudo ---
if [[ $EUID -ne 0 ]]; then
    echo "❌ This installer must be run with sudo."
    echo "   Try: sudo ./install.sh"
    exit 1
fi

APP_NAME="StillPoint"
EXEC_NAME="StillPoint"               # name of your executable
DIST_DIR="../../dist/StillPoint"              # PyInstaller dist directory
INSTALL_DIR="/opt/stillpoint"        # permanent install location
BIN_LINK="/usr/local/bin/stillpoint" # global symlink
ICON_SOURCE="../../assets/icon.png"       # optional local icon
ICON_TARGET="/usr/share/icons/stillpoint.png"
DESKTOP_FILE="/usr/share/applications/stillpoint.desktop"

echo "📦 Installing $APP_NAME..."

# --- Check dist folder ---
if [[ ! -d "$DIST_DIR" ]]; then
    echo "❌ dist/ folder not found: $DIST_DIR"
    exit 1
fi

if [[ ! -f "$DIST_DIR/$EXEC_NAME" ]]; then
    echo "❌ Executable not found: $DIST_DIR/$EXEC_NAME"
    exit 1
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

# --- Icon ---
if [[ -f "$ICON_SOURCE" ]]; then
    echo "➡️  Installing icon to $ICON_TARGET"
    cp "$ICON_SOURCE" "$ICON_TARGET"
else
    echo "ℹ️  No local icon found — skipping icon install"
fi

# --- Desktop entry ---
echo "➡️  Creating desktop entry at $DESKTOP_FILE"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=StillPoint Desktop
Exec=$INSTALL_DIR/$EXEC_NAME
Icon=$ICON_TARGET
Terminal=false
Categories=Utility;Notes;
StartupNotify=true
StartupWMClass=StillPoint
EOF

chmod +x "$DESKTOP_FILE"

echo ""
echo "🎉 $APP_NAME installed successfully!"
echo "You can now launch it from: Menu → Accessories → StillPoint"
echo "Or run from terminal: stillpoint"
echo ""