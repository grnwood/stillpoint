#!/usr/bin/env bash
# Build StillPoint locally on macOS.
#

# Usage:
#   ./build-macos.sh                    # Build ZIP and DMG
#   ./build-macos.sh --zip-only         # Build only the ZIP used by GitHub Actions
#   ./build-macos.sh --version v1.2.3   # Override the embedded release version

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BUILD_DMG=1
VERSION=""

usage() {
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --zip-only)
      BUILD_DMG=0
      shift
      ;;
    --version)
      [[ $# -ge 2 ]] || { echo "ERROR: --version requires a value" >&2; exit 2; }
      VERSION="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: This build must run on macOS." >&2
  exit 1
fi

for required_file in \
  "../../sp/requirements.txt" \
  "../../packaging/sp-macos.spec" \
  "../../packaging/stillpoint-capture.spec" \
  "../../packaging/macos/README.txt" \
  "../../packaging/macos/stillpoint.sh"; do
  if [[ ! -f "$required_file" ]]; then
    echo "ERROR: Missing $required_file. Run this script from the StillPoint repository." >&2
    exit 1
  fi
done

PYTHON_BIN="python"

if [[ -z "$VERSION" ]]; then
  VERSION="$(git describe --tags --always --dirty 2>/dev/null || true)"
  VERSION="${VERSION:-local}"
fi
export STILLPOINT_VERSION="$VERSION"

VENV_DIR="venv"
OUTPUT_DIR="dist"
ZIP_PATH="$OUTPUT_DIR/StillPoint-macOS.zip"
DMG_PATH="$OUTPUT_DIR/StillPoint-macOS.dmg"
MAIN_APP="$OUTPUT_DIR/StillPoint.app"
CAPTURE_OUTPUT="$OUTPUT_DIR/stillpoint-capture"
FOLDER_NAV_APP="$OUTPUT_DIR/StillPoint Folder Navigator.app"
QUICK_CAPTURE_APP="$OUTPUT_DIR/StillPoint Quick Capture.app"
BUNDLE_DIR="$OUTPUT_DIR/bundle"

echo "Building StillPoint ${STILLPOINT_VERSION}"
echo "Python: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
HOST_ARCH="$(uname -m)"
PYTHON_ARCH="$("$VENV_PYTHON" -c 'import platform; print(platform.machine())')"
if [[ "$PYTHON_ARCH" != "$HOST_ARCH" ]]; then
  echo "ERROR: Build Python is ${PYTHON_ARCH}, but this Mac is ${HOST_ARCH}." >&2
  echo "Remove packaging/macos/venv and rebuild with a native Python." >&2
  exit 1
fi
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install pyinstaller
"$VENV_PYTHON" -m pip install --no-binary charset_normalizer -r ../../sp/requirements.txt

"$VENV_PYTHON" ../../packaging/fetch_ripgrep.py
"$VENV_DIR/bin/pyinstaller" -y --clean --distpath "$OUTPUT_DIR" ../../packaging/sp-macos.spec
"$VENV_DIR/bin/pyinstaller" -y --clean --distpath "$OUTPUT_DIR" ../../packaging/stillpoint-capture.spec

../../packaging/macos/build-folder-navigator-app.sh "$OUTPUT_DIR" "$STILLPOINT_VERSION"
../../packaging/macos/build-quick-capture-app.sh "$OUTPUT_DIR" "$STILLPOINT_VERSION"
FOLDER_NAV_EXECUTABLE="$FOLDER_NAV_APP/Contents/MacOS/stillpoint-folder-navigator"
QUICK_CAPTURE_EXECUTABLE="$QUICK_CAPTURE_APP/Contents/MacOS/stillpoint-quick-capture"

test -d "$MAIN_APP"
test -d "$FOLDER_NAV_APP"
test -x "$FOLDER_NAV_EXECUTABLE"
test -d "$QUICK_CAPTURE_APP"
test -x "$QUICK_CAPTURE_EXECUTABLE"
test -x "$QUICK_CAPTURE_APP/Contents/Resources/stillpoint-capture/stillpoint-capture"
test -f "$MAIN_APP/Contents/Info.plist"
test -x "$MAIN_APP/Contents/MacOS/StillPoint"
test -x "$CAPTURE_OUTPUT/stillpoint-capture"
test -x ../../packaging/macos/stillpoint.sh

MAIN_BINARY_TYPE="$(file "$MAIN_APP/Contents/MacOS/StillPoint")"
echo "$MAIN_BINARY_TYPE"
if [[ "$MAIN_BINARY_TYPE" != *"Mach-O"* ]]; then
  echo "ERROR: StillPoint executable is not a Mach-O binary." >&2
  exit 1
fi
if ! lipo -archs "$MAIN_APP/Contents/MacOS/StillPoint" | tr ' ' '\n' | grep -qx "$HOST_ARCH"; then
  echo "ERROR: StillPoint executable does not contain the host architecture (${HOST_ARCH})." >&2
  exit 1
fi

BUNDLE_SIZE_MB="$(du -sm "$MAIN_APP" | awk '{print $1}')"
echo "Bundle size: ${BUNDLE_SIZE_MB} MB"
if (( BUNDLE_SIZE_MB < 50 )); then
  echo "ERROR: Bundle is too small (${BUNDLE_SIZE_MB} MB); dependencies may be missing." >&2
  exit 1
fi

rm -rf "$BUNDLE_DIR"
mkdir -p "$BUNDLE_DIR"
cp -R "$MAIN_APP" "$BUNDLE_DIR/"
cp -R "$FOLDER_NAV_APP" "$BUNDLE_DIR/"
cp -R "$QUICK_CAPTURE_APP" "$BUNDLE_DIR/"
cp -R "$CAPTURE_OUTPUT" "$BUNDLE_DIR/"
cp ../../packaging/macos/README.txt "$BUNDLE_DIR/README.txt"
cp ../../packaging/macos/stillpoint.sh "$BUNDLE_DIR/stillpoint.sh"
test -x "$BUNDLE_DIR/stillpoint.sh"

rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$BUNDLE_DIR" "$ZIP_PATH"
echo "Created: $ZIP_PATH"

if (( BUILD_DMG )); then
  DMG_STAGE="$OUTPUT_DIR/dmg"
  rm -rf "$DMG_STAGE"
  mkdir -p "$DMG_STAGE"
  cp -R "$MAIN_APP" "$DMG_STAGE/"
  cp -R "$FOLDER_NAV_APP" "$DMG_STAGE/"
  cp -R "$QUICK_CAPTURE_APP" "$DMG_STAGE/"
  cp -R "$CAPTURE_OUTPUT" "$DMG_STAGE/"
  cp ../../packaging/macos/README.txt "$DMG_STAGE/README.txt"
  cp ../../packaging/macos/stillpoint.sh "$DMG_STAGE/stillpoint.sh"
  test -x "$DMG_STAGE/stillpoint.sh"
  ln -s /Applications "$DMG_STAGE/Applications"

  rm -f "$DMG_PATH"
  hdiutil create \
    -volname "StillPoint" \
    -srcfolder "$DMG_STAGE" \
    -format UDZO \
    "$DMG_PATH"
  echo "Created: $DMG_PATH"
fi

echo
echo "Build complete."
echo "Note: these artifacts are not Developer ID signed or notarized."
