# Port XcodeMCPKit Auto-Approve to LongRunMCPProxy

## Problem

Current AppleScript auto-approver (`deploy/auto_approve_xcode_mcp.applescript`) uses `System Events` to find and click the Xcode MCP "Allow" dialog. **Does not work cross-Space** — `System Events` returns 0 windows when Xcode is on another macOS Space or user is in fullscreen.

## Verified Solution

XcodeMCPKit (`XcodePermissionDialogAutoApprover.swift`, 1005 lines) uses Swift + AXUIElement API directly (not through AppleScript System Events). **Works cross-Space** — verified 3 times on our machine:

1. Same-Space: auto-approved in <1s
2. Cross-Space (user on fullscreen): auto-approved in <1s  
3. Cross-Space (clean Xcode restart, fresh registry): auto-approved in 1s

Log confirmation: `Auto-approved the Xcode permission dialog. button=allow pid=90739`

## How XcodeMCPKit Does It

Source: `/tmp/XcodeMCPKit/Sources/ProxyFeatureXcode/XcodePermissionDialogAutoApprover.swift`

### Architecture

```
XcodePermissionDialogAutoApprover (main loop, 250ms poll)
  ├── LiveXcodePermissionDialogAXClient (AXUIElement operations)
  │     ├── runningXcodeProcessIDs()     — NSWorkspace.shared.runningApplications
  │     ├── openWindows(for: pid)        — AXUIElementCopyAttributeValue(app, kAXWindowsAttribute)
  │     ├── makeWindow(window: AXUIElement) → snapshot + defaultButton ref
  │     └── pressDefaultButton(window)   — AXUIElementPerformAction(button, kAXPressAction)
  └── XcodePermissionDialogMatcher (dialog identification)
        ├── passesStructuralChecks()     — bundleID, isModal, subrole=AXDialog/AXSystemDialog
        └── containsAssistantNameAndPID() — dialog text contains agent name + PID
```

### Key Logic

1. **Poll every 250ms**: `runMonitorLoop()` → `scanAndApprove()`
2. **Find Xcode processes**: `NSWorkspace.shared.runningApplications` filtered by `com.apple.dt.Xcode` and `com.apple.dt.ExternalViewService`
3. **Enumerate windows**: `AXUIElementCreateApplication(pid)` → `kAXWindowsAttribute`
4. **For each window, build snapshot**:
   - role (`kAXRoleAttribute`) — must be `AXWindow`
   - subrole (`kAXSubroleAttribute`) — must be `AXDialog` or `AXSystemDialog`
   - isModal (`kAXModalAttribute`) — must be true
   - defaultButton (`kAXDefaultButtonAttribute`) — must exist
   - text values — BFS traversal of children, collect all `kAXValueAttribute` strings
5. **Match dialog**: text must contain agent name AND PID from known candidates
6. **Click**: `AXUIElementPerformAction(defaultButton, kAXPressAction)` — goes through Cocoa responder chain, NOT WindowServer, which is why it works cross-Space
7. **Dedup**: fingerprint per dialog prevents double-clicks; retry after 500ms if dialog persists

### Why It Works Cross-Space

AppleScript `System Events` queries WindowServer for visible windows on current Space → returns 0 for other Spaces.

AXUIElement API talks directly to the app process via Accessibility framework (Mach IPC) → app reports its own windows regardless of Space visibility. `AXUIElementPerformAction` sends action directly to the app's Cocoa responder chain.

## Port Plan

### Approach: Swift standalone binary

Compile standalone Swift CLI that runs as subprocess (same pattern as current AppleScript subprocess). No pyobjc dependency, no framework install needed.

### Files

**New:**
- `tools/xcode-auto-approve/main.swift` — standalone Swift CLI, ~200 lines
- `tools/xcode-auto-approve/build.sh` — `swiftc main.swift -o ../../dist/xcode-auto-approve`

**Modified:**
- `src/longrun_mcp_proxy/extras/xcode_approver.py` — change subprocess command from `osascript <script>` to `dist/xcode-auto-approve <args>`

**Deleted:**
- `deploy/auto_approve_xcode_mcp.applescript` — replaced by Swift binary

### Swift CLI Design (`main.swift`)

Stripped-down version of XcodeMCPKit's 1005-line file. Remove:
- `XcodePermissionDialogAXAccessing` protocol (no need for test abstraction)
- `Dependencies` struct with injectable sleep/logger (direct calls)
- `XcodePermissionDialogWindowSnapshot` full struct (inline checks)
- `XcodePermissionDialogMatcher` as separate enum (inline matching)
- Fingerprint dedup system (simple "already clicked this dialog" set)
- Logging framework dependency (print to stdout)

Keep:
- `AXUIElementCreateApplication(pid)` → `kAXWindowsAttribute` enumeration
- `kAXSubroleAttribute` check for `AXDialog`/`AXSystemDialog`
- `kAXModalAttribute` check
- `kAXDefaultButtonAttribute` → `AXUIElementPerformAction(button, kAXPressAction)`
- BFS text collection from dialog children (to verify it's the MCP dialog, not a save/error dialog)
- `AXIsProcessTrusted()` check at startup
- 250ms poll interval
- `NSWorkspace.shared.runningApplications` for Xcode PID discovery

CLI interface:
```
xcode-auto-approve [--agent-name NAME] [--interval MS] [--verbose]
```

Stdout logging format (parseable by Python):
```
[INFO] Monitoring Xcode (PID 12345)
[INFO] Auto-approved dialog (PID 12345, button=Allow)
[WARN] Accessibility not trusted — request prompt
[ERROR] Failed to press button: <error>
```

### `xcode_approver.py` Changes

```python
# Before:
_SCRIPT_PATH = Path(__file__).parent.parent.parent.parent / "deploy" / "auto_approve_xcode_mcp.applescript"
proc = subprocess.Popen(["osascript", str(script)], ...)

# After:
_BINARY_PATH = Path(__file__).parent.parent.parent.parent / "dist" / "xcode-auto-approve"
proc = subprocess.Popen([str(binary), "--agent-name", agent_name], ...)
```

Add `agent_name` parameter to `start_auto_approver()` for dialog text matching.

### Build Integration

`build.sh`:
```bash
#!/bin/bash
cd "$(dirname "$0")"
swiftc main.swift -O -o ../../dist/xcode-auto-approve
```

`pyproject.toml` — add `dist/xcode-auto-approve` to package data.

`deploy/install.sh` (orchestra) — run build.sh during install, or pre-compile and distribute binary.

### Orchestra Changes

**`deploy/com.b9.orchestra.autoapprove.plist`** — change ProgramArguments from `osascript <script>` to `dist/xcode-auto-approve --agent-name orchestra`.

Or: remove separate autoapprove launchd plist entirely — auto-approve is embedded in proxy via `--auto-approve` flag.

### Testing

- `tests/test_xcode_approver.py` — verify `start_auto_approver()` launches binary with correct args
- Manual test: same-Space auto-approve (same as current)
- Manual test: cross-Space auto-approve (new capability)
- Manual test: Xcode restart → new dialog → auto-approve

### Risk: Binary Distribution

Swift binary is architecture-specific (arm64). Need to either:
- Compile on target machine during install (requires Xcode CLI tools)
- Ship pre-compiled binary in repo (larger repo, architecture lock-in)
- Compile in CI and attach to release

Recommendation: compile on target during `deploy/install.sh` — same machine that runs it has Xcode installed → has `swiftc`.
