"""Classify downstream tool results to detect hidden failures.

Xcode MCP's `mcpbridge` sometimes returns a successful-looking response
(no isError flag, populated `counts`/`results`) even when the underlying
action failed — e.g. when a test run is cancelled because the build
didn't compile, mcpbridge re-emits the prior run's stale xcresult
summary. The only ground-truth signal lives in `fullConsoleLogsPath`.

This module inspects the raw result text and maps it to a proxy-level
status the agent can trust, so agents don't waste cycles assuming tests
ran cleanly when they never did.
"""

from __future__ import annotations

import json

BUILD_FAILED_MARKER = "Testing cancelled because the build failed"
# XCResultKit raises `.incompleteMultipartMessage` when the proxy/native MCP
# reads the .xcresult bundle while xcodebuild is still flushing it. The
# surfaced wrapper message is stable across recent Xcodes.
INCOMPLETE_BUNDLE_MARKERS = (
    "result bundle could not be opened",
    "failed to finish writing the result bundle",
    "Incomplete multipart message",
)
_LOG_READ_LIMIT = 16_384


def classify_result(result_text: str) -> tuple[str, str | None]:
    """Return ``(status, error_message)`` for a downstream tool result.

    - ``("completed", None)``: looks like a normal successful result
    - ``("failed", <msg>)``: downstream wrapped an internal error payload
    - ``("compilation_issues", <msg>)``: tests didn't actually run because
      the build failed upstream; structured test counts are stale
    - ``("transient_error", <msg>)``: downstream hit a race/flake (e.g.
      xcresult bundle read mid-flush); the original action likely
      succeeded but the proxy can't read the result — agent should retry
    """
    if not isinstance(result_text, str) or not result_text:
        return "completed", None

    try:
        parsed = json.loads(result_text)
    except (json.JSONDecodeError, ValueError):
        return "completed", None
    if not isinstance(parsed, dict):
        return "completed", None

    if parsed.get("type") == "error":
        data = str(parsed.get("data", "Unknown error"))
        if _is_incomplete_bundle_error(data):
            return "transient_error", data
        return "failed", data

    log_path = parsed.get("fullConsoleLogsPath")
    if isinstance(log_path, str) and log_path:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(_LOG_READ_LIMIT)
        except OSError:
            content = ""
        if BUILD_FAILED_MARKER in content:
            return "compilation_issues", _extract_build_error(content)

    return "completed", None


def _is_incomplete_bundle_error(data: str) -> bool:
    """True if the downstream error looks like an xcresult read race."""
    lowered = data.lower()
    return any(marker.lower() in lowered for marker in INCOMPLETE_BUNDLE_MARKERS)


def _extract_build_error(console_log: str) -> str:
    """Pull the most useful error line out of the xcodebuild console log."""
    for raw_line in console_log.splitlines():
        line = raw_line.strip()
        if line.lower().startswith("error:"):
            return line[len("error:") :].strip() or BUILD_FAILED_MARKER
    return BUILD_FAILED_MARKER
