"""Auto-approve Xcode MCP agent permission dialogs.

Uses a compiled Swift binary (xcode-auto-approve) that polls Xcode via
AXUIElement API to find and click the "Allow" button on MCP connection
dialogs.  Works cross-Space because AXUIElement talks directly to the
app process via Mach IPC, not through WindowServer.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("longrun-mcp-proxy")

_approver_proc: subprocess.Popen | None = None

# Search order: explicit path > PATH > relative to package source
_BINARY_NAME = "xcode-auto-approve"
_BINARY_PATH_DEV = Path(__file__).parent.parent.parent.parent / "dist" / _BINARY_NAME


def _find_binary() -> Path | None:
    """Find the xcode-auto-approve binary."""
    # 1. Relative to source (dev mode / editable install)
    if _BINARY_PATH_DEV.exists():
        return _BINARY_PATH_DEV
    # 2. On PATH (installed globally or via build.sh)
    found = shutil.which(_BINARY_NAME)
    if found:
        return Path(found)
    return None


def start_auto_approver(
    agent_name: str = "longrun-mcp-proxy",
    binary_path: Path | None = None,
) -> subprocess.Popen | None:
    """Start the auto-approver for Xcode MCP dialogs.

    Searches for xcode-auto-approve binary in:
    1. Explicit binary_path argument
    2. dist/xcode-auto-approve (relative to package source)
    3. PATH

    Build with: tools/xcode-auto-approve/build.sh
    Requires: macOS with Accessibility access for the calling process.
    """
    global _approver_proc
    binary = binary_path or _find_binary()

    if not binary:
        logger.error(
            "Auto-approver binary '%s' not found. "
            "Run tools/xcode-auto-approve/build.sh to compile, "
            "or place the binary on PATH.",
            _BINARY_NAME,
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
