"""MCP proxy with async wrapper for long-running tools."""

__version__ = "0.1.0"

from longrun_mcp_proxy.client_identity import (
    FALLBACK_CLIENT_NAME,
    auto_client_name,
    client_info,
    resolve_client_name,
)
from longrun_mcp_proxy.job_store import Job, JobStore
from longrun_mcp_proxy.output_filter import filter_large_output
from longrun_mcp_proxy.proxy_stdio import build_proxy, connect_and_register
from longrun_mcp_proxy.proxy_persistent import (
    PersistentDownstream,
    start_persistent_proxy,
    stop_persistent_proxy,
)

__all__ = [
    "FALLBACK_CLIENT_NAME",
    "auto_client_name",
    "client_info",
    "resolve_client_name",
    "Job",
    "JobStore",
    "build_proxy",
    "connect_and_register",
    "filter_large_output",
    "PersistentDownstream",
    "start_persistent_proxy",
    "stop_persistent_proxy",
]
