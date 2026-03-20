"""Set Xcode defaults for MCP permissions."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("longrun-mcp-proxy")

XCODE_MCP_DEFAULTS = (
    "IDEAllowUnauthenticatedAgents",
    "IDEChatAllowAgents",
    "IDEChatAgenticChatSkipPermissions",
    "IDEChatInternalAllowUntrustedAgentsWithoutUserInteraction",
    "IDEChatSkipPermissionsForTools",
    "IDEChatSkipPermissionsForTrustedTools",
)

# Default async tools for native Xcode MCP (xcrun mcpbridge)
XCODE_NATIVE_ASYNC_TOOLS = {
    "BuildProject",
    "RunAllTests",
    "RunSomeTests",
    "RenderPreview",
    "ExecuteSnippet",
}

# Default async tools for XcodeBuildMCP (Sentry)
XCODE_BUILD_ASYNC_TOOLS = {
    "build_sim",
    "build_run_sim",
    "test_sim",
    "clean",
}

# Combined set of all known long-running tools for auto-detection.
# When --async-tools is not specified, proxy matches discovered downstream
# tool names against this set and wraps any matches automatically.
KNOWN_ASYNC_TOOLS = XCODE_NATIVE_ASYNC_TOOLS | XCODE_BUILD_ASYNC_TOOLS

# Tools that need a retry after completion to get a warmed-up result.
# For example, RenderPreview returns a screenshot before async images load;
# a second call after a delay returns the correct screenshot with cached images.
KNOWN_RETRY_TOOLS: dict[str, float] = {
    "RenderPreview": 3.0,  # seconds to wait before retry
}


def set_xcode_mcp_defaults() -> None:
    """Set all known Xcode defaults that may help with MCP permissions."""
    for key in XCODE_MCP_DEFAULTS:
        subprocess.run(
            ["defaults", "write", "com.apple.dt.Xcode", key, "-bool", "YES"],
            capture_output=True,
        )
    logger.info("Xcode MCP defaults set (%d keys)", len(XCODE_MCP_DEFAULTS))
