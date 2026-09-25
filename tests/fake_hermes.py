"""A stand-in Hermes gateway: /health plus a streaming /v1/chat/completions."""

from __future__ import annotations

import json

import httpx


class FakeHermes:
    def __init__(self, key: str = "hermes-key", reply: str = "Sure — start with **Find a job**.",
                 replies: list[str] | None = None) -> None:
        self.key = key
        self.reply = reply
        self.replies = list(replies or [])  # one per request, then falls back to `reply`
        self.online = True
        self.tool_events = True
        self.requests: list[dict] = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if not self.online:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"error": {"message": "invalid key"}})
        body = json.loads(request.content)
        self.requests.append(body)
        reply = self.replies.pop(0) if self.replies else self.reply
        chunks = [": keepalive\n\n"]
        if self.tool_events:
            chunks.append('event: hermes.tool.progress\ndata: {"tool": "terminal", "status": "running"}\n\n')
        for word in reply.split(" "):
            delta = {"choices": [{"delta": {"content": word + " "}, "index": 0}]}
            chunks.append(f"data: {json.dumps(delta)}\n\n")
        chunks.append("data: [DONE]\n\n")
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content="".join(chunks).encode())
