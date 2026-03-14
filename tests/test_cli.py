"""Tests for CLI argument parsing."""

from __future__ import annotations

import pytest

from longrun_mcp_proxy.cli import _parse_args


class TestCLIArgs:
    def test_stdio_basic(self):
        args = _parse_args(["stdio", "--async-tools", "build_sim,test_sim", "--", "npx", "-y", "xcodebuildmcp"])
        assert args.mode == "stdio"
        assert args.async_tools == "build_sim,test_sim"
        assert args.command == ["npx", "-y", "xcodebuildmcp"]

    def test_stdio_single_tool(self):
        args = _parse_args(["stdio", "--async-tools", "build_sim", "--", "echo", "hi"])
        assert args.async_tools == "build_sim"
        assert args.command == ["echo", "hi"]

    def test_persistent_basic(self):
        args = _parse_args(["persistent", "--async-tools", "BuildProject", "--port", "9000", "--", "xcrun", "mcpbridge"])
        assert args.mode == "persistent"
        assert args.async_tools == "BuildProject"
        assert args.port == 9000
        assert args.command == ["xcrun", "mcpbridge"]

    def test_persistent_defaults(self):
        args = _parse_args(["persistent", "--", "xcrun", "mcpbridge"])
        assert args.port == 8421
        assert args.host == "127.0.0.1"
        assert not args.xcode_defaults
        assert not args.auto_approve

    def test_persistent_xcode_flags(self):
        args = _parse_args(["persistent", "--xcode-defaults", "--auto-approve", "--", "xcrun", "mcpbridge"])
        assert args.xcode_defaults is True
        assert args.auto_approve is True

    def test_no_command_fails(self):
        with pytest.raises(SystemExit):
            _parse_args(["stdio", "--async-tools", "build_sim", "--"])

    def test_no_mode_fails(self):
        with pytest.raises(SystemExit):
            _parse_args(["--", "echo", "hi"])
