"""Identity the proxy presents to downstream MCP servers.

Downstream servers surface `clientInfo.name` from the MCP handshake in their
own UI — Xcode 27 lists it in the Agent Activity panel. The MCP SDK default is
`Implementation(name="mcp")`, so without this every proxied agent shows up as
a bare "mcp" row.
"""

from __future__ import annotations

import os
from importlib import metadata

from mcp import types as mcp_types

DEFAULT_CLIENT_NAME = "longrun mcp proxy"
CLIENT_NAME_ENV = "LONGRUN_CLIENT_NAME"


def _package_version() -> str:
    try:
        return metadata.version("longrun-mcp-proxy")
    except metadata.PackageNotFoundError:
        return "0"


def resolve_client_name(name: str | None = None) -> str:
    """Explicit argument wins, then $LONGRUN_CLIENT_NAME, then the default."""
    return name or os.environ.get(CLIENT_NAME_ENV) or DEFAULT_CLIENT_NAME


def client_info(name: str | None = None) -> mcp_types.Implementation:
    """Build the clientInfo sent during the downstream MCP handshake."""
    return mcp_types.Implementation(
        name=resolve_client_name(name), version=_package_version()
    )
