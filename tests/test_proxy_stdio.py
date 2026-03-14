"""Tests for stdio proxy builder (check_job / cancel_job tools)."""

from __future__ import annotations

import json

import pytest

from longrun_mcp_proxy.job_store import JobStore
from longrun_mcp_proxy.middleware import AsyncWrapperMiddleware
from longrun_mcp_proxy.proxy_stdio import build_proxy


def _get_store(proxy) -> JobStore:
    """Get JobStore from the AsyncWrapperMiddleware on the proxy."""
    for mw in proxy.middleware:
        if isinstance(mw, AsyncWrapperMiddleware):
            return mw._store
    raise AssertionError("AsyncWrapperMiddleware not found")


class TestProxyTools:
    @pytest.mark.asyncio
    async def test_check_job_unknown(self):
        proxy = build_proxy(["echo", "dummy"], {"nonexistent_tool"})
        check_fn = (await proxy.get_tool("check_job")).fn
        result = check_fn(job_id="no-such-id")
        parsed = json.loads(result)
        assert parsed["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_check_and_cancel_job(self):
        proxy = build_proxy(["echo", "dummy"], {"nonexistent_tool"})
        store = _get_store(proxy)

        job = store.create("build_sim")

        check_fn = (await proxy.get_tool("check_job")).fn
        cancel_fn = (await proxy.get_tool("cancel_job")).fn

        # check_job — running
        result = json.loads(check_fn(job_id=job.id))
        assert result["status"] == "running"
        assert result["tool"] == "build_sim"
        assert "elapsed_sec" in result

        # cancel_job
        result = json.loads(cancel_fn(job_id=job.id))
        assert result["status"] == "cancelled"
        assert job.status == "failed"
