"""Tests for job store."""

from __future__ import annotations

import time

from longrun_mcp_proxy.job_store import JobStore


class TestJobStore:
    def test_create_and_get(self):
        store = JobStore()
        job = store.create("build_sim")
        assert job.status == "running"
        assert job.tool_name == "build_sim"
        assert store.get(job.id) is job

    def test_get_unknown(self):
        store = JobStore()
        assert store.get("nonexistent") is None

    def test_cleanup_expired(self):
        store = JobStore()
        job = store.create("build_sim")
        job.status = "completed"
        job.completed_at = time.time() - 700  # older than TTL (600s)
        assert store.get(job.id) is None

    def test_cleanup_keeps_recent(self):
        store = JobStore()
        job = store.create("build_sim")
        job.status = "completed"
        job.completed_at = time.time() - 10  # recent
        assert store.get(job.id) is job

    def test_all(self):
        store = JobStore()
        store.create("a")
        store.create("b")
        assert len(store.all()) == 2
