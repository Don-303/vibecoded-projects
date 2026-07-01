"""Wraps a local Ollama model as an interactive tutor."""
from __future__ import annotations

from collections.abc import AsyncIterator

from ollama import AsyncClient

from app import rag
from app.curriculum import Lesson

# General-purpose instruct model rather than the Coder variant: this tutor
# mostly explains concepts (econometrics, portfolio theory, etc.) and only
# secondarily reviews code, and Qwen2.5-Coder is specialized away from that
# broader reasoning/knowledge in favor of code benchmarks.
MODEL = "qwen2.5:7b-instruct"

# Qwen2.5:7b-instruct supports 32k; 8192 comfortably fits a full source
# lecture or two plus conversation history without the extra prefill cost of
# the full window on CPU-only inference.
NUM_CTX = 8192

# This machine is CPU-only (12 threads, hybrid P/E cores) with no GPU
# acceleration available to Ollama. Two changes matter a lot here:
# - KEEP_ALIVE: Ollama's default unload-after-5-min-idle causes a slow cold
#   reload (~1-2 min) on the next message. Keep it loaded for a full session.
# - NUM_THREAD: llama.cpp's default thread count under-uses this CPU (we
#   observed ~2 cores active during inference); leave 2 threads free for the
#   OS/UI rather than requesting all 12.
KEEP_ALIVE = "30m"
NUM_THREAD = 10

# Force the local instance regardless of any OLLAMA_HOST pointed at a remote
# peer in the user's shell environment -- this app must work fully offline.
_client = AsyncClient(host="http://127.0.0.1:11434")

SYSTEM_TEMPLATE = """You are an interactive, patient tutor teaching machine \
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

REFERENCE_TEMPLATE = """

Reference material (from your local lecture corpus -- cite the title in \
brackets when you draw on one, e.g. "[{example_title}]"). If none of it is \
actually relevant to the question, ignore it and answer from your own \
knowledge instead -- say so if you're doing that.

{blocks}
"""

SOURCE_LESSON_TEMPLATE = """

Primary lesson material (the actual source lecture(s) for this topic, from \
your local corpus). Walk through this WITH the student interactively rather \
than dumping it all at once: explain one section in your own words, then \
pause with a short question or small exercise checking understanding before \
moving to the next section. Ground claims, formulas, and code in what's \
below. In every reply, start by naming which source you're drawing on, \
e.g. "From [{first_source_name}]:", so the student always knows this is \
coming from real lecture material and not just your own memory. If multiple \
documents are given, work through them in order across the conversation, \
using the chat history to judge how far you've already gotten.

{blocks}
"""


def system_prompt(module_title: str, lesson_title: str, extra_block: str = "") -> str:
    prompt = SYSTEM_TEMPLATE.format(module_title=module_title, lesson_title=lesson_title)
    return prompt + extra_block


def _build_reference_block(chunks: list[rag.Chunk]) -> str:
    if not chunks:
        return ""
    blocks = "\n\n".join(f"[{c.title}]\n{c.text}" for c in chunks)
    return REFERENCE_TEMPLATE.format(example_title=chunks[0].title, blocks=blocks)


def _build_source_lesson_block(lesson: Lesson) -> str:
    if not lesson.sources:
        return ""
    parts = []
    for src in lesson.sources:
        text = rag.load_source_text(src.repo, src.file)
        if text:
            parts.append(f"=== {src.file} ({src.repo}) ===\n{text}")
    if not parts:
        return ""
    return SOURCE_LESSON_TEMPLATE.format(
        first_source_name=lesson.sources[0].file, blocks="\n\n".join(parts)
    )


async def stream_reply(lesson: Lesson, history: list[dict], user_message: str) -> AsyncIterator[str]:
    extra_block = _build_source_lesson_block(lesson)
    if not extra_block:
        retrieved = await rag.retrieve(f"{lesson.title}. {user_message}")
        extra_block = _build_reference_block(retrieved)

    messages = [
        {"role": "system", "content": system_prompt(lesson.module_title, lesson.title, extra_block)}
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    async for chunk in await _client.chat(
        model=MODEL,
        messages=messages,
        stream=True,
        options={"num_ctx": NUM_CTX, "num_thread": NUM_THREAD},
        keep_alive=KEEP_ALIVE,
    ):
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
