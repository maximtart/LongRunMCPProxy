#!/usr/bin/env bash
# Install xcode-auto-approve as a launchd user agent.
# Compiles Swift binary and sets up auto-start at login.
#
# Usage: ./deploy/install.sh
# Requires: Xcode CLI tools (swiftc), Accessibility permission
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="com.b9.xcode-auto-approve"
PLIST_SRC="$SCRIPT_DIR/$PLIST_NAME.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
LOG_DIR="/tmp/orchestra"
BINARY_DIR="$HOME/.local/bin"
BINARY_PATH="$BINARY_DIR/xcode-auto-approve"

echo "xcode-auto-approve installer"
echo "============================"

# Build Swift binary
echo "Compiling Swift binary..."
mkdir -p "$BINARY_DIR"
if ! command -v swiftc &>/dev/null; then
    echo "ERROR: swiftc not found. Install Xcode CLI tools: xcode-select --install"
    exit 1
fi
swiftc "$REPO_DIR/tools/xcode-auto-approve/main.swift" -O -o "$BINARY_PATH"
echo "  Binary: $BINARY_PATH"

# Generate and install launchd plist
echo "Installing launchd agent..."
mkdir -p "$LOG_DIR"
sed -e "s|__BINARY_PATH__|$BINARY_PATH|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Load agent
launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"

echo ""
echo "Done! Auto-approver is running."
echo "  Status : launchctl list | grep xcode-auto-approve"
echo "  Log    : tail -f $LOG_DIR/xcode-auto-approve.log"
echo "  Stop   : launchctl bootout gui/$(id -u)/$PLIST_NAME"
echo ""
echo "NOTE: Accessibility permission required."
echo "  System Settings > Privacy & Security > Accessibility > enable xcode-auto-approve"
