"""Tests for the result classifier that detects hidden failures."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from longrun_mcp_proxy.result_classifier import (
    BUILD_FAILED_MARKER,
    classify_result,
)


class TestClassifyResult:
    def test_empty_text_is_completed(self):
        assert classify_result("") == ("completed", None)

    def test_non_json_is_completed(self):
        assert classify_result("plain text") == ("completed", None)

    def test_non_dict_json_is_completed(self):
        assert classify_result("[1, 2, 3]") == ("completed", None)

    def test_successful_test_result_is_completed(self):
        payload = json.dumps(
            {
                "counts": {"passed": 5, "failed": 0, "total": 5},
                "summary": "5 passed",
                "fullConsoleLogsPath": "/tmp/nonexistent-log-file.txt",
            }
        )
        # Log file does not exist — classifier treats as regular completed
        assert classify_result(payload) == ("completed", None)

    def test_error_type_wrapped_result_is_failed(self):
        payload = json.dumps(
            {"type": "error", "data": "Tests are already running."}
        )
        status, err = classify_result(payload)
        assert status == "failed"
        assert err == "Tests are already running."

    def test_error_type_without_data_still_fails(self):
        payload = json.dumps({"type": "error"})
        status, err = classify_result(payload)
        assert status == "failed"
        assert err == "Unknown error"

    def test_compilation_issues_detected_from_console_log(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(
                "Test CashbackFlow\n"
                "  error:Testing cancelled because the build failed.\n"
                "\n"
                "  Launch actions\n"
                "  \n"
                "** TEST FINISHED **\n"
            )
            log_path = f.name
        try:
            payload = json.dumps(
                {
                    # mcpbridge lies here — the structured data is from a prior run
                    "counts": {"passed": 1, "failed": 0, "total": 1},
                    "summary": "1 passed",
                    "fullConsoleLogsPath": log_path,
                }
            )
            status, err = classify_result(payload)
            assert status == "compilation_issues"
            assert err == "Testing cancelled because the build failed."
        finally:
            os.unlink(log_path)

    def test_compilation_issues_falls_back_to_marker_without_error_line(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(f"Some header\n{BUILD_FAILED_MARKER}\nfooter\n")
            log_path = f.name
        try:
            payload = json.dumps(
                {"fullConsoleLogsPath": log_path, "counts": {"passed": 0}}
            )
            status, err = classify_result(payload)
            assert status == "compilation_issues"
            assert err == BUILD_FAILED_MARKER
        finally:
            os.unlink(log_path)

    def test_unreadable_log_path_falls_back_to_completed(self):
        payload = json.dumps(
            {"fullConsoleLogsPath": "/no/such/path/ever.txt", "counts": {}}
        )
        assert classify_result(payload) == ("completed", None)

    def test_log_path_not_string_is_ignored(self):
        payload = json.dumps({"fullConsoleLogsPath": 12345, "counts": {}})
        assert classify_result(payload) == ("completed", None)


class TestCheckJobSurfacesCompilationIssues:
    """check_job must switch its shape for the new status."""

    @pytest.mark.asyncio
    async def test_check_job_returns_compilation_issues_shape(self):
        from longrun_mcp_proxy.proxy_stdio import build_proxy

        proxy = build_proxy(["echo", "dummy"], {"RunSomeTests"})
        store = proxy._store

        job = store.create("RunSomeTests")
        job.status = "compilation_issues"
        job.error = "expected expression after operator"
        job.completed_at = 0.0
        job.result_text = json.dumps({"counts": {"passed": 1, "total": 1}})

        check_fn = (await proxy.get_tool("check_job")).fn
        parsed = json.loads(check_fn(job_id=job.id))

        assert parsed["status"] == "compilation_issues"
        assert parsed["tool"] == "RunSomeTests"
        assert parsed["error"] == "expected expression after operator"
        assert "hint" in parsed and "did NOT run" in parsed["hint"]
        # The stale structured result is still included for transparency,
        # so the agent can see what mcpbridge tried to claim.
        assert parsed["result"]["counts"]["passed"] == 1
