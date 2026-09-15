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
  "../../packaging/macos/README.txt"; do
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

echo "Building StillPoint ${STILLPOINT_VERSION}"
echo "Python: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install pyinstaller
"$VENV_PYTHON" -m pip install --no-binary charset_normalizer -r ../../sp/requirements.txt

"$VENV_DIR/bin/pyinstaller" -y --clean ../../packaging/sp-macos.spec
"$VENV_DIR/bin/pyinstaller" -y --clean ../../packaging/stillpoint-capture.spec

test -d ../../dist/StillPoint.app
test -f ../../dist/StillPoint.app/Contents/Info.plist
test -x ../../dist/StillPoint.app/Contents/MacOS/StillPoint
test -x ../../dist/stillpoint-capture/stillpoint-capture

MAIN_BINARY_TYPE="$(file ../../dist/StillPoint.app/Contents/MacOS/StillPoint)"
echo "$MAIN_BINARY_TYPE"
if [[ "$MAIN_BINARY_TYPE" != *"Mach-O"* ]]; then
  echo "ERROR: StillPoint executable is not a Mach-O binary." >&2
  exit 1
fi

BUNDLE_SIZE_MB="$(du -sm ../../dist/StillPoint.app | awk '{print $1}')"
echo "Bundle size: ${BUNDLE_SIZE_MB} MB"
if (( BUNDLE_SIZE_MB < 50 )); then
  echo "ERROR: Bundle is too small (${BUNDLE_SIZE_MB} MB); dependencies may be missing." >&2
  exit 1
fi

rm -rf ../../dist/bundle
mkdir -p ../../dist/bundle "$OUTPUT_DIR"
cp -R ../../dist/StillPoint.app ../../dist/bundle/
cp -R ../../dist/stillpoint-capture ../../dist/bundle/
cp ../../packaging/macos/README.txt ../../dist/bundle/README.txt

rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent ../../dist/bundle "$ZIP_PATH"
echo "Created: $ZIP_PATH"

if (( BUILD_DMG )); then
  DMG_STAGE="dist/dmg"
  rm -rf "$DMG_STAGE"
  mkdir -p "$DMG_STAGE"
  cp -R ../../dist/StillPoint.app "$DMG_STAGE/"
  cp -R ../../dist/stillpoint-capture "$DMG_STAGE/"
  cp ../../packaging/macos/README.txt "$DMG_STAGE/README.txt"
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
