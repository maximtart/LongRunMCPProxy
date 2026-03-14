"""Tests for async wrapper middleware."""

from __future__ import annotations

import asyncio

import pytest

from longrun_mcp_proxy.job_store import JobStore
from longrun_mcp_proxy.middleware import AsyncWrapperMiddleware


class _FakeContext:
    """Minimal stand-in for MiddlewareContext[CallToolRequestParams]."""

    def __init__(self, tool_name: str):
        self.message = type("Msg", (), {"name": tool_name})()


class TestAsyncWrapperMiddleware:
    @pytest.mark.asyncio
    async def test_passthrough(self):
        """Non-async tools go directly to call_next."""
        store = JobStore()
        mw = AsyncWrapperMiddleware({"build_sim"}, store)

        result_obj = object()

        async def call_next(ctx):
            return result_obj

        ctx = _FakeContext("list_simulators")
        result = await mw.on_call_tool(ctx, call_next)
        assert result is result_obj
        assert len(store.all()) == 0

    @pytest.mark.asyncio
    async def test_async_wrap_returns_job_id(self):
        """Async tools return a job_id immediately."""
        store = JobStore()
        mw = AsyncWrapperMiddleware({"build_sim"}, store)

        call_started = asyncio.Event()
        call_complete = asyncio.Event()

        async def call_next(ctx):
            call_started.set()
            await call_complete.wait()
            from fastmcp.tools.tool import ToolResult

            return ToolResult(content="build output here")

        ctx = _FakeContext("build_sim")
        result = await mw.on_call_tool(ctx, call_next)

        # Should return immediately with job info
        assert len(store.all()) == 1
        job = store.all()[0]
        assert job.status == "running"
        assert "Job started" in result.content[0].text
        assert job.id in result.content[0].text

        # Let the background task complete
        call_complete.set()
        await call_started.wait()
        await asyncio.sleep(0.05)
        assert job.status == "completed"
        assert job.result is not None

    @pytest.mark.asyncio
    async def test_async_wrap_handles_error(self):
        """If downstream raises, job is marked failed."""
        store = JobStore()
        mw = AsyncWrapperMiddleware({"build_sim"}, store)

        async def call_next(ctx):
            raise RuntimeError("xcodebuild crashed")

        ctx = _FakeContext("build_sim")
        await mw.on_call_tool(ctx, call_next)

        job = store.all()[0]
        await asyncio.sleep(0.05)
        assert job.status == "failed"
        assert "xcodebuild crashed" in job.error
