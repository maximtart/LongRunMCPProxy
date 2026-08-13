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
| `--client-name NAME` | both | Name reported downstream as `clientInfo.name` (also `$LONGRUN_CLIENT_NAME`) |
| `-v, --verbose` | both | Enable debug logging |
| `--port PORT` | persistent | SSE server port (default: 8421) |
| `--host HOST` | persistent | SSE server host (default: 127.0.0.1) |
| `--name NAME` | persistent | Proxy server name |
| `--xcode-defaults` | persistent | Set Xcode permission defaults |
| `--auto-approve` | persistent | Auto-approve Xcode dialogs |

## Client identity (v1.8.0+)

Downstream servers display the `clientInfo.name` from the MCP handshake — Xcode 27
lists it in its Agent Activity panel. The MCP SDK default is `mcp`, so proxied
agents used to show up as a bare "mcp" row.

Downstream UIs show `clientInfo` — Xcode 27 lists it under Connected Agents.
Several sessions usually run at once, so the name is derived per process from
what the launching agent leaves in the environment:

```
dev1@B9-12361-ios-screen-with… · vscode · 2069486e
│    │                           │         └─ CLAUDE_CODE_SESSION_ID, first 8
│    │                           └─ TERM_PROGRAM, else CLAUDE_CODE_ENTRYPOINT
│    └─ current git branch, trimmed to 24 chars
└─ CLAUDE_PROJECT_DIR (else cwd)
```

`clientInfo.title` carries the long form — full project path, pid, and an
optional operator note explaining *why* this session is connected:

```bash
export LONGRUN_CLIENT_NOTE="release build for 4.9.0"
```

Override the name entirely with `--client-name`, or `$LONGRUN_CLIENT_NAME`.
Detection never blocks a connection: any failure falls back to
`longrun mcp proxy`.

## Signed binary for Xcode 27 (v1.9.0+)

Xcode 27's headless MCP server (`xcrun mcp-server`) grants durable trust only to
**signed** agents, and it treats the parent process of `mcpbridge` as the agent.
Run as a Python script, that parent is the shared `uv` interpreter — so the
permission record reads `signingIdentifier=python3` and covers every tool using
that interpreter. Unsigned interpreters are worse: they are pinned by file hash
and capped at 24 hours (`unsigned agents may only be approved with
--for-24-hours, not --always`).

Freezing the proxy into its own signed Mach-O narrows the grant to this proxy:

```bash
uv pip install --python .venv/bin/python pyinstaller typer
./tools/build-signed-binary.sh          # override cert with $SIGN_IDENTITY
cp dist/longrun-mcp-proxy ~/.local/bin/longrun-mcp-proxy-signed
```

Point the MCP client at that path. Keeping it out of the tracked config:

```jsonc
// .mcp.json — portable, falls back to the PATH install
"command": "${LONGRUN_MCP:-longrun-mcp-proxy}"
```

```jsonc
// ~/.claude/settings.json — machine-specific
"env": { "LONGRUN_MCP": "/Users/you/.local/bin/longrun-mcp-proxy-signed" }
```

### Re-sign on every update

**`uv tool install` does not touch the frozen binary.** Updating the proxy means
rebuilding and re-signing it, otherwise the MCP client keeps running the old
frozen copy:

```bash
git pull && ./tools/build-signed-binary.sh
cp dist/longrun-mcp-proxy ~/.local/bin/longrun-mcp-proxy-signed
```

The build script always signs, so a rebuilt binary stays trusted: Xcode matches
its record on `signingIdentifier` + `teamIdentifier` and stores no hash — a new
build with the same identity needs no new approval. Skipping the signing step
(or building without `-i longrun-mcp-proxy`) drops back to an ad-hoc signature
and the 24-hour prompt returns.

## Requirements

- Python >= 3.11
- FastMCP >= 2.0.0
