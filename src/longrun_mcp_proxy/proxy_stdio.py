"""Stdio MCP proxy with async wrapper for long-running tools.

Wraps a downstream MCP server (stdio) and converts designated long-running
tools into an async start/poll pattern so they never hit the client's timeout.

Usage:
    longrun-mcp-proxy stdio --async-tools build_sim,test_sim \
        -- npx -y xcodebuildmcp@2.2.1 mcp
"""

from __future__ import annotations

import json
import os
import time

from fastmcp.client.transports import StdioTransport
from fastmcp.server import create_proxy
from mcp.types import TextContent

from longrun_mcp_proxy.job_store import JobStore
from longrun_mcp_proxy.middleware import AsyncWrapperMiddleware
from longrun_mcp_proxy.output_filter import filter_large_output


def build_proxy(
    downstream_cmd: list[str],
    async_tools: set[str],
    env: dict[str, str] | None = None,
):
    """Build a FastMCP proxy with async wrapper middleware.

    Args:
        downstream_cmd: Command to launch downstream MCP server.
        async_tools: Tool names to wrap in async start/poll pattern.
        env: Environment variables for downstream process.
             Defaults to current process environment.
    """
    transport = StdioTransport(
        command=downstream_cmd[0],
        args=downstream_cmd[1:],
        env=env or dict(os.environ),
    )

    store = JobStore()

    proxy = create_proxy(transport, name="longrun-mcp-proxy")
    proxy.add_middleware(AsyncWrapperMiddleware(async_tools, store))

    _register_job_tools(proxy, store)

    return proxy


def _register_job_tools(proxy, store: JobStore) -> None:
    """Register check_job and cancel_job tools on the proxy."""

    @proxy.tool(name="check_job")
    def check_job(job_id: str) -> str:
        """Check the status of an async job. Returns the result when complete."""
        job = store.get(job_id)
        if not job:
            return json.dumps({"status": "unknown", "error": f"No job with id: {job_id}"})
        if job.status == "running":
            elapsed = time.time() - job.created_at
            return json.dumps({
                "status": "running",
                "tool": job.tool_name,
                "elapsed_sec": round(elapsed, 1),
            })
        if job.status == "failed":
            error = filter_large_output(job.error) if job.error else "unknown error"
            return json.dumps({"status": "failed", "error": error})
        if job.result and job.result.content:
            texts = [c.text for c in job.result.content if isinstance(c, TextContent)]
            combined = "\n".join(texts)
            return filter_large_output(combined)
        return json.dumps({"status": "completed", "result": None})

    @proxy.tool(name="cancel_job")
    def cancel_job(job_id: str) -> str:
        """Cancel a running async job."""
        job = store.get(job_id)
        if not job:
            return json.dumps({"status": "unknown", "error": f"No job with id: {job_id}"})
        if job.status != "running":
            return json.dumps({"status": job.status, "message": "Job is not running"})
        if job._task and not job._task.done():
            job._task.cancel()
        job.status = "failed"
        job.error = "Cancelled by user"
        job.completed_at = time.time()
        return json.dumps({"status": "cancelled"})
