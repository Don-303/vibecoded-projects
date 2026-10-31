"""Client for a remote Hermes Agent over its OpenAI-compatible API.

Hermes (``hermes gateway`` with ``API_SERVER_ENABLED=true``) serves
``POST /v1/chat/completions`` with SSE streaming, plus an unauthenticated
``GET /health``. Besides the usual ``choices[].delta.content`` chunks, its
streams carry named events (e.g. ``hermes.tool.progress``) while the agent
works, and a ``: keepalive`` comment every 10s so long tool calls don't
time out.

Conversation history is kept client-side and sent in full each turn, with a
fresh system message describing the day, so the agent always sees the
current plan.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from terminus.actions import PROTOCOL
from terminus.models import BlockState, DayPlan, Job, Task
from terminus.widgets.timeline import fmt_duration

DEFAULT_PORT = 8642

#: Hermes loads MCP tools lazily; without this, the model sometimes decides it has no Gmail.
GMAIL_HINT = (
    "You do have read-only Gmail access through MCP tools that may not be loaded yet: call "
    "tool_search for \"gmail\" first, then use the search_emails / read_email tools it finds."
)


class HermesError(Exception):
    """Something the user should see in the chat pane."""


@dataclass
class ChatEvent:
    kind: str  # "token" | "tool"
    text: str


def normalize_host(host: str) -> str:
    """``100.64.0.10`` → ``http://100.64.0.10:8642``; full URLs pass through."""
    host = host.strip().rstrip("/")
    if "://" not in host:
        host = f"http://{host}"
    scheme, _, rest = host.partition("://")
    if ":" not in rest.split("/")[0]:
        rest = rest.replace("/", f":{DEFAULT_PORT}/", 1) if "/" in rest else f"{rest}:{DEFAULT_PORT}"
    return f"{scheme}://{rest}".removesuffix("/v1")


def _describe_tool(event: str, data: dict) -> str:
    name = data.get("tool") or data.get("tool_name") or data.get("name") or ""
    status = data.get("status") or data.get("phase") or event.rsplit(".", 1)[-1]
    label = data.get("label") or data.get("message") or data.get("preview") or ""
    parts = [p for p in (name, status, label) if p]
    return " · ".join(str(p) for p in parts) or event


def _error_text(data: dict) -> str:
    err = data.get("error") or (data.get("hermes") or {}).get("error") or "unknown error"
    text = err.get("message", str(err)) if isinstance(err, dict) else str(err)
    if "not found" in text and "model" in text:
        text += " — Hermes' own LLM isn't available on its machine (check its model/provider settings)"
    return text


class HermesClient:
    def __init__(
        self,
        host: str,
        api_key: str | None,
        *,
        model: str = "hermes-agent",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_host(host)
        self.api_key = api_key
        self.model = model
        self.transport = transport

    def _client(self, read_timeout: float) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        timeout = httpx.Timeout(connect=5, read=read_timeout, write=10, pool=5)
        return httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=timeout, transport=self.transport
        )

    async def health(self) -> bool:
        """Is the gateway up? (``/health`` needs no key.)"""
        try:
            async with self._client(read_timeout=5) as client:
                resp = await client.get("/health")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[ChatEvent]:
        """Stream a reply. Raises HermesError with a human-readable message."""
        if not self.api_key:
            raise HermesError("No Hermes API key configured — see the setup notes above.")
        body = {"model": self.model, "messages": messages, "stream": True}
        try:
            # Keepalives arrive every 10s, so a 60s read timeout means "really stuck".
            async with self._client(read_timeout=60) as client:
                async with client.stream("POST", "/v1/chat/completions", json=body) as resp:
                    if resp.status_code in (401, 403):
                        raise HermesError("Hermes rejected the API key (check API_SERVER_KEY).")
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode(errors="replace")[:200]
                        raise HermesError(f"Hermes error {resp.status_code}: {detail}")
                    if "text/event-stream" not in resp.headers.get("content-type", ""):
                        # Server answered in one piece; accept that too.
                        data = json.loads(await resp.aread())
                        if data.get("error") or (data.get("hermes") or {}).get("failed"):
                            raise HermesError(f"Hermes failed: {_error_text(data)}")
                        content = data["choices"][0]["message"].get("content") or ""
                        if content:
                            yield ChatEvent("token", content)
                        return
                    async for event in self._parse_sse(resp):
                        yield event
        except httpx.ConnectError as exc:
            raise HermesError(
                f"Can't reach Hermes at {self.base_url} — is `hermes gateway` running there, "
                "listening on API_SERVER_HOST=0.0.0.0, and is the machine online?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise HermesError(f"Hermes at {self.base_url} stopped responding (timeout).") from exc
        except httpx.HTTPError as exc:
            raise HermesError(f"Connection to Hermes failed: {exc}") from exc

    async def _parse_sse(self, resp: httpx.Response) -> AsyncIterator[ChatEvent]:
        event = ""
        async for line in resp.aiter_lines():
            if not line:
                event = ""  # blank line ends an event
                continue
            if line.startswith(":"):
                continue  # keepalive / comment
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event and event != "message":
                yield ChatEvent("tool", _describe_tool(event, data))
                continue
            for choice in data.get("choices", []):
                content = (choice.get("delta") or {}).get("content")
                if content:
                    yield ChatEvent("token", content)
            # Hermes reports agent failures (e.g. its model backend is down) inside a
            # normal chunk: finish_reason "error" plus an "error" object.
            failed = data.get("error") or (data.get("hermes") or {}).get("failed") or any(
                c.get("finish_reason") == "error" for c in data.get("choices", [])
            )
            if failed:
                raise HermesError(f"Hermes failed: {_error_text(data)}")


def build_context(
    plan: DayPlan,
    *,
    now: datetime,
    focus_today: timedelta,
    streak: int,
    focus_task: str | None,
    upcoming: list[Task] = (),
    jobs: list[Job] = (),
    actions: bool = False,
) -> str:
    """The system message: who the agent is talking to and what their day looks like.

    Tasks carry their ids (``[#12]``) so proposed changes can refer to them. With
    ``actions``, the agent is told how to propose changes (see terminus.actions).
    """
    def est(t: Task) -> str:
        return f"~{t.estimate} pomodoro{'s' if t.estimate != 1 else ''}"

    def tags(t: Task) -> str:
        return "".join(f" #{tag}" for tag in t.tags)

    lines = [
        "You are the planning assistant inside Terminus, the user's terminal day planner "
        "and focus timer. Be concise; the reply is shown in a narrow terminal pane "
        "(Markdown is rendered).",
        f"Now: {now:%A %d %B %Y, %H:%M}.",
        "",
        "Today's timeline:",
    ]
    if not plan.blocks:
        lines.append("- (nothing scheduled)")
    for b in plan.blocks:
        t = b.task
        extra = []
        if t.fixed:
            extra.append("pinned")
        if b.state is BlockState.RUNNING:
            extra.append(f"{b.progress:.0%} done")
        lines.append(
            f"- [#{t.id}] {b.start:%H:%M}–{b.end:%H:%M} [{b.state.value}] {t.title} ({est(t)})"
            + tags(t) + (f" ({', '.join(extra)})" if extra else "")
        )
    if plan.overdue:
        lines += ["", "Overdue / undated (not on the timeline):"]
        for t in plan.overdue:
            since = "no date" if t.no_due else f"overdue since {t.overdue_from:%b %d}"
            lines.append(f"- [#{t.id}] {t.title} ({since}, {est(t)}){tags(t)}")
    if plan.overflow:
        lines += ["", "Won't fit today:"] + [f"- [#{t.id}] {t.title} ({est(t)})" for t in plan.overflow]
    if upcoming:
        lines += ["", "Upcoming (next 7 days):"]
        for t in upcoming:
            at = f" {t.fixed_start:%H:%M}" if t.fixed_start else ""
            lines.append(f"- [#{t.id}] {t.day:%a %d %b}{at} {t.title} ({est(t)}){tags(t)}")
    active_jobs = [j for j in jobs if not j.closed]
    if active_jobs:
        lines += ["", "Job applications (active):"]
        for j in active_jobs:
            bits = [j.stage]
            if j.status:
                bits.append(f"status: {j.status}")
            if j.applied_on:
                bits.append(f"applied {j.applied_on:%d %b}")
            if j.next_step or j.next_date:
                when = f"{j.next_date:%a %d %b} " if j.next_date else ""
                bits.append(f"next: {when}{j.next_step}".strip())
            lines.append(f"- [J#{j.id}] {j.label} · " + " · ".join(bits))
        closed = len(jobs) - len(active_jobs)
        if closed:
            lines.append(f"({closed} closed applications not shown)")
    lines += [
        "",
        f"Focus logged today: {fmt_duration(focus_today)}. Streak: {streak} days. "
        f"Focus timer: {focus_task or 'off'}.",
        "",
        "Every task listed above already exists in the planner — never suggest adding one "
        "of them again. Only suggest times later than now.",
    ]
    if actions:
        lines += ["", PROTOCOL]
    else:
        lines.append(
            "To change the planner, the user presses keys in Terminus: n = new task, "
            "space = done, t = move an overdue task onto today, f = focus, e = edit."
        )
    return "\n".join(lines)
