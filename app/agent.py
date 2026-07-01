"""Wraps the local Ollama Qwen2.5-Coder model as an interactive tutor."""
from __future__ import annotations

from collections.abc import AsyncIterator

from ollama import AsyncClient

MODEL = "qwen2.5-coder:7b"

# Force the local instance regardless of any OLLAMA_HOST pointed at a remote
# peer in the user's shell environment -- this app must work fully offline.
_client = AsyncClient(host="http://127.0.0.1:11434")

SYSTEM_TEMPLATE = """You are an interactive, patient coding tutor teaching machine \
learning for finance, econometrics, and quantitative finance. You are running \
fully offline as a local 7B model, so keep explanations concise and concrete -- \
prefer short explanations plus a small runnable code exercise over long essays.

Current module: {module_title}
Current lesson: {lesson_title}

Teaching approach:
- Introduce the concept briefly (a few sentences, not a lecture).
- Give the student one small, concrete coding exercise to try, using a real \
  or synthetic finance/econometrics dataset where relevant (e.g. price series, \
  returns, factor data). Prefer numpy/pandas/statsmodels/scikit-learn.
- When the student pastes code or its output, review it directly: point out \
  bugs, misconceptions, or ways to improve it. Be specific about lines/values.
- Only move to a harder exercise once the current one basically works.
- If the student asks to move on or says they're done, summarize the key \
  takeaway in 1-2 sentences.
"""


def system_prompt(module_title: str, lesson_title: str) -> str:
    return SYSTEM_TEMPLATE.format(module_title=module_title, lesson_title=lesson_title)


async def stream_reply(
    module_title: str, lesson_title: str, history: list[dict], user_message: str
) -> AsyncIterator[str]:
    messages = [{"role": "system", "content": system_prompt(module_title, lesson_title)}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    async for chunk in await _client.chat(model=MODEL, messages=messages, stream=True):
        content = chunk.get("message", {}).get("content", "")
        if content:
            yield content


async def check_model_available() -> bool:
    try:
        models = await _client.list()
        names = [m.get("model", m.get("name", "")) for m in models.get("models", [])]
        return any(MODEL in n for n in names)
    except Exception:
        return False
