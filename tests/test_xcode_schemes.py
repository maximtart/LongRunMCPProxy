"""Tests for Xcode scheme/destination extra tools (JXA + AppleScript)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from longrun_mcp_proxy.extras.xcode_schemes import (
    EXTRA_TOOLS,
    get_run_destinations,
    get_schemes,
    set_active_scheme,
    set_run_destination,
)
from longrun_mcp_proxy.proxy_stdio import _register_extras, build_proxy


class TestJXATools:
    """Test JXA tool functions with mocked osascript."""

    @pytest.mark.asyncio
    async def test_get_schemes_success(self):
        fake_output = json.dumps([
            {"name": "MyApp", "id": "1", "isActive": True},
            {"name": "MyAppTests", "id": "2", "isActive": False},
        ])
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate.return_value = (fake_output.encode(), b"")
            mock_exec.return_value = proc

            result = await get_schemes("/path/to/Project.xcodeproj")
            parsed = json.loads(result)
            assert len(parsed) == 2
            assert parsed[0]["name"] == "MyApp"
            assert parsed[0]["isActive"] is True

    @pytest.mark.asyncio
    async def test_set_active_scheme_success(self):
        fake_output = json.dumps({"scheme": "MyApp", "message": "Active scheme set to: MyApp"})
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate.return_value = (fake_output.encode(), b"")
            mock_exec.return_value = proc

            result = await set_active_scheme("/path/to/Project.xcodeproj", "MyApp")
            parsed = json.loads(result)
            assert parsed["scheme"] == "MyApp"

    @pytest.mark.asyncio
    async def test_get_run_destinations_success(self):
        fake_output = json.dumps([
            {"name": "iPhone 17 Pro", "platform": "iphonesimulator", "architecture": "arm64", "isActive": True},
        ])
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate.return_value = (fake_output.encode(), b"")
            mock_exec.return_value = proc

            result = await get_run_destinations("/path/to/Project.xcworkspace")
            parsed = json.loads(result)
            assert len(parsed) == 1
            assert parsed[0]["name"] == "iPhone 17 Pro"
            assert parsed[0]["platform"] == "iphonesimulator"

    @pytest.mark.asyncio
    async def test_set_run_destination_success(self):
        fake_output = "Active run destination set to: iPhone 17 Pro (26.3.1)"
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate.return_value = (fake_output.encode(), b"")
            mock_exec.return_value = proc

            result = await set_run_destination(
                "/path/to/BNineBanking.xcworkspace", "iPhone 17 Pro (26.3.1)"
            )
            parsed = json.loads(result)
            assert parsed["destination"] == "iPhone 17 Pro (26.3.1)"

            # Verify it used AppleScript (no -l JavaScript flag)
            call_args = mock_exec.call_args[0]
            assert "-l" not in call_args

    @pytest.mark.asyncio
    async def test_set_run_destination_error(self):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.returncode = 1
            proc.communicate.return_value = (b"", b"Error: Can't get run destination")
            mock_exec.return_value = proc

            result = await set_run_destination(
                "/path/to/BNineBanking.xcworkspace", "Nonexistent Device"
            )
            parsed = json.loads(result)
            assert "error" in parsed

    @pytest.mark.asyncio
    async def test_jxa_error_returns_json(self):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.returncode = 1
            proc.communicate.return_value = (b"", b"Error: Workspace not found")
            mock_exec.return_value = proc

            result = await get_schemes("/path/to/Missing.xcodeproj")
            parsed = json.loads(result)
            assert "error" in parsed


class TestExtrasRegistration:
    """Test that extras are registered only for native Xcode MCP."""

    @pytest.mark.asyncio
    async def test_registers_for_native_xcode(self):
        proxy = build_proxy(["echo", "dummy"], set())
        _register_extras(proxy, {"BuildProject", "RunAllTests", "XcodeRead"})
        tools = await proxy.list_tools()
        names = {t.name for t in tools}
        assert "get_schemes" in names
        assert "set_active_scheme" in names
        assert "get_run_destinations" in names
        assert "set_run_destination" in names

    @pytest.mark.asyncio
    async def test_skips_for_non_xcode(self):
        proxy = build_proxy(["echo", "dummy"], set())
        _register_extras(proxy, {"some_other_tool", "another_tool"})
        tools = await proxy.list_tools()
        names = {t.name for t in tools}
        assert "get_schemes" not in names

    @pytest.mark.asyncio
    async def test_skips_if_downstream_has_tool(self):
        proxy = build_proxy(["echo", "dummy"], set())
        _register_extras(proxy, {"BuildProject", "get_schemes"})
        tools = await proxy.list_tools()
        names = {t.name for t in tools}
        assert "set_active_scheme" in names
        assert "get_run_destinations" in names
        assert "set_run_destination" in names


class TestToolDefinitions:
    """Verify EXTRA_TOOLS structure."""

    def test_all_tools_have_required_fields(self):
        for tool in EXTRA_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert "handler" in tool
            assert callable(tool["handler"])

    def test_tool_names_match_handlers(self):
        names = {t["name"] for t in EXTRA_TOOLS}
        assert names == {"get_schemes", "set_active_scheme", "get_run_destinations", "set_run_destination"}
