"""An in-memory stand-in for the Jobs service on port 8643."""

from __future__ import annotations

import httpx

from terminus.jobs_client import JobsService


class FakeJobsService:
    def __init__(self, rows: list[dict] | None = None, key: str = "hermes-key") -> None:
        self.rows = rows or []
        self.key = key
        self.updated_at = "2026-09-25T09:30:00.000Z"
        self.refresh_fails = False
        self.calls: list[str] = []
        self.transport = httpx.MockTransport(self.handle)

    def client(self) -> JobsService:
        return JobsService("http://box:8643", self.key, transport=self.transport)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"error": "unauthorized"})
        if request.url.path == "/jobs/refresh" and request.method == "POST":
            if self.refresh_fails:
                return httpx.Response(503, json={"error": "gmail unavailable"})
        elif not (request.url.path == "/jobs" and request.method == "GET"):
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=self.rows, headers={"X-Updated-At": self.updated_at})
