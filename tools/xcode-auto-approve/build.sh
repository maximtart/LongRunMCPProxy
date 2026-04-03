#!/bin/bash
# Build the xcode-auto-approve Swift binary.
# Requires: Xcode CLI tools (swiftc).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$SCRIPT_DIR/../../dist"
mkdir -p "$OUT_DIR"

echo "Compiling xcode-auto-approve..."
swiftc "$SCRIPT_DIR/main.swift" -O -o "$OUT_DIR/xcode-auto-approve"
echo "Built: $OUT_DIR/xcode-auto-approve"
