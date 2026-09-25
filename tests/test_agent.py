from datetime import datetime, time, timedelta

import pytest

from conftest import DAY
from fake_hermes import FakeHermes
from terminus.agent_client import HermesClient, HermesError, build_context, normalize_host
from terminus.models import BlockState, Block, DayPlan, Task


@pytest.mark.parametrize("host,url", [
    ("100.64.0.10", "http://100.64.0.10:8642"),
    ("msi", "http://msi:8642"),
    ("http://10.0.0.5:9000/", "http://10.0.0.5:9000"),
    ("https://hermes.example.com/v1", "https://hermes.example.com:8642"),
    ("http://box:8642/v1", "http://box:8642"),
])
def test_normalize_host(host, url):
    assert normalize_host(host) == url


async def test_stream_tokens_and_tool_events():
    fake = FakeHermes(reply="hello there")
    client = HermesClient("box", "hermes-key", transport=fake.transport)
    events = [e async for e in client.stream_chat([{"role": "user", "content": "hi"}])]
    assert "".join(e.text for e in events if e.kind == "token").strip() == "hello there"
    assert [e.text for e in events if e.kind == "tool"] == ["terminal · running"]
    assert fake.requests[0]["stream"] is True and fake.requests[0]["model"] == "hermes-agent"


async def test_errors_are_readable():
    fake = FakeHermes()
    bad_key = HermesClient("box", "nope", transport=fake.transport)
    with pytest.raises(HermesError, match="API key"):
        [e async for e in bad_key.stream_chat([])]
    fake.online = False
    client = HermesClient("box", "hermes-key", transport=fake.transport)
    with pytest.raises(HermesError, match="Can't reach Hermes at http://box:8642"):
        [e async for e in client.stream_chat([])]
    assert await client.health() is False
    with pytest.raises(HermesError, match="No Hermes API key"):
        [e async for e in HermesClient("box", None).stream_chat([])]


def test_context_describes_the_day():
    now = datetime.combine(DAY, time(10))
    run = Task(1, "Refactor", DAY, 1, estimate=2, tags=["code"])
    old = Task(2, "Find a job", DAY, 2, overdue_from=DAY - timedelta(days=13))
    undated = Task(3, "apply for jobs", DAY, 3, no_due=True)
    plan = DayPlan(
        blocks=[Block(run, now, now + timedelta(minutes=50), BlockState.RUNNING, 0.4)],
        overflow=[], overdue=[old, undated],
    )
    ctx = build_context(plan, now=now, focus_today=timedelta(minutes=32), streak=3,
                        focus_task="Refactor")
    assert "10:00–10:50 [running] Refactor (~2 pomodoros) #code (40% done)" in ctx
    assert "Find a job (overdue since Sep 12)" in ctx
    assert "apply for jobs (no date)" in ctx
    assert "Focus logged today: 32m. Streak: 3 days. Focus timer: Refactor." in ctx


async def test_agent_failure_inside_stream_is_raised():
    """The exact shape Hermes 0.21 sends when its model backend is missing."""
    import httpx, json
    chunk = {"id": "c", "object": "chat.completion.chunk", "model": "hermes-agent",
             "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
             "error": {"message": "HTTP 404: model 'ollama/hermes3:8b' not found", "type": "agent_error"},
             "hermes": {"completed": False, "failed": True, "error": "HTTP 404: model 'ollama/hermes3:8b' not found"}}
    body = ('data: {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}\n\n'
            f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")
    transport = httpx.MockTransport(lambda r: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=body.encode()))
    client = HermesClient("box", "k", transport=transport)
    with pytest.raises(HermesError, match="hermes3:8b' not found — Hermes' own LLM isn't available"):
        [e async for e in client.stream_chat([])]
