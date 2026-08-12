#!/bin/bash
# Freeze the proxy into a standalone signed Mach-O binary.
#
# Why: Xcode 27's headless MCP server attributes a connection to the PARENT
# process of `mcpbridge`. When the proxy runs as a Python script that parent is
# the shared uv interpreter, so the granted trust reads
# `signingIdentifier=python3` and covers every tool running on that
# interpreter. A frozen binary narrows the trust to this proxy alone and makes
# it independent of `uv python` upgrades.
#
# Requires: repo .venv with dependencies + pyinstaller, and a codesigning
# identity. Override the identity with $SIGN_IDENTITY.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="$REPO_DIR/dist"
VENV="$REPO_DIR/.venv"
NAME="longrun-mcp-proxy"

SIGN_IDENTITY="${SIGN_IDENTITY:-Apple Development: Maksym Tartachnyk (4298S7CRKR)}"

if [[ ! -x "$VENV/bin/pyinstaller" ]]; then
    echo "pyinstaller missing — run: uv pip install --python $VENV/bin/python pyinstaller" >&2
    exit 1
fi

echo "Freezing $NAME..."
"$VENV/bin/pyinstaller" \
    --onefile \
    --name "$NAME" \
    --distpath "$OUT_DIR" \
    --workpath "$REPO_DIR/build/pyinstaller" \
    --specpath "$REPO_DIR/build" \
    --collect-all fastmcp \
    --collect-all mcp \
    --paths "$REPO_DIR/src" \
    --noconfirm \
    --clean \
    "$REPO_DIR/src/longrun_mcp_proxy/__main__.py"

BIN="$OUT_DIR/$NAME"

# The signing identifier is what Xcode stores in its permission record, so pin
# it explicitly instead of letting codesign derive one from the file name.
echo "Signing as '$SIGN_IDENTITY' (identifier: $NAME)..."
codesign --force -i "$NAME" -s "$SIGN_IDENTITY" "$BIN"
codesign -v --verify --strict "$BIN"

echo
codesign -dv --verbose=2 "$BIN" 2>&1 | grep -E "^Identifier|^Authority=Apple|TeamIdentifier"
echo
echo "Built: $BIN"
echo "Point your MCP client at that path (e.g. \$LONGRUN_MCP in .mcp.json)."
