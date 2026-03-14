"""Filter large build output to keep only diagnostic lines."""

from __future__ import annotations

import re

MAX_RESULT_CHARS = 30_000

_DEFAULT_DIAG_RE = re.compile(
    r"(?:error:|warning:|note:|\bfailure\b|\bBUILD )", re.IGNORECASE
)


def filter_large_output(
    text: str,
    max_chars: int = MAX_RESULT_CHARS,
    pattern: re.Pattern | None = None,
) -> str:
    """Extract only diagnostic lines from large output.

    If the output is small enough, return as-is. For large outputs,
    keep only lines matching the diagnostic pattern.

    Args:
        text: The raw output text.
        max_chars: Maximum characters to return.
        pattern: Regex pattern for diagnostic lines. Defaults to
                 error:/warning:/note:/failure/BUILD.
    """
    if len(text) <= max_chars:
        return text

    diag_re = pattern or _DEFAULT_DIAG_RE
    lines = text.split("\n")
    filtered = [ln for ln in lines if diag_re.search(ln)]

    if not filtered:
        return text[: max_chars // 2] + "\n...\n" + text[-max_chars // 2 :]

    result = "\n".join(filtered)
    if len(result) <= max_chars:
        return result

    # Deduplicate identical messages, then truncate
    seen: set[str] = set()
    unique: list[str] = []
    for ln in filtered:
        if ln not in seen:
            seen.add(ln)
            unique.append(ln)
    result = "\n".join(unique)
    if len(result) > max_chars:
        result = result[:max_chars] + f"\n... (truncated, {len(text)} total chars)"
    return result
