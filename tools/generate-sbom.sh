#!/usr/bin/env bash
set -euo pipefail

SBOM_OUT="../sbom.spdx.json"
VENV_DIR="../venv"

echo "▶ Generating SPDX SBOM from venv..."

# --- Sanity checks ---
if [[ ! -d "$VENV_DIR" ]]; then
  echo "ERROR: venv directory '$VENV_DIR' not found."
  exit 1
fi

# Activate venv
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Ensure syft exists
if ! command -v syft >/dev/null 2>&1; then
  echo "▶ Installing syft locally into venv..."
  curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
    | sh -s -- -b "$VENV_DIR/bin"
fi

SITE_PACKAGES="$(python - <<'EOF'
import site
print(site.getsitepackages()[0])
EOF
)"

echo "▶ Scanning site-packages:"
echo "  $SITE_PACKAGES"

syft scan "dir:$SITE_PACKAGES" -o spdx-json > "$SBOM_OUT"

echo "✔ SPDX SBOM written to $SBOM_OUT"
echo

echo "▶ Checking for disallowed licenses (block AGPL; block GPL unless LGPL alternative exists)..."

DISALLOWED=$(jq -r '
  .packages[]
  | . as $p
  | ($p.licenseDeclared // $p.licenseConcluded // "") as $lic
  | select($lic != "")
  # allow-list: pyinstaller (GPL w/ exception, typically build-time)
  | select(($p.name | ascii_downcase) != "pyinstaller")
  # Disallow:
  #  - any AGPL
  #  - any GPL where LGPL is NOT also present in the same license expression
  | select(
      ($lic | test("AGPL"; "i"))
      or
      (
        ($lic | test("GPL"; "i"))
        and
        (($lic | test("LGPL"; "i")) | not)
      )
    )
  | "\($p.name)  —  \($lic)"
' "$SBOM_OUT")

if [[ -n "$DISALLOWED" ]]; then
  echo "❌ Disallowed packages detected:"
  echo
  echo "$DISALLOWED"
  echo
  exit 1
fi

echo "✔ License policy passed (no AGPL; no GPL without an LGPL alternative)"
