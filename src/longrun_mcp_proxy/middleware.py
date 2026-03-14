"""Async wrapper middleware for long-running MCP tools."""

from __future__ import annotations

import asyncio

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams

from longrun_mcp_proxy.job_store import JobStore


class AsyncWrapperMiddleware(Middleware):
    """Intercept designated tools and run them in background tasks."""

    def __init__(self, async_tools: set[str], store: JobStore) -> None:
        self._async_tools = async_tools
        self._store = store

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name = context.message.name
        if tool_name not in self._async_tools:
            return await call_next(context)

        job = self._store.create(tool_name)

        async def _run() -> None:
            try:
                result = await call_next(context)
                job.result = result
                job.status = "completed"
            except Exception as exc:
                job.error = str(exc)
                job.status = "failed"
            job.completed_at = __import__("time").time()

        job._task = asyncio.create_task(_run())

        return ToolResult(
            content=f"Job started: {job.id}\n"
            f"Tool: {tool_name}\n"
            f'Poll with check_job(job_id="{job.id}") to get the result.',
        )
