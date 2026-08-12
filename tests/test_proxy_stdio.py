"""Tests for stdio proxy builder (check_job / cancel_job tools)."""

from __future__ import annotations

import json

import pytest
from mcp.types import TextContent

from anyio import ClosedResourceError

from longrun_mcp_proxy.job_store import JobStore
from longrun_mcp_proxy import proxy_stdio
from longrun_mcp_proxy.proxy_stdio import (
    _call_downstream,
    _register_dynamic_tool,
    build_proxy,
    describe_error,
)


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
        store = proxy._store

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


class TestDownstreamRecovery:
    """A dead downstream must be restarted, not reported as an empty error.

    `xcrun mcp-server deny` stops the Xcode MCP service and takes `mcpbridge`
    with it. The proxy survives, so every later call used to fail with
    `{"error": ""}` — no reconnect, no diagnosable message.
    """

    def test_describe_error_never_empty(self):
        assert describe_error(ClosedResourceError()) == "ClosedResourceError"
        assert describe_error(RuntimeError("boom")) == "boom"

    @pytest.mark.asyncio
    async def test_reconnects_once_then_retries(self, monkeypatch):
        proxy = build_proxy(["echo", "dummy"], set())
        calls = {"send": 0, "reconnect": 0}

        async def fake_send(_proxy, name, arguments):
            calls["send"] += 1
            if calls["send"] == 1:
                raise ClosedResourceError()

            class _Result:
                isError = False
                content = [TextContent(type="text", text="recovered")]

            return _Result()

        async def fake_reconnect(_proxy):
            calls["reconnect"] += 1

        monkeypatch.setattr(proxy_stdio, "_send_downstream", fake_send)
        monkeypatch.setattr(proxy_stdio, "_reconnect_downstream", fake_reconnect)

        assert await _call_downstream(proxy, "BuildProject", {}) == "recovered"
        assert calls == {"send": 2, "reconnect": 1}

    @pytest.mark.asyncio
    async def test_second_failure_propagates(self, monkeypatch):
        proxy = build_proxy(["echo", "dummy"], set())

        async def always_dead(_proxy, name, arguments):
            raise ClosedResourceError()

        async def fake_reconnect(_proxy):
            return None

        monkeypatch.setattr(proxy_stdio, "_send_downstream", always_dead)
        monkeypatch.setattr(proxy_stdio, "_reconnect_downstream", fake_reconnect)

        with pytest.raises(ClosedResourceError):
            await _call_downstream(proxy, "BuildProject", {})


class TestInputSchemaPreservation:
    """Downstream tool schemas must survive registration without flattening.

    FastMCP's signature inference would collapse `array`/`object` into `list`/`dict`,
    stripping nested item schemas (e.g. RunSomeTests.tests requires
    `{targetName, testIdentifier}` per item — agents cannot guess this).
    """

    @pytest.mark.asyncio
    async def test_nested_array_item_schema_preserved(self):
        proxy = build_proxy(["echo", "dummy"], set())

        raw_schema = {
            "type": "object",
            "properties": {
                "tabIdentifier": {"type": "string"},
                "tests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "targetName": {"type": "string"},
                            "testIdentifier": {"type": "string"},
                        },
                        "required": ["targetName", "testIdentifier"],
                    },
                },
            },
            "required": ["tabIdentifier", "tests"],
        }

        async def _handler(**kwargs):
            return json.dumps(kwargs)

        _register_dynamic_tool(
            proxy, "RunSomeTests", "Run tests", raw_schema, _handler
        )

        tool = await proxy.get_tool("RunSomeTests")
        assert tool.parameters == raw_schema
        # Sanity: nested item properties present (the bug flattened them to {})
        assert tool.parameters["properties"]["tests"]["items"]["properties"][
            "testIdentifier"
        ]["type"] == "string"

    @pytest.mark.asyncio
    async def test_nested_object_property_schema_preserved(self):
        proxy = build_proxy(["echo", "dummy"], set())

        raw_schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "timeout": {"type": "integer"},
                        "retries": {"type": "integer"},
                    },
                    "required": ["timeout"],
                }
            },
            "required": ["config"],
        }

        async def _handler(**kwargs):
            return json.dumps(kwargs)

        _register_dynamic_tool(
            proxy, "Configure", "Apply config", raw_schema, _handler
        )

        tool = await proxy.get_tool("Configure")
        assert tool.parameters == raw_schema
        assert (
            tool.parameters["properties"]["config"]["properties"]["timeout"]["type"]
            == "integer"
        )
