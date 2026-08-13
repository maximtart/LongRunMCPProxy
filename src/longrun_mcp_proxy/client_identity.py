"""Identity the proxy presents to downstream MCP servers.

Downstream servers surface `clientInfo` from the MCP handshake in their own UI —
Xcode 27 lists it under Connected Agents. The MCP SDK default is
`Implementation(name="mcp")`, and a fixed proxy name is barely better: every
session on the machine shows up as an identical row, so an idle agent cannot be
told from the one holding a build.

The agent that launched us leaves enough in the environment to tell sessions
apart, so the default name is derived per process:

    dev1@B9-12361 · vscode · 2069486e
    │    │          │        └─ CLAUDE_CODE_SESSION_ID, first 8 chars
    │    │          └─ host app (TERM_PROGRAM, else CLAUDE_CODE_ENTRYPOINT)
    │    └─ current git branch of the project
    └─ project directory (CLAUDE_PROJECT_DIR, else cwd)
"""

from __future__ import annotations

import os
import subprocess
from importlib import metadata
from pathlib import Path

from mcp import types as mcp_types

FALLBACK_CLIENT_NAME = "Longrun"
CLIENT_NAME_ENV = "LONGRUN_CLIENT_NAME"
CLIENT_NOTE_ENV = "LONGRUN_CLIENT_NOTE"

# CLAUDE_CODE_ENTRYPOINT values are internal-ish; shorten the ones we see and
# pass anything else through rather than hiding it behind "unknown".
_ENTRYPOINT_LABELS = {
    "claude-vscode": "vscode",
    "sdk-ts": "sdk",
    "sdk-py": "sdk",
    "cli": "cli",
}


def _package_version() -> str:
    try:
        return metadata.version("longrun-mcp-proxy")
    except metadata.PackageNotFoundError:
        return "0"


def _project_dir() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _git_branch(project: Path) -> str | None:
    """Current branch, or None when this isn't a work tree."""
    try:
        out = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    branch = out.stdout.strip()
    return branch if out.returncode == 0 and branch and branch != "HEAD" else None


def _host_label() -> str | None:
    term = os.environ.get("TERM_PROGRAM", "").strip()
    if term and term != "Apple_Terminal":
        return term
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").strip()
    if entrypoint:
        return _ENTRYPOINT_LABELS.get(entrypoint, entrypoint)
    return "Terminal" if term else None


def _session_short() -> str | None:
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    return sid[:8] if sid else None


# Ticket-prefixed branch names run long ("B9-12361-ios-screen-with-the-…") and
# the downstream UI is a narrow list, so keep the identifying head only.
BRANCH_MAX = 24


def _shorten(branch: str) -> str:
    return branch if len(branch) <= BRANCH_MAX else branch[:BRANCH_MAX].rstrip("-_") + "…"


def auto_client_name() -> str:
    """Best identity derivable from this process's environment."""
    project = _project_dir()
    label = project.name or str(project)

    branch = _git_branch(project)
    if branch:
        label = f"{label}@{_shorten(branch)}"

    parts = [label]
    for extra in (_host_label(), _session_short()):
        if extra:
            parts.append(extra)
    return " · ".join(parts)


def resolve_client_name(name: str | None = None) -> str:
    """Explicit argument wins, then $LONGRUN_CLIENT_NAME, then auto-detection."""
    explicit = name or os.environ.get(CLIENT_NAME_ENV)
    if explicit:
        return explicit
    try:
        return auto_client_name() or FALLBACK_CLIENT_NAME
    except Exception:  # identity must never block a connection
        return FALLBACK_CLIENT_NAME


def _title(resolved_name: str, note: str | None) -> str:
    """Long form: full project path, pid, and the operator's note if any."""
    bits = [f"Longrun — {resolved_name}", f"pid {os.getpid()}", str(_project_dir())]
    note = note or os.environ.get(CLIENT_NOTE_ENV)
    if note:
        bits.append(note.strip())
    return " | ".join(bits)


def client_info(
    name: str | None = None, note: str | None = None
) -> mcp_types.Implementation:
    """Build the clientInfo sent during the downstream MCP handshake."""
    resolved = resolve_client_name(name)
    return mcp_types.Implementation(
        name=resolved,
        title=_title(resolved, note),
        version=_package_version(),
    )
