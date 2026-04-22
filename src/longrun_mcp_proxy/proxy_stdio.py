"""Stdio MCP proxy with async wrapper for long-running tools.

Wraps a downstream MCP server (stdio) and converts designated long-running
tools into an async start/poll pattern so they never hit the client's timeout.

Uses manual proxy approach (not create_proxy) to preserve full error content
from downstream — FastMCP's create_proxy truncates error responses into a
single-line ToolError, losing compilation errors and build details.

Usage:
    longrun-mcp-proxy stdio --async-tools build_sim,test_sim \
        -- npx -y xcodebuildmcp@2.2.1 mcp
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
from mcp.types import TextContent

from longrun_mcp_proxy.job_store import JobStore
from longrun_mcp_proxy.output_filter import filter_large_output

logger = logging.getLogger("longrun-mcp-proxy")


def _extract_result_text(result) -> str:
    """Extract text from a CallToolResult or ToolResult."""
    if hasattr(result, "content") and result.content:
        texts = []
        for c in result.content:
            if hasattr(c, "text"):
                texts.append(c.text)
        return "\n".join(texts)
    return str(result)


def build_proxy(
    downstream_cmd: list[str],
    async_tools: set[str],
    env: dict[str, str] | None = None,
):
    """Build a FastMCP proxy stub (sync, no downstream connection).

    Returns a proxy with only check_job/cancel_job registered.
    Call `connect_and_register()` to connect to downstream and
    register all tools before running.

    Args:
        downstream_cmd: Command to launch downstream MCP server.
        async_tools: Tool names to wrap in async start/poll pattern.
        env: Environment variables for downstream process.
             Defaults to current process environment.
    """
    proxy = FastMCP("longrun-mcp-proxy")
    store = JobStore()

    proxy._downstream_cmd = downstream_cmd
    proxy._downstream_env = env or dict(os.environ)
    proxy._downstream_client: Client | None = None
    proxy._async_tools = async_tools
    proxy._store = store

    _register_job_tools(proxy, store)

    return proxy


async def connect_and_register(proxy) -> None:
    """Connect to downstream MCP server and register all tools."""
    transport = StdioTransport(
        command=proxy._downstream_cmd[0],
        args=proxy._downstream_cmd[1:],
        env=proxy._downstream_env,
    )
    proxy._downstream_client = Client(transport)
    await proxy._downstream_client.__aenter__()
    tools = await proxy._downstream_client.list_tools()

    logger.info("Connected to downstream (%d tools)", len(tools))

    # Auto-detect async tools if none were explicitly specified
    if not proxy._async_tools:
        from longrun_mcp_proxy.extras.xcode_defaults import KNOWN_ASYNC_TOOLS

        discovered = {t.name for t in tools}
        auto = discovered & KNOWN_ASYNC_TOOLS
        if auto:
            proxy._async_tools = auto
            logger.info("Auto-detected async tools: %s", ", ".join(sorted(auto)))

    # Auto-detect retry tools
    from longrun_mcp_proxy.extras.xcode_defaults import KNOWN_RETRY_TOOLS

    discovered = {t.name for t in tools}
    proxy._retry_tools = {
        name: delay
        for name, delay in KNOWN_RETRY_TOOLS.items()
        if name in discovered
    }
    if proxy._retry_tools:
        logger.info(
            "Auto-detected retry tools: %s",
            ", ".join(f"{n} ({d}s)" for n, d in sorted(proxy._retry_tools.items())),
        )

    # Auto-detect filter tools (Xcode build log dedup)
    from longrun_mcp_proxy.extras.xcode_filter import KNOWN_FILTER_TOOLS

    proxy._filter_tools = discovered & KNOWN_FILTER_TOOLS
    if proxy._filter_tools:
        logger.info("Auto-detected filter tools: %s", ", ".join(sorted(proxy._filter_tools)))

    for tool_def in tools:
        tool_name = tool_def.name
        tool_desc = tool_def.description or ""
        input_schema = tool_def.inputSchema or {}

        if tool_name in proxy._async_tools:
            _register_async_tool(
                proxy, proxy._store, tool_name, tool_desc, input_schema
            )
        else:
            _register_passthrough_tool(
                proxy, tool_name, tool_desc, input_schema
            )

    # Register extra tools (e.g. Xcode scheme management via JXA)
    # when downstream looks like native Xcode MCP.
    _register_extras(proxy, discovered)


def _register_extras(proxy, discovered_tool_names: set[str]) -> None:
    """Register extra tools based on what downstream exposes."""
    from longrun_mcp_proxy.extras.xcode_defaults import XCODE_NATIVE_ASYNC_TOOLS

    # Only add scheme tools when downstream is native Xcode MCP
    if not (discovered_tool_names & XCODE_NATIVE_ASYNC_TOOLS):
        return

    from longrun_mcp_proxy.extras.xcode_schemes import EXTRA_TOOLS

    for tool in EXTRA_TOOLS:
        if tool["name"] in discovered_tool_names:
            logger.debug("Skipping extra tool %s — already in downstream", tool["name"])
            continue
        handler = tool["handler"]
        _register_dynamic_tool(
            proxy, tool["name"], tool["description"], tool["inputSchema"], handler
        )
    logger.info(
        "Registered extra tools: %s",
        ", ".join(t["name"] for t in EXTRA_TOOLS if t["name"] not in discovered_tool_names),
    )


async def _call_downstream(proxy, name: str, arguments: dict) -> str:
    """Call downstream tool and return full text result.

    Uses send_request() directly to bypass both FastMCP's ToolError truncation
    AND MCP SDK's client-side outputSchema validation. The proxy is a transport
    layer — downstream servers (e.g. Xcode MCP) may return structuredContent
    that violates their own outputSchema (missing required fields like 'line').
    Blocking that here would lose valid build results.
    """
    session = proxy._downstream_client.session
    raw = await session.send_request(
        mcp_types.ClientRequest(
            mcp_types.CallToolRequest(
                params=mcp_types.CallToolRequestParams(
                    name=name, arguments=arguments
                ),
            )
        ),
        mcp_types.CallToolResult,
    )

    texts = []
    for c in (raw.content or []):
        if isinstance(c, TextContent):
            texts.append(c.text)

    combined = "\n".join(texts)

    if raw.isError:
        return filter_large_output(combined) if combined else "Unknown error"
    return combined


def _register_passthrough_tool(proxy, name, description, input_schema):
    async def _handler(**kwargs):
        try:
            result = await _call_downstream(proxy, name, kwargs)
            if name in getattr(proxy, "_filter_tools", set()):
                from longrun_mcp_proxy.extras.xcode_filter import dedup_build_log
                result = dedup_build_log(result)
            return result
        except Exception as e:
            return json.dumps({"error": str(e)})

    _register_dynamic_tool(proxy, name, description, input_schema, _handler)


def _register_async_tool(proxy, store, name, description, input_schema):
    async def _async_handler(**kwargs):
        job = store.create(name)

        async def _run():
            try:
                result_text = await _call_downstream(proxy, name, kwargs)
                # Retry tool if it's in the retry list (e.g. RenderPreview)
                retry_delay = getattr(proxy, "_retry_tools", {}).get(name)
                if retry_delay and result_text and "error" not in result_text.lower():
                    logger.info(
                        "Retry tool %s after %.1fs delay (cache warmup)",
                        name,
                        retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                    result_text = await _call_downstream(proxy, name, kwargs)
                job.result_text = result_text
                job.status = "completed"
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

    _register_dynamic_tool(
        proxy, name, f"[ASYNC] {description}", input_schema, _async_handler
    )


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
        # Restore original param names for downstream
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
        result_text = None
        if hasattr(job, "result_text") and job.result_text:
            result_text = filter_large_output(job.result_text)
        elapsed = round(time.time() - job.created_at, 1) if job.completed_at else None
        # Unwrap JSON strings to avoid double-encoding
        result_value = result_text
        if isinstance(result_text, str):
            try:
                result_value = json.loads(result_text)
            except (json.JSONDecodeError, ValueError):
                pass
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
            return json.dumps({"status": "unknown", "error": f"No job with id: {job_id}"})
        if job.status != "running":
            return json.dumps({"status": job.status, "message": "Job is not running"})
        if job._task and not job._task.done():
            job._task.cancel()
        job.status = "failed"
        job.error = "Cancelled by user"
        job.completed_at = time.time()
        return json.dumps({"status": "cancelled"})
