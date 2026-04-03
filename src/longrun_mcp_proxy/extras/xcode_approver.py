"""Auto-approve Xcode MCP agent permission dialogs.

Uses a compiled Swift binary (xcode-auto-approve) that polls Xcode via
AXUIElement API to find and click the "Allow" button on MCP connection
dialogs.  Works cross-Space because AXUIElement talks directly to the
app process via Mach IPC, not through WindowServer.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("longrun-mcp-proxy")

_approver_proc: subprocess.Popen | None = None

_BINARY_PATH = Path(__file__).parent.parent.parent.parent / "dist" / "xcode-auto-approve"


def start_auto_approver(
    agent_name: str = "longrun-mcp-proxy",
    binary_path: Path | None = None,
) -> subprocess.Popen | None:
    """Start the auto-approver for Xcode MCP dialogs.

    Requires: compiled Swift binary at dist/xcode-auto-approve.
    Build with: tools/xcode-auto-approve/build.sh
    Requires: macOS with Accessibility access for the calling process.
    """
    global _approver_proc
    binary = binary_path or _BINARY_PATH

    if not binary.exists():
        logger.error(
            "Auto-approver binary not found at %s. "
            "Run tools/xcode-auto-approve/build.sh to compile.",
            binary,
        )
        return None

    try:
        proc = subprocess.Popen(
            [str(binary), "--agent-name", agent_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
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
