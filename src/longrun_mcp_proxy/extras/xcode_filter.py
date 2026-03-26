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


def _group_repeated_issues(entries: list[dict]) -> tuple[list[dict], bool]:
    """Group repeated issues by message across all entries.

    Issues (warnings, linker errors, etc.) often repeat across many entries
    with the same message but different paths. Instead of 25 entries each
    saying "Linker command failed", produce a summary with count + locations.

    Issues that appear only in a single entry are left in place.
    Returns (new_entries, changed).
    """
    from collections import defaultdict

    # Count how many entries each message appears in
    msg_entry_count: dict[str, int] = defaultdict(int)
    for entry in entries:
        seen_in_entry: set[str] = set()
        for issue in entry.get("emittedIssues", []):
            msg = issue.get("message", "")
            if msg not in seen_in_entry:
                msg_entry_count[msg] += 1
                seen_in_entry.add(msg)

    # Messages that appear in 2+ entries are "repeated"
    repeated_msgs = {msg for msg, cnt in msg_entry_count.items() if cnt >= 2}
    if not repeated_msgs:
        return entries, False

    # Separate: keep unique issues in their entries, collect repeated ones
    kept_entries = []
    repeated_issues: list[tuple[dict, dict]] = []  # (issue, entry)

    for entry in entries:
        issues = entry.get("emittedIssues", [])
        unique = [i for i in issues if i.get("message", "") not in repeated_msgs]
        repeated = [i for i in issues if i.get("message", "") in repeated_msgs]

        for r in repeated:
            repeated_issues.append((r, entry))

        if unique:
            kept_entries.append({**entry, "emittedIssues": unique})

    # Group repeated issues by message + severity
    groups: dict[str, dict] = {}  # key → {severity, locations}
    for issue, entry in repeated_issues:
        msg = issue.get("message", "")
        severity = issue.get("severity", "")
        key = f"{msg}||{severity}"

        if key not in groups:
            groups[key] = {"message": msg, "severity": severity, "locations": []}

        location: dict = {}
        if issue.get("path"):
            location["path"] = issue["path"]
        if issue.get("line"):
            location["line"] = issue["line"]
        if location:
            groups[key]["locations"].append(location)

    # Build grouped issues sorted by count desc
    grouped_issues = []
    for group in sorted(groups.values(), key=lambda g: -len(g["locations"])):
        item: dict = {
            "message": group["message"],
            "severity": group["severity"],
        }
        count = max(len(group["locations"]), msg_entry_count.get(group["message"], 1))
        if count > 1:
            item["count"] = count
        locs = group["locations"]
        if locs:
            item["locations"] = locs[:3]
        grouped_issues.append(item)

    kept_entries.append({
        "buildTask": "Repeated issues (grouped by message)",
        "emittedIssues": grouped_issues,
    })

    return kept_entries, True


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

    # Step 2: group repeated issues across entries by message
    entries, grouped = _group_repeated_issues(entries)
    if grouped:
        data["buildLogEntries"] = entries
        changed = True

    # Step 3: dedup issues within each entry
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
