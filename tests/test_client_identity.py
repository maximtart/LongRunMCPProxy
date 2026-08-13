"""Tests for the clientInfo the proxy presents to downstream servers.

Without an explicit clientInfo the MCP SDK sends Implementation(name="mcp"),
which is what downstream UIs display — Xcode 27 lists it under Connected
Agents. A single fixed name is nearly as useless there: several sessions on one
machine render as identical rows, so the name is derived per process.
"""

from __future__ import annotations

import subprocess

import pytest

from longrun_mcp_proxy.client_identity import (
    CLIENT_NAME_ENV,
    CLIENT_NOTE_ENV,
    FALLBACK_CLIENT_NAME,
    auto_client_name,
    client_info,
    resolve_client_name,
)
from longrun_mcp_proxy.proxy_stdio import build_proxy

ENV_KEYS = (
    CLIENT_NAME_ENV,
    CLIENT_NOTE_ENV,
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "TERM_PROGRAM",
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def repo(tmp_path):
    """A real work tree, so branch detection is exercised, not mocked."""
    project = tmp_path / "dev1"
    project.mkdir()
    run = lambda *a: subprocess.run(a, cwd=project, capture_output=True, check=True)
    run("git", "init", "-q", "-b", "B9-12361")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (project / "f").write_text("x")
    run("git", "add", "f")
    run("git", "commit", "-qm", "init")
    return project


class TestAutoClientName:
    def test_project_branch_host_and_session(self, monkeypatch, repo):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-vscode")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "2069486e-8045-401b-b5b4")

        assert auto_client_name() == "dev1@B9-12361 · vscode · 2069486e"

    def test_term_program_wins_over_entrypoint(self, monkeypatch, repo):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-ts")
        monkeypatch.setenv("TERM_PROGRAM", "Kepler")

        assert auto_client_name() == "dev1@B9-12361 · Kepler"

    def test_long_branch_is_trimmed(self, monkeypatch, repo):
        subprocess.run(
            ["git", "checkout", "-qb", "B9-12361-ios-screen-with-the-possibility"],
            cwd=repo,
            check=True,
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))

        assert auto_client_name() == "dev1@B9-12361-ios-screen-with…"

    def test_plain_directory_has_no_branch_suffix(self, monkeypatch, tmp_path):
        plain = tmp_path / "scratch"
        plain.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plain))

        assert auto_client_name() == "scratch"

    def test_two_sessions_on_one_project_differ(self, monkeypatch, repo):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "aaaaaaaa-1111")
        first = auto_client_name()
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "bbbbbbbb-2222")

        assert first != auto_client_name()


class TestResolveClientName:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv(CLIENT_NAME_ENV, "Codex")
        assert resolve_client_name() == "Codex"

    def test_explicit_wins_over_env(self, monkeypatch):
        monkeypatch.setenv(CLIENT_NAME_ENV, "Codex")
        assert resolve_client_name("Claude Code") == "Claude Code"

    def test_falls_back_when_detection_raises(self, monkeypatch):
        monkeypatch.setattr(
            "longrun_mcp_proxy.client_identity.auto_client_name",
            lambda: (_ for _ in ()).throw(RuntimeError("no environment")),
        )
        assert resolve_client_name() == FALLBACK_CLIENT_NAME


class TestClientInfo:
    def test_never_the_sdk_default(self):
        assert client_info().name != "mcp"

    def test_title_carries_pid_and_project(self, monkeypatch, repo):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        info = client_info()
        assert str(repo) in info.title
        assert "pid" in info.title

    def test_note_reaches_the_title(self, monkeypatch, repo):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
        monkeypatch.setenv(CLIENT_NOTE_ENV, "running the release build")
        assert "running the release build" in client_info().title

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
