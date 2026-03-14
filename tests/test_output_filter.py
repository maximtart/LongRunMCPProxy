"""Tests for output filter."""

from __future__ import annotations

from longrun_mcp_proxy.output_filter import filter_large_output


class TestOutputFilter:
    def test_small_text_passthrough(self):
        text = "Build succeeded"
        assert filter_large_output(text) == text

    def test_large_text_filtered(self):
        # Build a large output with some diagnostic lines
        lines = [f"Compiling file{i}.swift" for i in range(1000)]
        lines.insert(500, "error: undefined symbol 'foo'")
        lines.insert(700, "warning: unused variable 'bar'")
        text = "\n".join(lines)
        result = filter_large_output(text, max_chars=1000)
        assert "error: undefined symbol" in result
        assert "warning: unused variable" in result
        assert len(result) <= len(text)

    def test_large_text_no_diagnostics(self):
        text = "x" * 100_000
        result = filter_large_output(text, max_chars=1000)
        assert "..." in result
        assert len(result) <= 1100  # some overhead for ellipsis

    def test_dedup_identical_messages(self):
        lines = ["error: same error"] * 1000
        text = "\n".join(lines)
        result = filter_large_output(text, max_chars=500)
        assert result.count("error: same error") == 1
