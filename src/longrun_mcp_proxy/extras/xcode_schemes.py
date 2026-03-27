"""Extra MCP tools for Xcode scheme/destination management via JXA and AppleScript.

Native Xcode MCP (xcrun mcpbridge) does not expose scheme or destination
selection — BuildProject and RenderPreview always use what's active in Xcode UI.
These tools fill the gap via JavaScript for Automation (JXA) and AppleScript.

Note: Xcode 26 has a bug where activeRunDestination getter returns nil/missing
value via both JXA and AppleScript, but the *setter* works correctly (verified
in Xcode UI). So set_run_destination works but cannot verify the result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess

logger = logging.getLogger("longrun-mcp-proxy")

# Tool names exposed by this module — used for auto-detection.
SCHEME_TOOL_NAMES = {"get_schemes", "set_active_scheme", "get_run_destinations", "set_run_destination"}

# JXA helper: find workspace document by path (matches .xcodeproj or .xcworkspace).
# Xcode runs as a single process — multiple open projects are separate
# workspaceDocuments within that process.  We match by exact path or by
# containment (workspace path contains the project dir or vice-versa).
# No fuzzy/basename fallback to avoid matching the wrong workspace when
# multiple projects with similar names are open.
_JXA_FIND_WORKSPACE = """
(function(targetPath) {
    const xcode = Application("Xcode");
    const docs = xcode.workspaceDocuments();
    // Normalize: strip trailing slash
    const target = targetPath.replace(/\\/+$/, "");
    for (const doc of docs) {
        const p = doc.path().replace(/\\/+$/, "");
        if (p === target || p.startsWith(target + "/") || target.startsWith(p + "/")) {
            return doc;
        }
    }
    const openPaths = docs.map(d => d.path());
    throw new Error("Workspace not found for: " + target +
        ". Open workspaces: " + JSON.stringify(openPaths));
})(TARGET_PATH)
"""

_JXA_GET_SCHEMES = """
(function() {
    const ws = FIND_WORKSPACE;
    const schemes = ws.schemes();
    const active = ws.activeScheme();
    const activeId = active ? active.id() : null;
    const result = schemes.map(function(s) {
        return { name: s.name(), id: s.id(), isActive: s.id() === activeId };
    });
    return JSON.stringify(result, null, 2);
})()
"""

_JXA_GET_RUN_DESTINATIONS = """
(function() {
    const ws = FIND_WORKSPACE;
    const dests = ws.runDestinations();
    const active = ws.activeRunDestination();
    const activeName = active ? active.name() : null;
    const result = [];
    for (var i = 0; i < dests.length; i++) {
        var d = dests[i];
        result.push({
            name: d.name(),
            platform: d.platform(),
            architecture: d.architecture(),
            isActive: d.name() === activeName
        });
    }
    return JSON.stringify(result, null, 2);
})()
"""

_JXA_SET_ACTIVE_SCHEME = """
(function() {
    const ws = FIND_WORKSPACE;
    const schemes = ws.schemes();
    const schemeName = SCHEME_NAME;
    let target = schemes.find(function(s) { return s.name() === schemeName; });
    if (!target) {
        // Case-insensitive fallback
        const lower = schemeName.toLowerCase();
        target = schemes.find(function(s) { return s.name().toLowerCase() === lower; });
    }
    if (!target) {
        const available = schemes.map(function(s) { return s.name(); });
        throw new Error("Scheme not found: " + schemeName +
            ". Available: " + JSON.stringify(available));
    }
    ws.activeScheme = target;
    return JSON.stringify({ scheme: target.name(), message: "Active scheme set to: " + target.name() });
})()
"""


def _build_jxa(template: str, workspace_path: str, **replacements: str) -> str:
    """Build a complete JXA script with workspace lookup inlined."""
    find_ws = _JXA_FIND_WORKSPACE.replace("TARGET_PATH", json.dumps(workspace_path))
    script = template.replace("FIND_WORKSPACE", find_ws)
    for key, value in replacements.items():
        script = script.replace(key, json.dumps(value))
    return script


async def _run_osascript(script: str, language: str = "JavaScript") -> str:
    """Execute an osascript and return stdout.

    Args:
        script: Script content.
        language: "JavaScript" for JXA, "AppleScript" for native AppleScript.
    """
    args = ["osascript"]
    if language == "JavaScript":
        args += ["-l", "JavaScript"]
    args += ["-e", script]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        error_msg = stderr.decode().strip() if stderr else "Unknown osascript error"
        for line in error_msg.splitlines():
            if "Error" in line or "not found" in line.lower():
                error_msg = line.strip()
                break
        return json.dumps({"error": error_msg})
    return stdout.decode().strip()


async def _run_jxa(script: str) -> str:
    """Execute a JXA script via osascript and return stdout."""
    return await _run_osascript(script, language="JavaScript")


async def get_schemes(workspace_path: str) -> str:
    """List available schemes for a workspace/project open in Xcode.

    Returns JSON array of schemes with name, id, and isActive flag."""
    script = _build_jxa(_JXA_GET_SCHEMES, workspace_path)
    return await _run_jxa(script)


async def set_active_scheme(workspace_path: str, scheme_name: str) -> str:
    """Set the active scheme for a workspace/project open in Xcode.

    After calling this, BuildProject and RenderPreview will use the new scheme."""
    script = _build_jxa(_JXA_SET_ACTIVE_SCHEME, workspace_path, SCHEME_NAME=scheme_name)
    return await _run_jxa(script)


async def set_run_destination(workspace_path: str, destination_name: str) -> str:
    """Set the active run destination for a workspace open in Xcode.

    Uses AppleScript (not JXA) because the setter only works via native
    AppleScript in Xcode 26. The getter is broken in both languages but
    the setter correctly updates Xcode UI.

    Use get_run_destinations first to see available destination names."""
    # Extract workspace document name from path (e.g. "BNineBanking.xcworkspace")
    ws_name = workspace_path.rstrip("/").split("/")[-1]

    # AppleScript — setter works, getter broken in Xcode 26
    applescript = f'''
tell application "Xcode"
    set workspaceDocument to workspace document "{ws_name}"
    set targetDest to run destination "{destination_name}" of workspaceDocument
    set active run destination of workspaceDocument to targetDest
    return "Active run destination set to: " & name of targetDest
end tell
'''
    result = await _run_osascript(applescript, language="AppleScript")
    if result.startswith("{"):
        return result  # error JSON
    return json.dumps({
        "destination": destination_name,
        "message": result,
    })


async def get_run_destinations(workspace_path: str) -> str:
    """List available run destinations (simulators/devices) for a workspace open in Xcode.

    Returns JSON array of destinations with name, platform, architecture, and isActive flag."""
    script = _build_jxa(_JXA_GET_RUN_DESTINATIONS, workspace_path)
    return await _run_jxa(script)


# Tool definitions for proxy registration (MCP input schema format).
EXTRA_TOOLS = [
    {
        "name": "get_schemes",
        "description": "List available Xcode schemes for a workspace/project. Returns JSON array with name, id, and isActive flag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_path": {
                    "type": "string",
                    "description": "Absolute path to .xcodeproj or .xcworkspace open in Xcode",
                },
            },
            "required": ["workspace_path"],
        },
        "handler": get_schemes,
    },
    {
        "name": "set_active_scheme",
        "description": "Set the active Xcode scheme. After this, BuildProject and RenderPreview will use the selected scheme.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_path": {
                    "type": "string",
                    "description": "Absolute path to .xcodeproj or .xcworkspace open in Xcode",
                },
                "scheme_name": {
                    "type": "string",
                    "description": "Name of the scheme to activate",
                },
            },
            "required": ["workspace_path", "scheme_name"],
        },
        "handler": set_active_scheme,
    },
    {
        "name": "get_run_destinations",
        "description": "List available run destinations (simulators/devices) for a workspace open in Xcode. Returns JSON array with name, platform, architecture, and isActive flag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_path": {
                    "type": "string",
                    "description": "Absolute path to .xcodeproj or .xcworkspace open in Xcode",
                },
            },
            "required": ["workspace_path"],
        },
        "handler": get_run_destinations,
    },
    {
        "name": "set_run_destination",
        "description": "Set the active run destination (simulator/device) for a workspace open in Xcode. Use get_run_destinations first to see available names.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_path": {
                    "type": "string",
                    "description": "Absolute path to .xcodeproj or .xcworkspace open in Xcode",
                },
                "destination_name": {
                    "type": "string",
                    "description": "Exact name of the run destination, e.g. 'iPhone 17 Pro (26.3.1)'",
                },
            },
            "required": ["workspace_path", "destination_name"],
        },
        "handler": set_run_destination,
    },
]
