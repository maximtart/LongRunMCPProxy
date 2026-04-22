"""Tests for stdio proxy builder (check_job / cancel_job tools)."""

from __future__ import annotations

import json

import pytest

from longrun_mcp_proxy.job_store import JobStore
from longrun_mcp_proxy.proxy_stdio import _register_dynamic_tool, build_proxy


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
