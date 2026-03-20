# LongRunMCPProxy

MCP proxy that wraps downstream MCP servers and converts long-running tools into an async start/poll pattern — so they never hit the client's timeout (e.g. Cursor's 60-second limit).

## Problem

AI coding agents (Cursor, Claude Code, VS Code Copilot) have built-in timeouts for MCP tool calls. Operations like Xcode builds or test runs can take minutes, causing the agent to drop the connection and lose results.

## Solution

LongRunMCPProxy sits between the AI agent and the MCP server. It:

1. Discovers downstream tools on startup
2. Auto-detects known long-running tools (or uses an explicit list)
3. Wraps them in an async pattern: `tool()` → returns `job_id` instantly, agent polls `check_job(job_id)` for the result
4. Passes all other tools through unchanged

## Installation

```bash
# Install globally (recommended — instant startup)
uv tool install "git+https://github.com/maximtart/LongRunMCPProxy.git@v1.1.0"

# Or run without installing
uvx --from "git+https://github.com/maximtart/LongRunMCPProxy.git@v1.1.0" longrun-mcp-proxy --help
```

### Updating

```bash
uv tool install "git+https://github.com/maximtart/LongRunMCPProxy.git@vX.Y.Z"
```

## Modes

### stdio (recommended)

For most MCP servers. Communicates with the AI agent via stdin/stdout.

```bash
longrun-mcp-proxy stdio -- xcrun mcpbridge
longrun-mcp-proxy stdio -- npx -y xcodebuildmcp@latest mcp
```

### persistent

Starts an SSE server on a local port. Use when the downstream server requires `outputSchema` or when multiple clients need to connect.

```bash
longrun-mcp-proxy persistent --port 8421 -- xcrun mcpbridge
```

## Auto-detection (v1.1.0+)

When `--async-tools` is not specified, the proxy automatically detects known long-running tools from the downstream server:

| Tool | Source |
|------|--------|
| `BuildProject` | Xcode native MCP |
| `RunAllTests` | Xcode native MCP |
| `RunSomeTests` | Xcode native MCP |
| `RenderPreview` | Xcode native MCP |
| `ExecuteSnippet` | Xcode native MCP |
| `build_sim` | xcodebuildmcp |
| `build_run_sim` | xcodebuildmcp |
| `test_sim` | xcodebuildmcp |
| `clean` | xcodebuildmcp |

You can still override with `--async-tools` if needed:

```bash
longrun-mcp-proxy stdio --async-tools BuildProject,RunAllTests -- xcrun mcpbridge
```

## Configuration

### Claude Code (`.mcp.json` in project root)

```json
{
  "mcpServers": {
    "xcode": {
      "command": "longrun-mcp-proxy",
      "args": ["stdio", "--", "xcrun", "mcpbridge"]
    },
    "xcode-build": {
      "command": "longrun-mcp-proxy",
      "args": ["stdio", "--", "npx", "-y", "xcodebuildmcp@latest", "mcp"]
    }
  }
}
```

### VS Code (`.vscode/mcp.json`)

```json
{
  "servers": {
    "xcode": {
      "type": "stdio",
      "command": "longrun-mcp-proxy",
      "args": ["stdio", "--", "xcrun", "mcpbridge"]
    },
    "xcode-build": {
      "type": "stdio",
      "command": "longrun-mcp-proxy",
      "args": ["stdio", "--", "npx", "-y", "xcodebuildmcp@latest", "mcp"]
    }
  }
}
```

### Cursor (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "xcode": {
      "command": "longrun-mcp-proxy",
      "args": ["stdio", "--", "xcrun", "mcpbridge"]
    },
    "xcode-build": {
      "command": "longrun-mcp-proxy",
      "args": ["stdio", "--", "npx", "-y", "xcodebuildmcp@latest", "mcp"]
    }
  }
}
```

## How it works

For async-wrapped tools, the agent sees:

```
1. Agent calls BuildProject(...)
2. Proxy returns: {"job_id": "abc123", "status": "running"}
3. Agent calls check_job(job_id="abc123")
4. Proxy returns: {"status": "running", "elapsed_sec": 12.5}
   ... agent keeps polling ...
5. Proxy returns: {"status": "completed", "result": "Build succeeded."}
```

Two extra tools are added automatically:
- `check_job(job_id)` — poll for result
- `cancel_job(job_id)` — cancel a running job

## Persistent mode extras

```bash
# Set Xcode MCP permission defaults (skip approval dialogs)
longrun-mcp-proxy persistent --xcode-defaults --port 8421 -- xcrun mcpbridge

# Auto-approve Xcode MCP permission dialogs via AppleScript
longrun-mcp-proxy persistent --auto-approve --port 8421 -- xcrun mcpbridge
```

## Options

| Flag | Mode | Description |
|------|------|-------------|
| `--async-tools TOOLS` | both | Comma-separated tool names to wrap (overrides auto-detect) |
| `-v, --verbose` | both | Enable debug logging |
| `--port PORT` | persistent | SSE server port (default: 8421) |
| `--host HOST` | persistent | SSE server host (default: 127.0.0.1) |
| `--name NAME` | persistent | Proxy server name |
| `--xcode-defaults` | persistent | Set Xcode permission defaults |
| `--auto-approve` | persistent | Auto-approve Xcode dialogs |

## Requirements

- Python >= 3.11
- FastMCP >= 2.0.0
