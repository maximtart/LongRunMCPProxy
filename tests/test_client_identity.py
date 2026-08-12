"""Tests for the clientInfo the proxy presents to downstream servers.

Without an explicit clientInfo the MCP SDK sends Implementation(name="mcp"),
which is what downstream UIs display — Xcode 27 lists it in Agent Activity.
"""

from __future__ import annotations

import pytest

from longrun_mcp_proxy.client_identity import (
    CLIENT_NAME_ENV,
    DEFAULT_CLIENT_NAME,
    client_info,
    resolve_client_name,
)
from longrun_mcp_proxy.proxy_stdio import build_proxy


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(CLIENT_NAME_ENV, raising=False)


class TestResolveClientName:
    def test_default(self):
        assert resolve_client_name() == DEFAULT_CLIENT_NAME

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv(CLIENT_NAME_ENV, "Codex")
        assert resolve_client_name() == "Codex"

    def test_explicit_wins_over_env(self, monkeypatch):
        monkeypatch.setenv(CLIENT_NAME_ENV, "Codex")
        assert resolve_client_name("Claude Code") == "Claude Code"


class TestClientInfo:
    def test_never_the_sdk_default(self):
        assert client_info().name != "mcp"

    def test_carries_name_and_version(self):
        info = client_info("Agent X")
        assert info.name == "Agent X"
        assert info.version


class TestProxyWiring:
    def test_build_proxy_keeps_client_name(self):
        proxy = build_proxy(["echo", "dummy"], set(), client_name="Agent X")
        assert proxy._client_name == "Agent X"

    def test_build_proxy_defaults_to_none(self):
        proxy = build_proxy(["echo", "dummy"], set())
        assert proxy._client_name is None
