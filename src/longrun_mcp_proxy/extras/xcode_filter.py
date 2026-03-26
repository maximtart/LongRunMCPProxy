"""Xcode-specific output filters for build log deduplication.

Native Xcode MCP (xcrun mcpbridge) returns build logs with cascading
duplicate issues — e.g. 49 identical "Clang dependency scanning failure"
entries differing only by input file hash. This wastes LLM context tokens.

Filters applied:
1. Dedup emittedIssues within each buildLogEntry by normalized message,
   collapsing duplicates into a single issue with a "count" field.
2. Collapse cascading Copy artifact failures (.swiftmodule, .swiftdoc,
   .abi.json, .swiftsourceinfo) into a single summary entry.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("longrun-mcp-proxy")

# Tools whose output should be filtered for build log dedup
KNOWN_FILTER_TOOLS = {"GetBuildLog"}

# Pattern to normalize hash-like suffixes in Clang/Swift messages
# e.g. "BNineCore-69f6c3dc.input:1:1:" → "BNineCore-HASH.input:1:1:"
_HASH_RE = re.compile(r"-[0-9a-f]{6,}\.input")

# Build artifact extensions — Copy tasks for these are always cascading
# failures when compilation fails, never real missing-file errors.
_CASCADING_ARTIFACT_EXTS = {".swiftmodule", ".swiftdoc", ".abi.json", ".swiftsourceinfo"}


def _normalize_message(msg: str) -> str:
    """Normalize a message for dedup comparison."""
    return _HASH_RE.sub("-HASH.input", msg)


def _is_cascading_copy(entry: dict) -> bool:
    """Check if a buildLogEntry is a cascading Copy artifact failure."""
    task = entry.get("buildTask", "")
    if not task.startswith("Copy "):
        return False
    return any(task.endswith(f" (arm64)") and ext in task
               for ext in _CASCADING_ARTIFACT_EXTS)


def _collapse_copy_failures(entries: list[dict]) -> tuple[list[dict], bool]:
    """Replace cascading Copy artifact entries with a single summary."""
    kept = []
    collapsed_names = []

    for entry in entries:
        if _is_cascading_copy(entry):
            # Extract artifact name from "Copy Foo.swiftmodule (arm64)"
            task = entry["buildTask"]
            name = task.removeprefix("Copy ").removesuffix(" (arm64)").strip()
            collapsed_names.append(name)
        else:
            kept.append(entry)

    if not collapsed_names:
        return entries, False

    kept.append({
        "buildTask": "Copy build artifacts (cascading failures)",
        "emittedIssues": [{
            "message": f"{len(collapsed_names)} build artifacts not found (expected — build failed): {', '.join(collapsed_names)}",
            "severity": "note",
        }],
    })
    return kept, True


def dedup_build_log(text: str) -> str:
    """Deduplicate and clean up a GetBuildLog JSON response.

    1. Collapse cascading Copy artifact failures into a single summary.
    2. Dedup emittedIssues within each buildLogEntry by normalized
       message+severity+path+line, adding a "count" field for duplicates.

    If parsing fails or format is unexpected, returns input unchanged.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text

    entries = data.get("buildLogEntries")
    if not isinstance(entries, list):
        return text

    changed = False

    # Step 1: collapse cascading Copy failures
    entries, collapsed = _collapse_copy_failures(entries)
    if collapsed:
        data["buildLogEntries"] = entries
        changed = True

    # Step 2: dedup issues within each entry
    for entry in entries:
        issues = entry.get("emittedIssues")
        if not isinstance(issues, list) or len(issues) <= 1:
            continue

        # Group by normalized message + severity + path + line
        groups: dict[str, list[dict]] = {}
        for issue in issues:
            group_key = (
                f"{_normalize_message(issue.get('message', ''))}"
                f"||{issue.get('severity', '')}"
                f"||{issue.get('path', '')}"
                f"||{issue.get('line', '')}"
            )
            groups.setdefault(group_key, []).append(issue)

        # Only rebuild if there are actual duplicates
        if all(len(g) == 1 for g in groups.values()):
            continue

        deduped = []
        for group in groups.values():
            representative = group[0]
            if len(group) > 1:
                representative = dict(representative)
                representative["count"] = len(group)
                changed = True
            deduped.append(representative)

        entry["emittedIssues"] = deduped

    if not changed:
        return text

    original_len = len(text)
    result = json.dumps(data)
    logger.debug(
        "Build log filtered: %d → %d chars (%.0f%% reduction)",
        original_len, len(result),
        (1 - len(result) / original_len) * 100,
    )
    return result
