"""CLI entry point for longrun-mcp-proxy.

Subcommands:
    stdio      — Stdio proxy (FastMCP create_proxy + middleware).
                 For servers WITHOUT outputSchema (e.g. XcodeBuildMCP).
    persistent — Persistent SSE proxy with manual tool registration.
                 For servers WITH outputSchema (e.g. native Xcode MCP).

Usage:
    longrun-mcp-proxy stdio --async-tools build_sim,test_sim \
        -- npx -y xcodebuildmcp@latest mcp

    longrun-mcp-proxy persistent --async-tools BuildProject,RunAllTests \
        --port 8421 -- xcrun mcpbridge
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="longrun-mcp-proxy",
        description="MCP proxy with async wrapper for long-running tools",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    sub = parser.add_subparsers(dest="mode", required=True)

    # --- stdio ---
    stdio_p = sub.add_parser(
        "stdio",
        help="Stdio proxy (for servers without outputSchema)",
    )
    stdio_p.add_argument(
        "--async-tools",
        default="",
        help="Comma-separated tool names to wrap in async start/poll pattern",
    )
    stdio_p.add_argument(
        "command", nargs=argparse.REMAINDER, help="Downstream MCP server command"
    )

    # --- persistent ---
    pers_p = sub.add_parser(
        "persistent",
        help="Persistent SSE proxy (for servers with outputSchema)",
    )
    pers_p.add_argument(
        "--async-tools",
        default="",
        help="Comma-separated tool names to wrap in async start/poll pattern",
    )
    pers_p.add_argument(
        "--port", type=int, default=8421, help="SSE server port (default: 8421)"
    )
    pers_p.add_argument(
        "--host", default="127.0.0.1", help="SSE server host (default: 127.0.0.1)"
    )
    pers_p.add_argument(
        "--name",
        default="longrun-mcp-proxy",
        help="Proxy server name (default: longrun-mcp-proxy)",
    )
    pers_p.add_argument(
        "--xcode-defaults",
        action="store_true",
        help="Set Xcode MCP permission defaults before starting",
    )
    pers_p.add_argument(
        "--auto-approve",
        action="store_true",
        help="Start AppleScript auto-approver for Xcode MCP dialogs",
    )
    pers_p.add_argument(
        "command", nargs=argparse.REMAINDER, help="Downstream MCP server command"
    )

    args = parser.parse_args(argv)

    # Strip leading '--' from command
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]

    if not args.command:
        parser.error("No downstream command specified")

    return args


def _run_stdio(args: argparse.Namespace) -> None:
    """Run stdio proxy."""
    from longrun_mcp_proxy.proxy_stdio import build_proxy

    async_tools = {t.strip() for t in args.async_tools.split(",") if t.strip()}

    proxy = build_proxy(args.command, async_tools)

    asyncio.run(proxy.run_async(transport="stdio"))


def _run_persistent(args: argparse.Namespace) -> None:
    """Run persistent SSE proxy."""
    from longrun_mcp_proxy.proxy_persistent import (
        start_persistent_proxy,
        stop_persistent_proxy,
    )

    async_tools = {t.strip() for t in args.async_tools.split(",") if t.strip()}

    if args.xcode_defaults:
        from longrun_mcp_proxy.extras.xcode_defaults import set_xcode_mcp_defaults

        set_xcode_mcp_defaults()

    approver_proc = None
    if args.auto_approve:
        from longrun_mcp_proxy.extras.xcode_approver import start_auto_approver

        approver_proc = start_auto_approver()

    async def _main() -> None:
        task = await start_persistent_proxy(
            command=args.command,
            async_tools=async_tools,
            port=args.port,
            host=args.host,
            name=args.name,
        )
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            await stop_persistent_proxy(task)

    try:
        asyncio.run(_main())
    finally:
        if approver_proc:
            from longrun_mcp_proxy.extras.xcode_approver import stop_auto_approver

            stop_auto_approver()


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.mode == "stdio":
        _run_stdio(args)
    elif args.mode == "persistent":
        _run_persistent(args)
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
