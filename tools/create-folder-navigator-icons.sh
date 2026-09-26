#!/usr/bin/env bash
# Regenerate Folder Navigator platform icons from the checked-in 1024px master.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ICON_DIR="$ROOT_DIR/sp/assets/icons"
PNG_DIR="$ICON_DIR/linux-png"
SOURCE="$PNG_DIR/folder-navigator-1024x1024.png"

if [[ ! -f "$SOURCE" ]]; then
    echo "Error: source icon not found: $SOURCE" >&2
    exit 1
fi

if command -v magick >/dev/null 2>&1; then
    IMAGE_TOOL=(magick)
elif command -v convert >/dev/null 2>&1; then
    IMAGE_TOOL=(convert)
else
    echo "Error: ImageMagick is required (magick or convert)." >&2
    exit 1
fi

for size in 16 24 32 48 64 128 256 512; do
    "${IMAGE_TOOL[@]}" "$SOURCE" -filter Lanczos -resize "${size}x${size}" \
        "$PNG_DIR/folder-navigator-${size}x${size}.png"
done

"${IMAGE_TOOL[@]}" \
    "$PNG_DIR/folder-navigator-16x16.png" \
    "$PNG_DIR/folder-navigator-24x24.png" \
    "$PNG_DIR/folder-navigator-32x32.png" \
    "$PNG_DIR/folder-navigator-48x48.png" \
    "$PNG_DIR/folder-navigator-64x64.png" \
    "$PNG_DIR/folder-navigator-128x128.png" \
    "$PNG_DIR/folder-navigator-256x256.png" \
    "$ICON_DIR/FolderNavigator.ico"

if [[ "$(uname -s)" == "Darwin" ]]; then
    ICONSET_DIR="$(mktemp -d)/FolderNavigator.iconset"
    mkdir -p "$ICONSET_DIR"
    trap 'rm -rf "${ICONSET_DIR%/FolderNavigator.iconset}"' EXIT
    cp "$PNG_DIR/folder-navigator-16x16.png" "$ICONSET_DIR/icon_16x16.png"
    cp "$PNG_DIR/folder-navigator-32x32.png" "$ICONSET_DIR/icon_16x16@2x.png"
    cp "$PNG_DIR/folder-navigator-32x32.png" "$ICONSET_DIR/icon_32x32.png"
    cp "$PNG_DIR/folder-navigator-64x64.png" "$ICONSET_DIR/icon_32x32@2x.png"
    cp "$PNG_DIR/folder-navigator-128x128.png" "$ICONSET_DIR/icon_128x128.png"
    cp "$PNG_DIR/folder-navigator-256x256.png" "$ICONSET_DIR/icon_128x128@2x.png"
    cp "$PNG_DIR/folder-navigator-256x256.png" "$ICONSET_DIR/icon_256x256.png"
    cp "$PNG_DIR/folder-navigator-512x512.png" "$ICONSET_DIR/icon_256x256@2x.png"
    cp "$PNG_DIR/folder-navigator-512x512.png" "$ICONSET_DIR/icon_512x512.png"
    cp "$SOURCE" "$ICONSET_DIR/icon_512x512@2x.png"
    iconutil -c icns "$ICONSET_DIR" -o "$ICON_DIR/FolderNavigator.icns"
elif command -v png2icns >/dev/null 2>&1; then
    png2icns "$ICON_DIR/FolderNavigator.icns" \
        "$PNG_DIR/folder-navigator-16x16.png" \
        "$PNG_DIR/folder-navigator-32x32.png" \
        "$PNG_DIR/folder-navigator-48x48.png" \
        "$PNG_DIR/folder-navigator-128x128.png" \
        "$PNG_DIR/folder-navigator-256x256.png" \
        "$PNG_DIR/folder-navigator-512x512.png" \
        "$SOURCE"
else
    echo "Warning: install libicns-utils (png2icns) to regenerate FolderNavigator.icns." >&2
fi

echo "Created Folder Navigator PNG, ICO, and ICNS assets in $ICON_DIR"
