#!/bin/bash
# Create macOS .icns file from PNG icon
# Run this on macOS before building the .app bundle

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ICON_PNG="$ROOT_DIR/sp/assets/sp-icon.png"
OUTPUT_ICNS="$ROOT_DIR/sp/assets/StillPoint.icns"

if [ ! -f "$ICON_PNG" ]; then
    echo "Error: Source icon not found: $ICON_PNG"
    exit 1
fi

# Check if we're on macOS
if [ "$(uname)" != "Darwin" ]; then
    echo "Warning: This script should be run on macOS to create .icns files"
    echo "If you're on Linux, you can use 'png2icns' or 'icnsutils' package:"
    echo "  sudo apt-get install icnsutils"
    echo "  png2icns '$OUTPUT_ICNS' '$ICON_PNG'"
    exit 1
fi

# Create temporary iconset directory
ICONSET_DIR="$ROOT_DIR/sp/assets/StillPoint.iconset"
mkdir -p "$ICONSET_DIR"

echo "Creating icon sizes for macOS .icns..."

# Generate all required sizes for macOS icons
# Using sips (built into macOS) to resize the PNG
sips -z 16 16     "$ICON_PNG" --out "$ICONSET_DIR/icon_16x16.png" > /dev/null 2>&1
sips -z 32 32     "$ICON_PNG" --out "$ICONSET_DIR/icon_16x16@2x.png" > /dev/null 2>&1
sips -z 32 32     "$ICON_PNG" --out "$ICONSET_DIR/icon_32x32.png" > /dev/null 2>&1
sips -z 64 64     "$ICON_PNG" --out "$ICONSET_DIR/icon_32x32@2x.png" > /dev/null 2>&1
sips -z 128 128   "$ICON_PNG" --out "$ICONSET_DIR/icon_128x128.png" > /dev/null 2>&1
sips -z 256 256   "$ICON_PNG" --out "$ICONSET_DIR/icon_128x128@2x.png" > /dev/null 2>&1
sips -z 256 256   "$ICON_PNG" --out "$ICONSET_DIR/icon_256x256.png" > /dev/null 2>&1
sips -z 512 512   "$ICON_PNG" --out "$ICONSET_DIR/icon_256x256@2x.png" > /dev/null 2>&1
sips -z 512 512   "$ICON_PNG" --out "$ICONSET_DIR/icon_512x512.png" > /dev/null 2>&1
sips -z 1024 1024 "$ICON_PNG" --out "$ICONSET_DIR/icon_512x512@2x.png" > /dev/null 2>&1

echo "Converting iconset to .icns..."
iconutil -c icns "$ICONSET_DIR" -o "$OUTPUT_ICNS"

# Clean up
rm -rf "$ICONSET_DIR"

echo "✓ Created: $OUTPUT_ICNS"
echo ""
echo "You can now build the macOS app with:"
echo "  pyinstaller -y packaging/sp-macos.spec"
