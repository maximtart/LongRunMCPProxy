"""Recover test results from an xcresult bundle after a transient read failure.

When mcpbridge hits the xcresult-incomplete race (Info.plist not yet written),
the proxy detects a `transient_error` status. Instead of asking the agent to
retry (which re-runs the tests), the proxy reads the xcresult bundle directly
via `xcrun xcresulttool` once Xcode finishes writing it.

Usage:
    success, data = await recover_from_xcresult()
    if success:
        job.result_text = json.dumps(data)
        job.status = "completed"
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import subprocess
import time


_DERIVED_DATA = os.path.expanduser("~/Library/Developer/Xcode/DerivedData")
_BUNDLE_GLOB = os.path.join(_DERIVED_DATA, "*/Logs/Test/*.xcresult")


def _find_most_recent_bundle(max_age_seconds: int = 300) -> str | None:
    """Return path to the most recently modified xcresult, or None."""
    bundles = glob.glob(_BUNDLE_GLOB)
    if not bundles:
        return None
    cutoff = time.time() - max_age_seconds
    recent = [b for b in bundles if os.path.getmtime(b) >= cutoff]
    return max(recent, key=os.path.getmtime) if recent else None


def _is_bundle_complete(bundle_path: str) -> bool:
    """True when the xcresult has been fully written (Info.plist exists)."""
    return os.path.exists(os.path.join(bundle_path, "Info.plist"))


def _run_xcresulttool(subcmd: list[str], bundle_path: str) -> dict | None:
    """Run xcresulttool and return parsed JSON, or None on failure."""
    cmd = ["xcrun", "xcresulttool", "get"] + subcmd + ["--path", bundle_path]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def _flatten_test_nodes(nodes: list, results: list) -> None:
    """Recursively collect leaf Test Case nodes into results list."""
    for node in nodes:
        if node.get("nodeType") == "Test Case":
            identifier = node.get("nodeIdentifier", "")
            raw_result = node.get("result", "")
            state = "Passed" if raw_result == "Passed" else "Failed"
            error_messages: list[str] = []
            for child in node.get("children", []):
                if child.get("nodeType") in ("Issue", "Failure"):
                    msg = child.get("name") or child.get("message") or ""
                    if msg:
                        error_messages.append(msg)
            results.append({
                "identifier": identifier,
                "state": state,
                "errorMessages": error_messages,
                "targetName": "",
                "displayName": node.get("name", identifier),
            })
        else:
            _flatten_test_nodes(node.get("children", []), results)


def _build_response(summary: dict, tests_data: dict | None) -> dict:
    """Convert xcresulttool output into mcpbridge-compatible result dict."""
    passed = summary.get("passedTests", 0)
    failed = summary.get("failedTests", 0)
    skipped = summary.get("skippedTests", 0)
    expected_failures = summary.get("expectedFailures", 0)
    total = summary.get("totalTestCount", passed + failed + skipped)

    results: list[dict] = []
    if tests_data:
        for node in tests_data.get("testNodes", []):
            _flatten_test_nodes(node.get("children", [node]), results)

    # Extract scheme name from summary title ("Test - SchemeName" pattern)
    title: str = summary.get("title", "")
    scheme_name = title.replace("Test - ", "") if title.startswith("Test - ") else title

    summary_str = (
        f"{total} tests: {passed} passed, {failed} failed, "
        f"{skipped} skipped, {expected_failures} expected failures, 0 not run"
    )

    return {
        "counts": {
            "expectedFailures": expected_failures,
            "failed": failed,
            "notRun": 0,
            "passed": passed,
            "skipped": skipped,
            "total": total,
        },
        "results": results[:100],
        "schemeName": scheme_name,
        "summary": summary_str,
        "totalResults": len(results),
        "truncated": len(results) > 100,
        "_recoveredFromXcresult": True,
    }


async def recover_from_xcresult(
    max_age_seconds: int = 300,
    timeout: int = 30,
    poll_interval: float = 2.0,
) -> tuple[bool, dict | str]:
    """Wait for the most recent xcresult bundle to finish writing, then read it.

    Returns ``(True, result_dict)`` on success,
            ``(False, error_string)`` if recovery failed.
    """
    bundle = _find_most_recent_bundle(max_age_seconds)
    if bundle is None:
        return False, "No recent xcresult bundle found in DerivedData"

    # Poll until Info.plist appears (Xcode finishes writing)
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if _is_bundle_complete(bundle):
            break
        await asyncio.sleep(poll_interval)
    else:
        return False, (
            f"xcresult bundle still incomplete after {timeout}s: "
            f"{os.path.basename(bundle)}"
        )

    loop = asyncio.get_event_loop()
    summary = await loop.run_in_executor(
        None, lambda: _run_xcresulttool(["test-results", "summary"], bundle)
    )
    if summary is None:
        return False, f"xcresulttool failed to read summary from {os.path.basename(bundle)}"

    tests_data = await loop.run_in_executor(
        None, lambda: _run_xcresulttool(["test-results", "tests"], bundle)
    )

    return True, _build_response(summary, tests_data)
