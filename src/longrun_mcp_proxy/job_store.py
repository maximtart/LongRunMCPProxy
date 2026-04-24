"""In-memory job store for async-wrapped tool calls."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from uuid import uuid4

JOB_TTL_SEC = 600  # clean up completed jobs after 10 min


@dataclass
class Job:
    id: str
    tool_name: str
    status: str = "running"  # running | completed | failed | compilation_issues | transient_error
    result: object | None = None          # raw downstream CallToolResult (persistent proxy)
    result_text: str | None = None        # serialised result text (stdio proxy or recovery)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    _task: asyncio.Task | None = field(default=None, repr=False)


class JobStore:
    """In-memory store for async jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, tool_name: str) -> Job:
        job = Job(id=uuid4().hex[:12], tool_name=tool_name)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        self._cleanup()
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        self._cleanup()
        return list(self._jobs.values())

    def _cleanup(self) -> None:
        now = time.time()
        expired = [
            jid
            for jid, j in self._jobs.items()
            if j.completed_at and now - j.completed_at > JOB_TTL_SEC
        ]
        for jid in expired:
            del self._jobs[jid]
