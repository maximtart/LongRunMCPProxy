"""Auto-approve Xcode MCP agent permission dialogs via AppleScript."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("longrun-mcp-proxy")

_approver_proc: subprocess.Popen | None = None

# AppleScript is bundled with the package
_SCRIPT_PATH = Path(__file__).parent.parent.parent.parent / "deploy" / "auto_approve_xcode_mcp.applescript"


def start_auto_approver(script_path: Path | None = None) -> subprocess.Popen | None:
    """Start the AppleScript auto-approver for Xcode MCP dialogs.

    Requires: macOS with Accessibility access for the calling process.
    """
    global _approver_proc
    script = script_path or _SCRIPT_PATH
    if not script.exists():
        logger.warning("Auto-approver script not found: %s", script)
        return None
    try:
        proc = subprocess.Popen(
            ["osascript", str(script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _approver_proc = proc
        logger.info("Auto-approver started (PID %d)", proc.pid)
        return proc
    except Exception as e:
        logger.warning("Failed to start auto-approver: %s", e)
        return None


def stop_auto_approver() -> None:
    """Stop the auto-approver process."""
    global _approver_proc
    if _approver_proc and _approver_proc.poll() is None:
        _approver_proc.terminate()
        try:
            _approver_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _approver_proc.kill()
        logger.info("Auto-approver stopped")
    _approver_proc = None
