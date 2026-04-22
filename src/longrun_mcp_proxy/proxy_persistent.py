"""Persistent MCP proxy with SSE transport.

Keeps a single long-lived downstream MCP server process so the client
only needs to approve the connection once.  Exposes proxied tools via
SSE on a configurable port.

Reconnect logic: when the downstream dies, the proxy detects the broken
pipe, restarts it, and continues serving.

NOTE: FastMCP create_proxy() does NOT work with MCP servers that declare
outputSchema on their tools.  This module uses a manual proxy approach:
reads tools from downstream once, registers pass-through handlers, and
forwards call_tool to the persistent client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import typing

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.tools.tool import Tool
from mcp import types as mcp_types

from longrun_mcp_proxy.job_store import JobStore
from longrun_mcp_proxy.output_filter import filter_large_output
from longrun_mcp_proxy.result_classifier import classify_result

logger = logging.getLogger("longrun-mcp-proxy")


# ---------------------------------------------------------------------------
# Persistent downstream client with reconnect
# ---------------------------------------------------------------------------


class PersistentDownstream:
    """Manages a single persistent connection to a downstream MCP server.

    Reconnects automatically if the downstream dies.
    """

    def __init__(self, command: list[str], env: dict[str, str] | None = None) -> None:
        self._command = command
        self._env = env or dict(os.environ)
        self._client: Client | None = None
        self._lock = asyncio.Lock()
        self._tools: list = []

    async def connect(self) -> list:
        """Connect to downstream and return tool list."""
        async with self._lock:
            if self._client is not None:
                return self._tools
            self._client = self._create_client()
            await self._client.__aenter__()
            self._tools = await self._client.list_tools()
            logger.info(
                "Connected to %s (%d tools)", self._command[0], len(self._tools)
            )
            return self._tools

    async def _send_tool_request(self, name: str, arguments: dict):
        """Send tool call via send_request, bypassing outputSchema validation.

        The proxy is a transport — downstream servers (e.g. Xcode MCP) may
        return structuredContent that violates their own outputSchema.
        Blocking that here would lose valid results.
        """
        session = self._client.session
        return await session.send_request(
            mcp_types.ClientRequest(
                mcp_types.CallToolRequest(
                    params=mcp_types.CallToolRequestParams(
                        name=name, arguments=arguments
                    ),
                )
            ),
            mcp_types.CallToolResult,
        )

    async def call_tool(
        self, name: str, arguments: dict, timeout: float = 300
    ) -> object:
        """Forward a tool call to downstream, reconnecting if needed."""
        try:
            return await asyncio.wait_for(
                self._send_tool_request(name, arguments),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(
                "Downstream call failed (%s): %s — reconnecting",
                type(e).__name__,
                e,
            )
            await self._reconnect()
            return await asyncio.wait_for(
                self._send_tool_request(name, arguments),
                timeout=timeout,
            )

    async def _reconnect(self) -> None:
        async with self._lock:
            await self._close_client()
            self._client = self._create_client()
            await self._client.__aenter__()
            self._tools = await self._client.list_tools()
            logger.info(
                "Reconnected to %s (%d tools)", self._command[0], len(self._tools)
            )

    def _create_client(self) -> Client:
        transport = StdioTransport(
            command=self._command[0],
            args=self._command[1:],
            env=self._env,
        )
        return Client(transport)

    async def _close_client(self) -> None:
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None

    async def close(self) -> None:
        async with self._lock:
            await self._close_client()


# ---------------------------------------------------------------------------
# Manual proxy builder
# ---------------------------------------------------------------------------


def _extract_result_text(result) -> str:
    if hasattr(result, "content"):
        texts = []
        for c in result.content:
            if hasattr(c, "text"):
                texts.append(c.text)
        return "\n".join(texts)
    return str(result)


def build_persistent_proxy(
    downstream: PersistentDownstream,
    tools: list,
    async_tools: set[str],
    name: str = "longrun-mcp-proxy",
) -> FastMCP:
    """Build a FastMCP server that manually forwards all tools to downstream."""
    proxy = FastMCP(name)
    store = JobStore()

    # Auto-detect async tools if none were explicitly specified
    if not async_tools:
        from longrun_mcp_proxy.extras.xcode_defaults import KNOWN_ASYNC_TOOLS

        discovered = {t.name for t in tools}
        auto = discovered & KNOWN_ASYNC_TOOLS
        if auto:
            async_tools = auto
            logger.info("Auto-detected async tools: %s", ", ".join(sorted(auto)))

    # Auto-detect retry tools
    from longrun_mcp_proxy.extras.xcode_defaults import KNOWN_RETRY_TOOLS

    discovered = {t.name for t in tools}
    retry_tools = {
        name: delay
        for name, delay in KNOWN_RETRY_TOOLS.items()
        if name in discovered
    }
    if retry_tools:
        logger.info(
            "Auto-detected retry tools: %s",
            ", ".join(f"{n} ({d}s)" for n, d in sorted(retry_tools.items())),
        )
    proxy._retry_tools = retry_tools

    for tool_def in tools:
        tool_name = tool_def.name
        tool_desc = tool_def.description or ""
        input_schema = tool_def.inputSchema or {}

        if tool_name in async_tools:
            _register_async_tool(
                proxy, downstream, store, tool_name, tool_desc, input_schema
            )
        else:
            _register_passthrough_tool(
                proxy, downstream, tool_name, tool_desc, input_schema
            )

    # check_job / cancel_job
    @proxy.tool(name="check_job")
    def check_job(job_id: str) -> str:
        """Check the status of an async job. Returns the result when complete."""
        job = store.get(job_id)
        if not job:
            return json.dumps(
                {"status": "unknown", "error": f"No job with id: {job_id}"}
            )
        if job.status == "running":
            elapsed = time.time() - job.created_at
            return json.dumps(
                {
                    "status": "running",
                    "tool": job.tool_name,
                    "elapsed_sec": round(elapsed, 1),
                }
            )
        if job.status == "failed":
            error = filter_large_output(job.error) if job.error else "unknown error"
            return json.dumps({"status": "failed", "error": error})
        result_text = None
        if job.result:
            result_text = filter_large_output(_extract_result_text(job.result))
        elapsed = round(time.time() - job.created_at, 1) if job.completed_at else None
        # Unwrap JSON strings to avoid double-encoding
        result_value = result_text
        if isinstance(result_text, str):
            try:
                result_value = json.loads(result_text)
            except (json.JSONDecodeError, ValueError):
                pass
        if job.status == "compilation_issues":
            return json.dumps({
                "status": "compilation_issues",
                "tool": job.tool_name,
                "elapsed_sec": elapsed,
                "error": job.error or "Testing cancelled because the build failed.",
                "hint": (
                    "Tests did NOT run — the target failed to compile. "
                    "Fix the build first (e.g. BuildProject or XcodeListNavigatorIssues) "
                    "before trusting any test counts in result."
                ),
                "result": result_value,
            })
        if job.status == "transient_error":
            return json.dumps({
                "status": "transient_error",
                "tool": job.tool_name,
                "elapsed_sec": elapsed,
                "error": job.error or "Transient downstream error.",
                "hint": (
                    "Race while reading the xcresult bundle — the action likely "
                    "succeeded but the result bundle wasn't fully written when "
                    "Xcode read it. Wait ~3-5 seconds and retry the same tool. "
                    "Do NOT assume the action failed."
                ),
            })
        return json.dumps({
            "status": "completed",
            "tool": job.tool_name,
            "elapsed_sec": elapsed,
            "result": result_value,
        })

    @proxy.tool(name="cancel_job")
    def cancel_job(job_id: str) -> str:
        """Cancel a running async job."""
        job = store.get(job_id)
        if not job:
            return json.dumps(
                {"status": "unknown", "error": f"No job with id: {job_id}"}
            )
        if job.status != "running":
            return json.dumps(
                {"status": job.status, "message": "Job is not running"}
            )
        if job._task and not job._task.done():
            job._task.cancel()
        job.status = "failed"
        job.error = "Cancelled by user"
        job.completed_at = time.time()
        return json.dumps({"status": "cancelled"})

    return proxy


def _register_passthrough_tool(proxy, downstream, name, description, input_schema):
    async def _handler(**kwargs):
        try:
            result = await downstream.call_tool(name, kwargs)
            return _extract_result_text(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    _register_dynamic_tool(proxy, name, description, input_schema, _handler)


def _register_async_tool(proxy, downstream, store, name, description, input_schema):
    async def _async_handler(**kwargs):
        job = store.create(name)

        async def _run():
            try:
                result = await downstream.call_tool(name, kwargs)
                # Retry tool if it's in the retry list (e.g. RenderPreview)
                retry_delay = getattr(proxy, "_retry_tools", {}).get(name)
                if retry_delay:
                    result_text = _extract_result_text(result)
                    if "error" not in result_text.lower():
                        logger.info(
                            "Retry tool %s after %.1fs delay (cache warmup)",
                            name,
                            retry_delay,
                        )
                        await asyncio.sleep(retry_delay)
                        result = await downstream.call_tool(name, kwargs)
                job.result = result
                result_text = _extract_result_text(result)
                status, error_msg = classify_result(result_text)
                job.status = status
                if error_msg:
                    job.error = error_msg
            except Exception as exc:
                job.error = str(exc)
                job.status = "failed"
            job.completed_at = time.time()

        job._task = asyncio.create_task(_run())
        return (
            f"Job started: {job.id}\n"
            f"Tool: {name}\n"
            f'Poll with check_job(job_id="{job.id}") to get the result.'
        )

    _register_dynamic_tool(proxy, name, f"[ASYNC] {description}", input_schema, _async_handler)


import keyword as _keyword


def _register_dynamic_tool(proxy, name, description, input_schema, handler):
    """Register a tool with dynamic signature matching the input schema."""
    params = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    # Map param names to safe Python identifiers (e.g. "global" → "global_")
    safe_names: dict[str, str] = {}
    for pname in params:
        safe = f"{pname}_" if _keyword.iskeyword(pname) else pname
        safe_names[pname] = safe

    param_annotations = {}
    for pname, pdef in params.items():
        ptype = pdef.get("type", "string")
        type_map = {
            "string": str, "integer": int, "number": float,
            "boolean": bool, "array": list, "object": dict,
        }
        py_type = type_map.get(ptype, str)
        if pname not in required:
            py_type = typing.Optional[py_type]
        param_annotations[safe_names[pname]] = py_type

    # Required params first, then optional (Python syntax requirement)
    param_strs = []
    for pname in params:
        safe = safe_names[pname]
        if pname in required:
            param_strs.append(f"{safe}: __annotations__['{safe}']")
    for pname in params:
        safe = safe_names[pname]
        if pname not in required:
            param_strs.append(f"{safe}: __annotations__['{safe}'] = None")

    # Build reverse mapping for renamed params
    renames = {safe: orig for orig, safe in safe_names.items() if orig != safe}

    func_code = f"async def {name}({', '.join(param_strs)}) -> str:\n"
    func_code += "    kwargs = {k: v for k, v in locals().items() if v is not None}\n"
    if renames:
        for safe, orig in renames.items():
            func_code += f"    if '{safe}' in kwargs: kwargs['{orig}'] = kwargs.pop('{safe}')\n"
    func_code += "    return await _handler_ref(**kwargs)\n"

    local_ns = {"__annotations__": param_annotations, "_handler_ref": handler}
    exec(func_code, local_ns)  # noqa: S102
    func = local_ns[name]
    func.__doc__ = description

    tool_obj = Tool.from_function(func, name=name, description=description)
    # Preserve the raw downstream inputSchema (nested item/property types
    # that FastMCP's signature inference would flatten to `list` / `dict`).
    tool_obj.parameters = input_schema
    proxy.add_tool(tool_obj)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

_server_task: asyncio.Task | None = None
_downstream: PersistentDownstream | None = None


async def start_persistent_proxy(
    command: list[str],
    async_tools: set[str],
    port: int = 8421,
    host: str = "127.0.0.1",
    env: dict[str, str] | None = None,
    name: str = "longrun-mcp-proxy",
) -> asyncio.Task:
    """Start persistent MCP proxy as an SSE server.

    Returns the asyncio.Task running the server.
    """
    global _server_task, _downstream

    _downstream = PersistentDownstream(command, env=env)
    tools = await _downstream.connect()

    proxy = build_persistent_proxy(_downstream, tools, async_tools, name=name)

    async def _run_server():
        try:
            logger.info("Starting persistent proxy on %s:%d", host, port)
            await proxy.run_async(
                transport="sse", host=host, port=port, show_banner=False
            )
        except Exception as e:
            logger.error("Proxy server crashed: %s", e)

    task = asyncio.create_task(_run_server())
    _server_task = task
    return task


async def stop_persistent_proxy(task: asyncio.Task | None = None) -> None:
    """Stop the persistent proxy."""
    global _server_task, _downstream
    t = task or _server_task
    if t and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    _server_task = None
    if _downstream:
        await _downstream.close()
        _downstream = None
    logger.info("Persistent proxy stopped")
